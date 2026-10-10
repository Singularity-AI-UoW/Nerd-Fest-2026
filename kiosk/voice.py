"""Push-to-talk speech-to-text with live captions (Gemini Live transcription).

Children's voices: audio only ever lives in memory. Chunks go from the mic
straight to the live session and are never saved anywhere.

Live API usage, per https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
("Manual VAD (push-to-talk)"), checked against google-genai 2.29.0 and one live
session on 10/10/2026:
  - client.aio.live.connect(model=..., config=types.LiveConnectConfig(
        response_modalities=["TEXT"],
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)),
        input_audio_transcription=types.AudioTranscriptionConfig()))
  - session.send_realtime_input(activity_start=types.ActivityStart()) when recording starts,
    session.send_realtime_input(audio=types.Blob(data=..., mime_type="audio/pcm;rate=16000"))
    for each 100 ms chunk, and
    session.send_realtime_input(activity_end=types.ActivityEnd()) when the button is let go.
  - session.receive() yields types.LiveServerMessage. It ends after each completed
    turn, so it is called again in a loop.
      server_content.interim_input_transcription.text: the live caption so far.
      server_content.input_transcription.text: the final text.
  - Seen live: with speech, after activity_end the server sends input_transcription,
    then server_content.generation_complete, then voice_activity.voice_activity_type
    == ACTIVITY_END. With silence it sends only the ACTIVITY_END signal.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator, Callable

from google import genai
from google.genai import types

from kiosk.events import Event, EventBus

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_FRAMES = 1600  # 100 ms at 16 kHz
AUDIO_MIME = f"audio/pcm;rate={SAMPLE_RATE}"

FINAL_TEXT_WAIT_S = 2.0  # how long stop() waits for the final text after the button is let go
CONNECT_TIMEOUT_S = 5.0
MIC_CLOSE_WAIT_S = 1.0
SESSION_CLOSE_WAIT_S = 2.0

LIVE_CONFIG = types.LiveConnectConfig(
    response_modalities=["TEXT"],
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
)

MicFactory = Callable[[], AsyncIterator[bytes]]


async def default_mic() -> AsyncIterator[bytes]:
    """Yield 100 ms chunks of 16 kHz mono int16 audio from the default input device.

    sounddevice calls back on its own audio thread, so each chunk is handed to the
    event loop with call_soon_threadsafe. The stream is closed when iteration stops.
    """
    import sounddevice as sd  # lazy: tests and --fake need no audio hardware

    loop = asyncio.get_running_loop()
    chunks: asyncio.Queue[bytes] = asyncio.Queue()

    def on_audio(indata, frames, time_info, status) -> None:
        with contextlib.suppress(RuntimeError):  # the loop closed while the stream stopped
            loop.call_soon_threadsafe(chunks.put_nowait, bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=CHUNK_FRAMES,
        callback=on_audio,
    )
    try:
        stream.start()
        while True:
            yield await chunks.get()
    finally:
        stream.close(ignore_errors=True)


class Voice:
    """One recording at a time: start() when the button goes down, stop() when it comes up."""

    def __init__(self, client: genai.Client, model: str, bus: EventBus,
                 mic_factory: MicFactory = default_mic):
        self._client = client
        self._model = model
        self._bus = bus
        self._mic_factory = mic_factory
        self._mic_task: asyncio.Task | None = None
        self._session_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Open the mic at once and the live session in the background. Never raises."""
        if self._session_task is not None:
            await self.stop()
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._done = asyncio.Event()  # final text in, or failed
        self._closing = asyncio.Event()
        self._audio_ended = False
        self._failed = False
        self._finals: list[str] = []
        self._partial = ""
        self._mic_task = asyncio.create_task(self._pump_mic(), name="voice-mic")
        self._session_task = asyncio.create_task(self._run_session(), name="voice-session")

    async def stop(self) -> str:
        """Release the mic, wait up to FINAL_TEXT_WAIT_S for the final text, close the
        session and return the stripped transcript ("" if none or on error). Never raises."""
        mic_task, session_task = self._mic_task, self._session_task
        self._mic_task = self._session_task = None
        if session_task is None or mic_task is None:
            return ""
        try:
            mic_task.cancel()
            await asyncio.wait({mic_task}, timeout=MIC_CLOSE_WAIT_S)
            self._audio.put_nowait(None)  # end of audio: the session sends activity_end
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._done.wait(), FINAL_TEXT_WAIT_S)
        finally:
            self._closing.set()
            _, pending = await asyncio.wait({session_task}, timeout=SESSION_CLOSE_WAIT_S)
            if pending:
                log.warning("live session did not close in time; cancelled")
                session_task.cancel()
        if self._failed:
            return ""
        return (" ".join(self._finals) or self._partial).strip()

    # ----- internals (all on the event loop thread) -----

    def _fail(self, exc: BaseException) -> None:
        if self._failed or self._done.is_set():
            log.debug("voice error after the final text: %s", type(exc).__name__)
            return
        self._failed = True
        # Type only: messages could carry request details.
        log.warning("speech-to-text failed: %s", type(exc).__name__)
        self._bus.put(Event("stt_error"))
        self._done.set()

    async def _pump_mic(self) -> None:
        try:
            async with contextlib.aclosing(self._mic_factory()) as mic:
                async for chunk in mic:
                    self._audio.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - no mic: report it, keep the show going
            self._fail(exc)

    async def _run_session(self) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                session = await asyncio.wait_for(
                    stack.enter_async_context(
                        self._client.aio.live.connect(model=self._model, config=LIVE_CONFIG)
                    ),
                    CONNECT_TIMEOUT_S,
                )
                receiver = asyncio.create_task(self._receive(session), name="voice-receive")
                try:
                    await self._send_audio(session)
                    await self._closing.wait()
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    async def _send_audio(self, session) -> None:
        await session.send_realtime_input(activity_start=types.ActivityStart())
        while (chunk := await self._audio.get()) is not None:
            await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type=AUDIO_MIME))
        self._audio_ended = True  # set first: the final text can arrive during the send
        await session.send_realtime_input(activity_end=types.ActivityEnd())

    async def _receive(self, session) -> None:
        try:
            while not self._done.is_set():
                async for msg in session.receive():
                    self._handle(msg)
                    if self._done.is_set():
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def _handle(self, msg: types.LiveServerMessage) -> None:
        sc = msg.server_content
        if sc is not None:
            if sc.interim_input_transcription and sc.interim_input_transcription.text:
                self._partial = sc.interim_input_transcription.text
                self._bus.put(Event("caption", text=self._partial))
            if sc.input_transcription and sc.input_transcription.text:
                self._finals.append(sc.input_transcription.text.strip())
                if self._audio_ended:
                    self._done.set()
            if self._audio_ended and (sc.generation_complete or sc.turn_complete):
                self._done.set()
        va = msg.voice_activity
        if (self._audio_ended and va is not None
                and va.voice_activity_type == types.VoiceActivityType.ACTIVITY_END
                and not self._partial):
            self._done.set()  # silence: no text is coming

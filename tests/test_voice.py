"""Voice tests. A fake live client stands in for google-genai; no network, no microphone.

The fake session answers the way the real gemini-3.5-transcribe-live session did
on 10/10/2026: interim captions while audio arrives, then, after activity_end,
one input_transcription, generation_complete and a voice_activity ACTIVITY_END.
"""
import asyncio
import contextlib
import os
import re
import subprocess
import sys
import threading
import types as pytypes
from pathlib import Path

import pytest
from google.genai import types

from kiosk import voice as voice_mod
from kiosk.events import EventBus
from kiosk.voice import Voice, default_mic

MODEL = "gemini-3.5-transcribe-live"
CHUNK = bytes(3200)  # 100 ms of 16 kHz mono int16 silence


def interim(text):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        interim_input_transcription=types.Transcription(text=text)))


def final(text):
    return types.LiveServerMessage(server_content=types.LiveServerContent(
        input_transcription=types.Transcription(text=text)))


def generation_complete():
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(generation_complete=True))


def activity_end_signal():
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(),
        voice_activity=types.VoiceActivity(
            voice_activity_type=types.VoiceActivityType.ACTIVITY_END))


class FakeSession:
    """Records what Voice sends. `on_audio` maps the n-th audio chunk (1-based) to
    messages to emit; `on_end` is emitted after activity_end."""

    def __init__(self, on_audio=None, on_end=(), fail_receive=None, fail_send=None):
        self.sent: list[dict] = []
        self._out: asyncio.Queue = asyncio.Queue()
        self._on_audio = on_audio or {}
        self._on_end = list(on_end)
        self._fail_receive = fail_receive
        self._fail_send = fail_send
        self._chunks = 0

    async def send_realtime_input(self, **kwargs):
        if self._fail_send is not None:
            raise self._fail_send
        self.sent.append(kwargs)
        if "audio" in kwargs:
            self._chunks += 1
            for m in self._on_audio.get(self._chunks, ()):
                self._out.put_nowait(m)
        if "activity_end" in kwargs:
            for m in self._on_end:
                self._out.put_nowait(m)

    async def receive(self):
        if self._fail_receive is not None:
            raise self._fail_receive
        while True:
            yield await self._out.get()

    def kinds(self):
        return [next(iter(k)) for k in self.sent]


class FakeLive:
    def __init__(self, session, fail_connect=None, hang_on_close=False):
        self.session = session
        self.fail_connect = fail_connect
        self.hang_on_close = hang_on_close
        self.connects: list[tuple] = []
        self.closed = False

    @contextlib.asynccontextmanager
    async def connect(self, *, model, config):
        self.connects.append((model, config))
        if self.fail_connect is not None:
            raise self.fail_connect
        try:
            yield self.session
        finally:
            if self.hang_on_close:
                await asyncio.Event().wait()  # a close handshake that never ends
            self.closed = True


class FakeClient:
    def __init__(self, live):
        self.aio = pytypes.SimpleNamespace(live=live)


class FakeMic:
    """mic_factory for tests: yields the given chunks, then waits like a live mic."""

    def __init__(self, chunks=(CHUNK,) * 3, keep_open=True):
        self.chunks = list(chunks)
        self.keep_open = keep_open
        self.opened = False
        self.released = False

    async def __call__(self):
        self.opened = True
        try:
            for c in self.chunks:
                yield c
                await asyncio.sleep(0)
            if self.keep_open:
                await asyncio.Event().wait()
        finally:
            self.released = True


def drain(bus: EventBus):
    out = []
    while not bus._q.empty():
        out.append(bus._q.get_nowait())
    return out


async def settle(session, n_audio):
    """Wait until the fake session has received n_audio audio chunks."""
    for _ in range(200):
        if session.kinds().count("audio") >= n_audio:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {session.kinds()} sent")


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(voice_mod, "FINAL_TEXT_WAIT_S", 0.3)
    monkeypatch.setattr(voice_mod, "SESSION_CLOSE_WAIT_S", 0.3)
    monkeypatch.setattr(voice_mod, "MIC_CLOSE_WAIT_S", 0.3)


async def test_partials_posted_as_caption_events(fast):
    session = FakeSession(on_audio={1: [interim("a dra")], 2: [interim("a dragon")]})
    bus, mic = EventBus(), FakeMic()
    v = Voice(FakeClient(FakeLive(session)), MODEL, bus, mic_factory=mic)
    await v.start()
    await settle(session, 3)
    await asyncio.sleep(0.05)
    events = drain(bus)
    assert [(e.kind, e.text) for e in events] == [("caption", "a dra"), ("caption", "a dragon")]
    await v.stop()


async def test_stop_returns_final_text(fast):
    session = FakeSession(
        on_audio={1: [interim("a dragon")]},
        on_end=[final("  A dragon riding a bike. "), generation_complete(), activity_end_signal()],
    )
    live, bus, mic = FakeLive(session), EventBus(), FakeMic()
    v = Voice(FakeClient(live), MODEL, bus, mic_factory=mic)
    await v.start()
    await settle(session, 3)
    assert await v.stop() == "A dragon riding a bike."

    # The documented push-to-talk sequence, with 16 kHz PCM audio.
    kinds = session.kinds()
    assert kinds[0] == "activity_start" and kinds[-1] == "activity_end"
    assert kinds[1:-1] == ["audio"] * 3
    blobs = [s["audio"] for s in session.sent if "audio" in s]
    assert all(b.mime_type == "audio/pcm;rate=16000" and b.data == CHUNK for b in blobs)
    model, config = live.connects[0]
    assert model == MODEL
    assert config.input_audio_transcription is not None
    assert config.realtime_input_config.automatic_activity_detection.disabled is True
    # Mic released, session closed, no error.
    assert mic.released and live.closed
    assert all(e.kind != "stt_error" for e in drain(bus))


async def test_audio_sent_before_connect_is_kept(fast):
    """The mic opens at once; chunks captured while connecting are sent after."""
    session = FakeSession(on_end=[final("hello")])
    gate = asyncio.Event()

    class SlowLive(FakeLive):
        @contextlib.asynccontextmanager
        async def connect(self, *, model, config):
            await gate.wait()
            async with super().connect(model=model, config=config) as s:
                yield s

    live, mic = SlowLive(session), FakeMic(keep_open=False)
    v = Voice(FakeClient(live), MODEL, EventBus(), mic_factory=mic)
    await v.start()
    await asyncio.sleep(0.05)
    assert mic.opened
    gate.set()
    await settle(session, 3)
    assert await v.stop() == "hello"


async def test_finished_mic_is_not_an_error(fast):
    session = FakeSession(on_end=[final("hi")])
    bus, mic = EventBus(), FakeMic(keep_open=False)
    v = Voice(FakeClient(FakeLive(session)), MODEL, bus, mic_factory=mic)
    await v.start()
    await settle(session, 3)
    assert await v.stop() == "hi"
    assert drain(bus) == []


async def test_silence_ends_without_waiting_for_the_full_final_wait(monkeypatch, fast):
    monkeypatch.setattr(voice_mod, "FINAL_TEXT_WAIT_S", 5.0)
    session = FakeSession(on_end=[activity_end_signal()])
    bus = EventBus()
    v = Voice(FakeClient(FakeLive(session)), MODEL, bus, mic_factory=FakeMic())
    await v.start()
    await settle(session, 3)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    assert await v.stop() == ""
    assert loop.time() - t0 < 1.0
    assert drain(bus) == []


async def test_no_final_falls_back_to_last_partial(fast):
    session = FakeSession(on_audio={2: [interim("a cat in space")]})
    v = Voice(FakeClient(FakeLive(session)), MODEL, EventBus(), mic_factory=FakeMic())
    await v.start()
    await settle(session, 3)
    await asyncio.sleep(0.05)
    assert await v.stop() == "a cat in space"


@pytest.mark.parametrize("where", ["connect", "receive", "send"])
async def test_session_error_posts_stt_error_and_returns_empty(fast, where):
    boom = RuntimeError("boom")
    session = FakeSession(
        on_audio={1: [interim("a dra")]}, on_end=[final("a dragon")],
        fail_receive=boom if where == "receive" else None,
        fail_send=boom if where == "send" else None,
    )
    live = FakeLive(session, fail_connect=boom if where == "connect" else None)
    bus, mic = EventBus(), FakeMic()
    v = Voice(FakeClient(live), MODEL, bus, mic_factory=mic)
    await v.start()
    ev = await bus.next(1.0)
    while ev is not None and ev.kind == "caption":
        ev = await bus.next(1.0)
    assert ev is not None and ev.kind == "stt_error"
    assert await v.stop() == ""
    assert mic.released
    if where != "connect":
        assert live.closed
    assert [e.kind for e in drain(bus)] == []  # stt_error is posted once


async def test_mic_error_posts_stt_error(fast):
    async def broken_mic():
        raise OSError("no input device")
        yield b""  # pragma: no cover - makes this an async generator

    session = FakeSession()
    live, bus = FakeLive(session), EventBus()
    v = Voice(FakeClient(live), MODEL, bus, mic_factory=broken_mic)
    await v.start()
    ev = await bus.next(1.0)
    assert ev is not None and ev.kind == "stt_error"
    assert await v.stop() == ""
    assert live.closed


async def test_stop_is_bounded_when_session_hangs(fast):
    session = FakeSession()  # never sends a final transcript
    live, mic = FakeLive(session, hang_on_close=True), FakeMic()
    v = Voice(FakeClient(live), MODEL, EventBus(), mic_factory=mic)
    await v.start()
    await settle(session, 3)
    assert await asyncio.wait_for(v.stop(), 2.0) == ""
    assert mic.released


async def test_stop_without_start_returns_empty():
    v = Voice(FakeClient(FakeLive(FakeSession())), MODEL, EventBus(), mic_factory=FakeMic())
    assert await v.stop() == ""


async def test_voice_can_record_again_after_stop(fast):
    s1 = FakeSession(on_end=[final("one")])
    s2 = FakeSession(on_end=[final("two")])
    live = FakeLive(s1)
    v = Voice(FakeClient(live), MODEL, EventBus(), mic_factory=FakeMic())
    await v.start()
    await settle(s1, 3)
    assert await v.stop() == "one"
    live.session = s2
    await v.start()
    await settle(s2, 3)
    assert await v.stop() == "two"


async def test_audio_never_written_to_disk(fast, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session = FakeSession(on_audio={1: [interim("a dra")]}, on_end=[final("a dragon")])
    v = Voice(FakeClient(FakeLive(session)), MODEL, EventBus(), mic_factory=FakeMic())
    await v.start()
    await settle(session, 3)
    assert await v.stop() == "a dragon"
    assert list(tmp_path.iterdir()) == []

    source = Path(voice_mod.__file__).read_text(encoding="utf-8")
    assert not re.search(r"open\([^)]*['\"]\w*w\w*b", source)  # no open(..., 'wb')
    assert "wave" not in source
    assert "soundfile" not in source and ".write(" not in source


def test_sounddevice_imported_lazily():
    code = "import sys, kiosk.voice; assert 'sounddevice' not in sys.modules"
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)


async def test_default_mic_hands_chunks_over_from_the_audio_thread(monkeypatch):
    """default_mic opens a 16 kHz mono int16 RawInputStream with 100 ms blocks; the
    callback runs on another thread and the stream is closed when iteration stops."""
    streams = []

    class FakeStream:
        def __init__(self, **kw):
            self.kw = kw
            self.closed = False
            self._thread = None
            streams.append(self)

        def start(self):
            def run():
                for i in range(3):
                    self.kw["callback"](bytes([i]) * 3200, 1600, None, None)
            self._thread = threading.Thread(target=run)
            self._thread.start()

        def close(self, ignore_errors=True):
            self._thread.join()
            self.closed = True

    monkeypatch.setitem(sys.modules, "sounddevice",
                        pytypes.SimpleNamespace(RawInputStream=FakeStream))
    got = []
    async with contextlib.aclosing(default_mic()) as mic:
        async for chunk in mic:
            got.append(chunk)
            if len(got) == 3:
                break
    assert got == [bytes([i]) * 3200 for i in range(3)]
    (stream,) = streams
    assert stream.kw["samplerate"] == 16000 and stream.kw["channels"] == 1
    assert stream.kw["dtype"] == "int16" and stream.kw["blocksize"] == 1600
    assert stream.closed


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("KIOSK_LIVE"), reason="set KIOSK_LIVE=1 to call the real API")
async def test_live_silence_smoke():
    """Real gemini-3.5-transcribe-live session: 1 s of silence through the real code path."""
    from google import genai

    from kiosk.config import REPO_ROOT, load_config, load_secrets

    cfg = load_config(REPO_ROOT / "config.toml")
    client = genai.Client(api_key=load_secrets().google_api_key)
    bus = EventBus()
    mic = FakeMic(chunks=[CHUNK] * 10, keep_open=False)  # 10 x 100 ms
    v = Voice(client, cfg.models.stt, bus, mic_factory=mic)
    await v.start()
    await asyncio.sleep(1.5)
    text = await asyncio.wait_for(v.stop(), 10.0)
    assert isinstance(text, str)
    assert all(e.kind != "stt_error" for e in drain(bus))
    assert mic.released

"""Preflight checklist for the organiser's /check page: is everything ready for the stall?

Every check returns {"ok": bool, "detail": str}. The detail is written for the organiser,
includes latency in ms where it helps, and never holds a key, a token or an exception
message (messages can carry URLs, and URLs can carry keys): failures name the exception
type, or the HTTP status, only. Each check is bounded by its own timeout.

Safe to run with the stall live: the mic check never saves audio, the button check
pauses the show while it waits (and puts the previous pause state back), the speech
check sends silence, and the gallery check deletes its test picture (id "testtest")
even when the upload or the fetch-back failed.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
import types as pytypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
import numpy as np
from PIL import Image

from kiosk.config import Config, Secrets
from kiosk.events import EventBus
from kiosk.health import Check
from kiosk.voice import CHUNK_FRAMES, MicFactory, Voice, default_mic

log = logging.getLogger(__name__)

# (name, label): the order they are listed on the page.
PREFLIGHT: list[tuple[str, str]] = [
    ("mic", "Microphone level"),
    ("button", "Arcade button press"),
    ("internet", "Internet"),
    ("google", "Google image model"),
    ("stt", "Speech-to-text connect"),
    ("openrouter", "OpenRouter LLM (1 short call)"),
    ("fallback_llm", "Qwen fallback over Tailscale (1 short call)"),
    ("gallery", "Gallery upload and delete"),
]

TEST_IMAGE_ID = "testtest"
MIC_LISTEN_S = 1.5
MIC_MIN_RMS = 0.005  # of full scale; a muted or unplugged mic reads about 0
BUTTON_WAIT_S = 10.0
STT_CONNECT_WAIT_S = 6.0
STT_SILENCE_S = 0.5
STEP_TIMEOUT_S = 8.0  # one gallery request
BUTTON_SETTLE_S = 0.05  # on top of two show polls, so an idle show has noticed the pause
LLM_PROMPT = {
    "instructions": "Reply with the single word: ready",
    "input": "Say ready.",
}

# Whole-check limits. The button and LLM limits leave room for their own waits.
TIMEOUT_S: dict[str, float] = {
    "mic": MIC_LISTEN_S + 5,
    "button": BUTTON_WAIT_S + 5,
    "internet": 8,
    "google": 8,
    "stt": STT_CONNECT_WAIT_S + STT_SILENCE_S + 8,
    "openrouter": 25,
    "fallback_llm": 25,
    "gallery": 3 * STEP_TIMEOUT_S + 4,
}


@dataclass
class PreflightDeps:
    cfg: Config
    secrets: Secrets
    checks: dict[str, Check]  # the health checks: "openrouter", "google", "fallback_llm", ...
    llm: Any                  # LlmClient: probe()
    http: httpx.AsyncClient
    uploader: Any             # Uploader: delete()
    google: Any               # google.genai.Client, for the speech-to-text session
    bus: EventBus             # the show's bus, where button presses arrive
    show: Any                 # Show: paused, state, poll_s, set_paused()
    mic_factory: MicFactory = default_mic
    button_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


Result = dict  # {"ok": bool, "detail": str}


def _ok(detail: str) -> Result:
    return {"ok": True, "detail": detail}


def _fail(detail: str) -> Result:
    return {"ok": False, "detail": detail}


def _ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


def _why(exc: BaseException) -> str:
    """The exception type, plus the HTTP status if it has one. Never its message."""
    status = getattr(exc, "status_code", None)
    suffix = f", HTTP {status}" if isinstance(status, int) else ""
    return f"{type(exc).__name__}{suffix}"


async def run_check(name: str, deps: PreflightDeps) -> Result:
    check = _CHECKS.get(name)
    if check is None:
        return _fail(f"Unknown check: {name}")
    limit = TIMEOUT_S[name]
    try:
        return await asyncio.wait_for(check(deps), limit)
    except TimeoutError:
        return _fail(f"Timed out after {limit:g} s")
    except Exception as exc:  # noqa: BLE001 - a check must report, never crash the page
        log.warning("preflight %s failed: %s", name, type(exc).__name__)
        return _fail(f"Check failed unexpectedly ({_why(exc)})")


def make_runner(deps: PreflightDeps, *, fake: bool = False) -> Callable[[str], Awaitable[Result]]:
    """What the server calls for POST /api/check/{name}. In --fake mode only the
    button check is real (it needs no keys); the rest say there is nothing to check."""
    async def runner(name: str) -> Result:
        if fake and name != "button":
            return _ok("Fake mode: nothing real to check")
        return await run_check(name, deps)
    return runner


# ----- microphone -----

async def _mic(deps: PreflightDeps) -> Result:
    """Open the default mic for MIC_LISTEN_S and report the loudness. Audio is only
    ever measured, never kept."""
    sum_squares, count = 0.0, 0
    try:
        async with asyncio.timeout(MIC_LISTEN_S):
            async with contextlib.aclosing(deps.mic_factory()) as mic:
                async for chunk in mic:
                    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float64) / 32768
                    sum_squares += float(np.sum(samples * samples))
                    count += len(samples)
    except TimeoutError:
        pass  # the listening time is up
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Could not open the microphone ({_why(exc)}). Check it is plugged in "
                     "and that Windows privacy settings allow microphone access.")
    if count == 0:
        return _fail("No audio came from the microphone. Check it is plugged in and set as the default.")
    rms = (sum_squares / count) ** 0.5
    level = f"Level {rms * 100:.1f}% of full scale"
    if rms < MIC_MIN_RMS:
        return _fail(f"Very quiet: {level}. Say something while it listens, and check the mic is not muted.")
    return _ok(f"{level}: the microphone is picking up sound.")


# ----- button -----

async def _wait_for_press(bus: EventBus, wait_s: float):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while (left := deadline - loop.time()) > 0:
        ev = await bus.next(left)
        if ev is None:
            return None
        if ev.kind in ("taps", "hold_start"):
            return ev
    return None


async def _button(deps: PreflightDeps) -> Result:
    """Wait for a press on the show's own bus. The show is paused meanwhile, so the
    press is not taken as a visitor starting."""
    if deps.button_lock.locked():
        return _fail("A button check is already running")
    async with deps.button_lock:
        show = deps.show
        was_paused = show.paused
        if not was_paused:
            if show.state != "idle":
                return _fail("A visitor is using the stall right now. "
                             "Try again when the TV shows the idle screen.")
            await show.set_paused(True)
            # An idle show only notices the pause on its next poll; it must not take our press.
            await asyncio.sleep(2 * show.poll_s + BUTTON_SETTLE_S)
        deps.bus.clear()
        try:
            ev = await _wait_for_press(deps.bus, BUTTON_WAIT_S)
        finally:
            deps.bus.clear()
            if not was_paused:
                await show.set_paused(False)
    if ev is None:
        key = deps.cfg.button.key.upper()
        return _fail(f"No button press seen in {BUTTON_WAIT_S:g} s. The button should send the "
                     f"{key} key (button.key in config.toml).")
    if ev.kind == "hold_start":
        return _ok("Button works: a hold was seen.")
    return _ok(f"Button works: {ev.n} tap{'s' if ev.n != 1 else ''} seen.")


# ----- reachability (the health checks) -----

async def _reach(deps: PreflightDeps, key: str, what: str, hint: str) -> Result:
    check = deps.checks.get(key)
    if check is None:
        return _fail(f"{what}: no check is set up")
    started = time.perf_counter()
    try:
        reachable = await check()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{what} failed ({_why(exc)}). {hint}")
    if not reachable:
        return _fail(f"{what} did not answer properly ({_ms(started)} ms). {hint}")
    return _ok(f"{what} reachable ({_ms(started)} ms)")


async def _internet(deps: PreflightDeps) -> Result:
    # OpenRouter's public model list: no key needed, so this is a plain internet test.
    return await _reach(deps, "openrouter", "Internet (OpenRouter)",
                        "Check the venue Wi-Fi and that this laptop is connected.")


async def _google(deps: PreflightDeps) -> Result:
    return await _reach(deps, "google", f"Google image model {deps.cfg.models.image}",
                        "Check the internet, and GOOGLE_API_KEY in the .env file.")


# ----- speech-to-text -----

class _Watch:
    """Records whether the live session connected, and why not if it failed."""

    def __init__(self) -> None:
        self.connected = asyncio.Event()
        self.error: str | None = None


class _WatchedSession:
    def __init__(self, cm, watch: _Watch):
        self._cm, self._watch = cm, watch

    async def __aenter__(self):
        try:
            session = await self._cm.__aenter__()
        except Exception as exc:  # noqa: BLE001
            self._watch.error = _why(exc)
            raise
        self._watch.connected.set()
        return session

    async def __aexit__(self, *exc_info):
        return await self._cm.__aexit__(*exc_info)


def _watched_client(client, watch: _Watch):
    live = client.aio.live

    class WatchedLive:
        def connect(self, **kwargs):
            return _WatchedSession(live.connect(**kwargs), watch)

    return pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(live=WatchedLive()))


async def _silent_mic():
    chunk = bytes(CHUNK_FRAMES * 2)  # 100 ms of 16 kHz mono int16 silence
    for _ in range(int(STT_SILENCE_S * 10)):
        yield chunk
        await asyncio.sleep(0.1)
    await asyncio.Event().wait()  # like a live mic: stays open until stopped


async def _stt(deps: PreflightDeps) -> Result:
    """Open a real live session, send about 0.5 s of silence, close it. The session gets
    its own bus, so its stt_error never reaches the show."""
    bus, watch = EventBus(), _Watch()
    voice = Voice(_watched_client(deps.google, watch), deps.cfg.models.stt, bus,
                  mic_factory=_silent_mic)
    started = time.perf_counter()
    await voice.start()
    connect_ms = 0
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STT_CONNECT_WAIT_S
        failed = False
        while not watch.connected.is_set() and not failed and loop.time() < deadline:
            ev = await bus.next(0.05)
            failed = ev is not None and ev.kind == "stt_error"
        if watch.connected.is_set():
            connect_ms = _ms(started)
            await asyncio.sleep(STT_SILENCE_S)
    finally:
        await voice.stop()
    errored = False
    while (ev := await bus.next(0.01)) is not None:
        errored = errored or ev.kind == "stt_error"
    if not watch.connected.is_set() or errored:
        why = watch.error or "no answer in time"
        return _fail(f"Could not use the speech-to-text service ({why}). "
                     "Check the internet, and GOOGLE_API_KEY in the .env file.")
    return _ok(f"{deps.cfg.models.stt}: connected in {connect_ms} ms, silence sent, closed cleanly")


# ----- LLM calls -----

async def _llm_call(deps: PreflightDeps, which: str, model: str, what: str, hint: str) -> Result:
    started = time.perf_counter()
    try:
        reply = await deps.llm.probe(which, **LLM_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{what} ({model}) call failed: {_why(exc)}. {hint}")
    ms = _ms(started)
    if not (reply.text or "").strip():
        return _fail(f"{what} ({model}) answered with no text ({ms} ms)")
    return _ok(f"{reply.model or model} answered in {ms} ms")


async def _openrouter(deps: PreflightDeps) -> Result:
    return await _llm_call(deps, "openrouter", deps.cfg.models.llm, "OpenRouter",
                           "Check the internet, and OPENROUTER_API_KEY in the .env file.")


async def _fallback_llm(deps: PreflightDeps) -> Result:
    model = deps.cfg.models.fallback_llm
    hint = "Is Tailscale signed in on this laptop, and FALLBACK_LLM_URL and FALLBACK_LLM_KEY in the .env file right?"
    reachable = deps.checks.get("fallback_llm")
    if reachable is not None:  # a cheap check first: a clear answer when Tailscale is off
        try:
            up = await reachable()
        except Exception:  # noqa: BLE001
            up = False
        if not up:
            return _fail(f"Qwen fallback ({model}) is not reachable. {hint}")
    return await _llm_call(deps, "fallback", model, "Qwen fallback", hint)


# ----- gallery -----

def _tiny_jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "#335577").save(out, format="JPEG", quality=85)
    return out.getvalue()


async def _upload_and_fetch(deps: PreflightDeps, base: str, steps: list[str]) -> str | None:
    """Upload the test picture and fetch it back. Returns the problem, or None."""
    headers = {"Authorization": f"Bearer {deps.secrets.gallery_token}"}
    data = {
        "id": TEST_IMAGE_ID, "prompt": "preflight test", "mode": "kid",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    files = {"image": (f"{TEST_IMAGE_ID}.jpg", _tiny_jpeg(), "image/jpeg")}
    try:
        started = time.perf_counter()
        response = await deps.http.post(f"{base}/api/upload", data=data, files=files,
                                        headers=headers, timeout=STEP_TIMEOUT_S)
        if not response.is_success:
            hint = " (check GALLERY_TOKEN)" if response.status_code in (401, 403) else ""
            return f"upload refused, HTTP {response.status_code}{hint}"
        steps.append(f"upload {_ms(started)} ms")
        started = time.perf_counter()
        response = await deps.http.get(f"{base}/img/{TEST_IMAGE_ID}.jpg", timeout=STEP_TIMEOUT_S)
        if not response.is_success or not response.content.startswith(b"\xff\xd8\xff"):
            return f"uploaded, but fetching it back failed (HTTP {response.status_code})"
        steps.append(f"fetch {_ms(started)} ms")
    except Exception as exc:  # noqa: BLE001
        return f"could not reach the gallery ({_why(exc)})"
    return None


async def _delete_test_image(deps: PreflightDeps) -> tuple[bool, int]:
    started = time.perf_counter()
    try:
        deleted = await asyncio.wait_for(deps.uploader.delete(TEST_IMAGE_ID), STEP_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - includes the step timeout
        deleted = False
    return bool(deleted), _ms(started)


async def _gallery(deps: PreflightDeps) -> Result:
    base = deps.secrets.gallery_url.rstrip("/")
    if not base or not deps.secrets.gallery_token:
        return _fail("GALLERY_URL or GALLERY_TOKEN is not set in the .env file.")
    steps: list[str] = []
    try:
        problem = await _upload_and_fetch(deps, base, steps)
    finally:  # also if the whole check is cut off by its timeout
        deleted, delete_ms = await _delete_test_image(deps)
    if deleted:
        steps.append(f"delete {delete_ms} ms")
    elif problem is None:
        problem = f"the test picture could not be deleted (id {TEST_IMAGE_ID}: remove it from the gallery by hand)"
    else:
        problem += f"; the delete of test id {TEST_IMAGE_ID} also failed"
    if problem is not None:
        return _fail(f"Gallery: {problem}." + (f" Done so far: {', '.join(steps)}." if steps else ""))
    return _ok(f"Gallery works: {', '.join(steps)}. Test picture removed.")


_CHECKS: dict[str, Callable[[PreflightDeps], Awaitable[Result]]] = {
    "mic": _mic,
    "button": _button,
    "internet": _internet,
    "google": _google,
    "stt": _stt,
    "openrouter": _openrouter,
    "fallback_llm": _fallback_llm,
    "gallery": _gallery,
}

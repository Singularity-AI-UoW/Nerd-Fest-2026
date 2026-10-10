"""Preflight checks, the /check routes, and the wipe and e2e scripts' argument handling.

Nothing here touches the network, the microphone or a real gallery: the gallery is an
httpx.MockTransport and every other service is a small fake.
"""
from __future__ import annotations

import asyncio
import types as pytypes
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from kiosk import preflight
from kiosk import voice as voice_mod
from kiosk.config import REPO_ROOT, Secrets, load_config
from kiosk.events import Event, EventBus
from kiosk.preflight import PREFLIGHT, PreflightDeps, run_check
from kiosk.store import Store
from kiosk.uploader import Uploader
from kiosk.web.server import Hub, create_app

BASE = "https://gallery.example"
TOKEN = "gallery-token-not-real"
KEY = "sk-secret-key-not-real"
CFG = load_config(REPO_ROOT / "config.toml")
SECRETS = Secrets(KEY, KEY, "http://qwen.invalid/v1", KEY, BASE, TOKEN)


# ----- fakes -----

class Gallery:
    """Records requests; each route's status can be changed."""

    def __init__(self, upload=200, fetch=200, delete=200):
        self.status = {"POST": upload, "GET": fetch, "DELETE": delete}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        status = self.status[request.method]
        if request.method == "GET":
            body = b"\xff\xd8\xff\xe0jpeg" if status == 200 else b"nope"
            return httpx.Response(status, content=body, headers={"content-type": "image/jpeg"})
        return httpx.Response(status, json={"ok": status < 400})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests]


class FakeLlm:
    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail = fail or {}
        self.calls: list[str] = []

    async def probe(self, which: str, *, instructions: str, input: str):
        self.calls.append(which)
        if which in self.fail:
            raise self.fail[which]
        model = {"openrouter": "openai/gpt-6.1-sol", "fallback": "Qwen3.8-27B"}[which]
        return pytypes.SimpleNamespace(text="ready", model=model)


class FakeShow:
    def __init__(self, paused=False, state="idle"):
        self.paused, self.state, self.poll_s = paused, state, 0.01
        self.history: list[bool] = []

    async def set_paused(self, p: bool) -> None:
        self.paused = p
        self.history.append(p)


def chunks_of(level: int, n: int = 3) -> list[bytes]:
    return [np.full(1600, level, dtype=np.int16).tobytes()] * n


def mic_factory(chunks: list[bytes] | None, *, error: Exception | None = None):
    async def mic():
        if error is not None:
            raise error
        for c in chunks or []:
            yield c
            await asyncio.sleep(0.01)
        await asyncio.Event().wait()  # like a live mic: no more chunks, never ends
    return mic


async def ok_check() -> bool:
    return True


async def bad_check() -> bool:
    return False


def make_deps(tmp_path: Path, *, gallery: Gallery | None = None, **kw) -> PreflightDeps:
    http = (gallery or Gallery()).client()
    defaults = dict(
        cfg=CFG, secrets=SECRETS,
        checks={"gallery": ok_check, "google": ok_check, "openrouter": ok_check, "fallback_llm": ok_check},
        llm=FakeLlm(), http=http, uploader=Uploader(Store(tmp_path), BASE, TOKEN, http),
        google=None, bus=EventBus(), show=FakeShow(), mic_factory=mic_factory(chunks_of(3000)),
    )
    return PreflightDeps(**{**defaults, **kw})


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(preflight, "MIC_LISTEN_S", 0.2)
    monkeypatch.setattr(preflight, "BUTTON_WAIT_S", 0.5)
    monkeypatch.setattr(voice_mod, "FINAL_TEXT_WAIT_S", 0.2)  # the fake session never answers
    monkeypatch.setattr(voice_mod, "SESSION_CLOSE_WAIT_S", 0.2)
    monkeypatch.setattr(voice_mod, "MIC_CLOSE_WAIT_S", 0.2)


def assert_no_secrets(result: dict) -> None:
    assert KEY not in result["detail"] and TOKEN not in result["detail"]


# ----- the list and the result shape -----

def test_preflight_list_names():
    assert [n for n, _ in PREFLIGHT] == [
        "mic", "button", "internet", "google", "stt", "openrouter", "fallback_llm", "gallery",
    ]
    assert all(label for _, label in PREFLIGHT)


async def test_unknown_check(tmp_path):
    result = await run_check("nope", make_deps(tmp_path))
    assert result["ok"] is False and "nope" in result["detail"]


async def test_check_is_bounded_by_a_timeout(tmp_path, monkeypatch):
    async def hang() -> bool:
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(preflight, "TIMEOUT_S", {**preflight.TIMEOUT_S, "google": 0.05})
    result = await run_check("google", make_deps(tmp_path, checks={"google": hang}))
    assert result["ok"] is False and "Timed out" in result["detail"]


# ----- gallery -----

async def test_gallery_check_uploads_then_deletes(tmp_path):
    gallery = Gallery()
    result = await run_check("gallery", make_deps(tmp_path, gallery=gallery))
    assert result["ok"] is True, result
    assert gallery.calls() == [
        ("POST", "/api/upload"), ("GET", "/img/testtest.jpg"), ("DELETE", "/api/images/testtest"),
    ]
    upload = gallery.requests[0]
    assert upload.headers["authorization"] == f"Bearer {TOKEN}"
    assert b'name="id"\r\n\r\ntesttest' in upload.content
    assert b"\xff\xd8\xff" in upload.content  # a real JPEG went up
    assert "ms" in result["detail"]
    assert_no_secrets(result)


@pytest.mark.parametrize("failing", [{"upload": 401}, {"upload": 500}, {"fetch": 404}])
async def test_gallery_check_deletes_even_if_upload_or_verify_fails(tmp_path, failing):
    gallery = Gallery(**failing)
    result = await run_check("gallery", make_deps(tmp_path, gallery=gallery))
    assert result["ok"] is False
    assert ("DELETE", "/api/images/testtest") in gallery.calls()
    assert_no_secrets(result)


async def test_gallery_check_reports_a_failed_delete(tmp_path):
    result = await run_check("gallery", make_deps(tmp_path, gallery=Gallery(delete=500)))
    assert result["ok"] is False and "delete" in result["detail"].lower()


async def test_gallery_check_unreachable_still_tries_delete_and_names_the_cause(tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    deps = make_deps(tmp_path, http=http, uploader=Uploader(Store(tmp_path), BASE, TOKEN, http))
    result = await run_check("gallery", deps)
    assert result["ok"] is False and "ConnectError" in result["detail"]
    assert_no_secrets(result)


# ----- LLM calls -----

async def test_openrouter_check_reports_model_and_latency(tmp_path):
    deps = make_deps(tmp_path)
    result = await run_check("openrouter", deps)
    assert result["ok"] is True
    assert "openai/gpt-6.1-sol" in result["detail"] and "ms" in result["detail"]
    assert deps.llm.calls == ["openrouter"]


async def test_fallback_check_reports_failure_detail(tmp_path):
    llm = FakeLlm({"fallback": httpx.ConnectError(f"cannot reach http://qwen.invalid/v1 key={KEY}")})
    result = await run_check("fallback_llm", make_deps(tmp_path, llm=llm))
    assert result["ok"] is False
    assert "Qwen3.8-27B" in result["detail"] and "ConnectError" in result["detail"]
    assert "Tailscale" in result["detail"]
    assert_no_secrets(result)  # the exception message is never copied into the detail


async def test_fallback_check_success(tmp_path):
    result = await run_check("fallback_llm", make_deps(tmp_path))
    assert result["ok"] is True and "Qwen3.8-27B" in result["detail"]


async def test_fallback_check_stops_early_when_unreachable(tmp_path):
    deps = make_deps(tmp_path, checks={"fallback_llm": bad_check})
    result = await run_check("fallback_llm", deps)
    assert result["ok"] is False and "Tailscale" in result["detail"]
    assert deps.llm.calls == []


# ----- reachability (health checks) -----

@pytest.mark.parametrize("name,key", [("internet", "openrouter"), ("google", "google")])
async def test_reachability_checks_reuse_health_checks(tmp_path, name, key):
    good = await run_check(name, make_deps(tmp_path, checks={key: ok_check}))
    assert good["ok"] is True and "ms" in good["detail"]
    bad = await run_check(name, make_deps(tmp_path, checks={key: bad_check}))
    assert bad["ok"] is False


async def test_reachability_failure_names_the_exception_type_only(tmp_path):
    async def boom() -> bool:
        raise RuntimeError(f"secret in message {KEY}")

    result = await run_check("google", make_deps(tmp_path, checks={"google": boom}))
    assert result["ok"] is False and "RuntimeError" in result["detail"]
    assert_no_secrets(result)


# ----- microphone -----

async def test_mic_check_reports_level(tmp_path):
    result = await run_check("mic", make_deps(tmp_path, mic_factory=mic_factory(chunks_of(3000))))
    assert result["ok"] is True and "%" in result["detail"]


async def test_mic_check_flags_a_silent_mic(tmp_path):
    result = await run_check("mic", make_deps(tmp_path, mic_factory=mic_factory(chunks_of(0))))
    assert result["ok"] is False and "quiet" in result["detail"].lower()


async def test_mic_check_no_audio_at_all(tmp_path):
    result = await run_check("mic", make_deps(tmp_path, mic_factory=mic_factory([])))
    assert result["ok"] is False and "no audio" in result["detail"].lower()


async def test_mic_check_device_error(tmp_path):
    deps = make_deps(tmp_path, mic_factory=mic_factory(None, error=OSError(f"device {KEY}")))
    result = await run_check("mic", deps)
    assert result["ok"] is False and "OSError" in result["detail"]
    assert_no_secrets(result)


# ----- button -----

async def test_button_check_sees_a_press_and_restores_pause(tmp_path):
    show, bus = FakeShow(paused=False), EventBus()
    deps = make_deps(tmp_path, show=show, bus=bus)

    async def press():
        await asyncio.sleep(0.3)
        bus.put(Event("hold_end"))  # not a press start: ignored
        bus.put(Event("taps", n=1))

    task = asyncio.create_task(press())
    result = await run_check("button", deps)
    await task
    assert result["ok"] is True and "tap" in result["detail"].lower()
    assert show.history == [True, False]  # paused for the check, then put back
    assert show.paused is False
    assert bus._q.empty()


async def test_button_check_accepts_hold_start(tmp_path):
    bus = EventBus()
    deps = make_deps(tmp_path, bus=bus)

    async def press():
        await asyncio.sleep(0.3)
        bus.put(Event("hold_start"))

    task = asyncio.create_task(press())
    result = await run_check("button", deps)
    await task
    assert result["ok"] is True and "hold" in result["detail"].lower()


async def test_button_check_times_out_and_still_restores_pause(tmp_path):
    show = FakeShow(paused=False)
    result = await run_check("button", make_deps(tmp_path, show=show))
    assert result["ok"] is False and "No button press" in result["detail"]
    assert show.paused is False and show.history == [True, False]


async def test_button_check_leaves_an_already_paused_show_paused(tmp_path):
    show, bus = FakeShow(paused=True), EventBus()
    deps = make_deps(tmp_path, show=show, bus=bus)
    bus.put(Event("taps", n=1))  # a press from before the check is not the answer
    result = await run_check("button", deps)
    assert result["ok"] is False
    assert show.paused is True and show.history == []


async def test_button_check_refuses_while_a_visitor_is_mid_turn(tmp_path):
    show = FakeShow(paused=False, state="agent")
    result = await run_check("button", make_deps(tmp_path, show=show))
    assert result["ok"] is False and "visitor" in result["detail"].lower()
    assert show.history == []


# ----- speech-to-text connect -----

class FakeLive:
    def __init__(self, fail: Exception | None = None):
        self.fail, self.closed, self.sent = fail, False, []

    def connect(self, *, model, config):
        return _FakeSessionCm(self)


class _FakeSessionCm:
    def __init__(self, live):
        self.live = live

    async def __aenter__(self):
        if self.live.fail is not None:
            raise self.live.fail
        return _FakeSession(self.live)

    async def __aexit__(self, *exc):
        self.live.closed = True


class _FakeSession:
    def __init__(self, live):
        self.live = live

    async def send_realtime_input(self, **kw):
        self.live.sent.append(next(iter(kw)))

    async def receive(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover


async def test_stt_check_opens_and_closes_a_session(tmp_path):
    live = FakeLive()
    google = pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(live=live))
    result = await run_check("stt", make_deps(tmp_path, google=google))
    assert result["ok"] is True and "ms" in result["detail"]
    assert live.closed
    assert "audio" in live.sent  # silence went up the real Voice code path


async def test_stt_check_reports_connect_failure(tmp_path):
    live = FakeLive(fail=ConnectionError(f"key={KEY}"))
    google = pytypes.SimpleNamespace(aio=pytypes.SimpleNamespace(live=live))
    deps = make_deps(tmp_path, google=google)
    result = await run_check("stt", deps)
    assert result["ok"] is False and "ConnectionError" in result["detail"]
    assert_no_secrets(result)
    assert deps.bus._q.empty()  # the check's own stt_error never reaches the show's bus


# ----- the /check routes -----

class StubControls:
    async def set_paused(self, p): ...
    async def remove(self, id): return False
    def winners(self): return []


@pytest.fixture
def client(tmp_path):
    calls: list[str] = []

    async def runner(name: str) -> dict:
        calls.append(name)
        return {"ok": name != "internet", "detail": f"{name} detail"}

    app = create_app(Hub(), EventBus(), Store(tmp_path), StubControls(), check_runner=runner)
    with TestClient(app) as c:
        c.calls = calls
        yield c


def test_check_page_is_served(client):
    r = client.get("/check")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    for name, _ in PREFLIGHT:
        assert f'"{name}"' in r.text  # the page knows every check by name


def test_organiser_page_links_to_check():
    html = (Path(preflight.__file__).parent / "web" / "static" / "organiser.html").read_text()
    assert 'href="/check"' in html


def test_check_api_runs_one_check(client):
    r = client.post("/api/check/google")
    assert r.status_code == 200 and r.json() == {"ok": True, "detail": "google detail"}
    assert client.post("/api/check/internet").json()["ok"] is False
    assert client.calls == ["google", "internet"]


def test_check_api_unknown_name_is_404(client):
    assert client.post("/api/check/nope").status_code == 404
    assert client.calls == []


def test_check_api_without_a_runner_is_503(tmp_path):
    app = create_app(Hub(), EventBus(), Store(tmp_path), StubControls())
    with TestClient(app) as c:
        assert c.post("/api/check/google").status_code == 503


# ----- scripts -----

def test_wipe_requires_yes(capsys):
    from scripts import wipe_gallery

    def no_network(request):
        raise AssertionError("must not touch the network without --yes")

    code = wipe_gallery.main([], transport=httpx.MockTransport(no_network), secrets=SECRETS)
    assert code != 0
    assert "--yes" in capsys.readouterr().err


def test_wipe_deletes_every_image_and_reports_failures(capsys):
    from scripts import wipe_gallery

    remaining = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
    deleted: list[str] = []
    auth: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"images": [{"id": i} for i in remaining]})
        auth.add(request.headers["authorization"])
        id_ = request.url.path.rsplit("/", 1)[1]
        if id_ == "bbbbbbbb":
            return httpx.Response(500)
        deleted.append(id_)
        remaining.remove(id_)
        return httpx.Response(200, json={"ok": True})

    code = wipe_gallery.main(["--yes"], transport=httpx.MockTransport(handler), secrets=SECRETS)
    out = capsys.readouterr()
    assert deleted == ["aaaaaaaa", "cccccccc"]
    assert auth == {f"Bearer {TOKEN}"}
    assert code == 1
    assert BASE in out.out and "3" in out.out  # says which gallery, and how many
    assert "bbbbbbbb" in out.out + out.err
    assert TOKEN not in out.out + out.err


def test_wipe_clean_run_returns_zero(capsys):
    from scripts import wipe_gallery

    remaining = ["aaaaaaaa", "bbbbbbbb"]

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"images": [{"id": i} for i in remaining]})
        remaining.remove(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"ok": True})

    assert wipe_gallery.main(["--yes"], transport=httpx.MockTransport(handler), secrets=SECRETS) == 0
    assert remaining == []


def test_wipe_without_gallery_settings_fails_before_any_request(capsys):
    from scripts import wipe_gallery

    empty = Secrets("", "", "", "", "", "")
    assert wipe_gallery.main(["--yes"], secrets=empty) != 0


def test_e2e_real_argument_handling(capsys):
    from scripts import e2e_real

    args = e2e_real.parse_args([])
    assert args.text == "a kiwi bird surfing a giant wave"
    assert args.max_total_s == 45
    assert e2e_real.parse_args(["--text", "a dragon"]).text == "a dragon"
    with pytest.raises(SystemExit):
        e2e_real.parse_args(["--bogus"])


def test_e2e_real_refuses_without_gallery_settings(capsys):
    from scripts import e2e_real

    empty = Secrets("", "", "", "", "", "")
    assert e2e_real.main([], secrets=empty) == 2
    assert "GALLERY_URL" in capsys.readouterr().err


def test_e2e_real_never_names_a_default_gallery_url():
    source = (REPO_ROOT / "scripts" / "e2e_real.py").read_text()
    assert "http://" not in source.replace("http://127.0.0.1", "")
    assert "https://" not in source


# ----- Windows start files -----

@pytest.mark.parametrize("name,fake", [("start.bat", False), ("start-fake.bat", True)])
def test_start_bat_files(name, fake):
    raw = (REPO_ROOT / name).read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "every line ending must be CRLF"
    text = raw.decode("ascii")
    lines = [line.strip() for line in text.splitlines()]
    assert 'cd /d "%~dp0"' in lines
    assert any("where uv" in line for line in lines)
    assert 'echo powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"' in lines
    steps = [line for line in lines if line.startswith("uv ")]
    assert steps == ["uv sync", "uv run playwright install chromium",
                     "uv run python -m kiosk" + (" --fake" if fake else "")]
    assert lines[-1] == "pause"


def test_gitattributes_keeps_bat_files_crlf():
    assert "*.bat text eol=crlf" in (REPO_ROOT / ".gitattributes").read_text().splitlines()


def test_readme_has_no_secrets_or_data():
    text = (REPO_ROOT / "README.md").read_text()
    for needle in ("AIza", "sk-", "Bearer", "GALLERY_TOKEN="):
        assert needle not in text

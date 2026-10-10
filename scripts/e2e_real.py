"""One real visitor turn against the real services, for the afternoon of 30/10/2026.

    uv run python scripts/e2e_real.py [--text "a kiwi bird surfing a giant wave"]

Typed request -> safety check -> LLM agent driving a headless studio -> real image ->
upload to the gallery in ~/.nerdfest/.env -> fetch it back -> delete it. Prints the time
each stage took and PASS or FAIL (the whole turn must take under 45 s).

This calls the paid APIs and the live gallery, so it is NOT part of the test suite.
It always deletes the picture it uploaded, even when a stage fails.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import socket
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script: find `kiosk`

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from kiosk.__main__ import _launch_tv_browser, _wait_started  # noqa: E402
from kiosk.agent import Job, LlmAgent, ToolContext  # noqa: E402
from kiosk.blocklist import Blocklist  # noqa: E402
from kiosk.config import REPO_ROOT, Secrets, load_config, load_secrets, repo_path  # noqa: E402
from kiosk.events import EventBus  # noqa: E402
from kiosk.images import make_image_service  # noqa: E402
from kiosk.llm import make_llm_client  # noqa: E402
from kiosk.safety import Safety  # noqa: E402
from kiosk.store import Store  # noqa: E402
from kiosk.studio import Studio  # noqa: E402
from kiosk.uploader import Uploader  # noqa: E402
from kiosk.web.server import Hub, create_app  # noqa: E402

DEFAULT_TEXT = "a kiwi bird surfing a giant wave"
MAX_TOTAL_S = 45
HTTP_TIMEOUT_S = 30.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One real visitor turn, end to end.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="the typed request")
    parser.add_argument("--max-total-s", type=float, default=MAX_TOTAL_S,
                        help="fail if the whole turn takes longer than this")
    return parser.parse_args(argv)


def _missing(secrets: Secrets) -> list[str]:
    needed = {
        "GOOGLE_API_KEY": secrets.google_api_key,
        "OPENROUTER_API_KEY": secrets.openrouter_api_key,
        "FALLBACK_LLM_URL": secrets.fallback_llm_url,
        "FALLBACK_LLM_KEY": secrets.fallback_llm_key,
        "GALLERY_URL": secrets.gallery_url,
        "GALLERY_TOKEN": secrets.gallery_token,
    }
    return [name for name, value in needed.items() if not value]


class _NoControls:
    async def set_paused(self, p: bool) -> None: ...
    async def remove(self, id: str) -> bool: return False
    def winners(self) -> list[dict]: return []


async def _no_text(_: str) -> None: ...
async def _no_tool(_name: str, _args: dict) -> None: ...
async def _keep(_options: list[str]) -> None: return None  # no tweak


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Timer:
    def __init__(self) -> None:
        self.stages: list[tuple[str, float]] = []
        self._start = time.perf_counter()

    @contextlib.contextmanager
    def stage(self, name: str):
        began = time.perf_counter()
        try:
            yield
        finally:
            self.stages.append((name, time.perf_counter() - began))
            print(f"  {name:<10} {self.stages[-1][1]:5.1f} s", flush=True)

    @property
    def total(self) -> float:
        return time.perf_counter() - self._start


async def _turn(args: argparse.Namespace, secrets: Secrets, timer: _Timer, work_dir: Path) -> str:
    """Run the turn. Returns "" if it worked, else what went wrong."""
    cfg = load_config(REPO_ROOT / "config.toml")
    store = Store(work_dir)
    llm, images = make_llm_client(cfg, secrets), make_image_service(cfg, secrets)
    safety = Safety(Blocklist.from_file(repo_path(cfg.files.blocklist)), llm,
                    cfg.limits.max_request_chars, cfg.preset_tweaks)
    base = secrets.gallery_url.rstrip("/")
    rec_id: str | None = None

    async with contextlib.AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=HTTP_TIMEOUT_S))
        uploader = Uploader(store, base, secrets.gallery_token, http)

        # A local copy of the kiosk's pages, so the studio frame is the real one.
        hub, port = Hub(), _free_port()
        server = uvicorn.Server(uvicorn.Config(
            create_app(hub, EventBus(), store, _NoControls()),
            host="127.0.0.1", port=port, log_level="warning"))
        server_task = asyncio.create_task(server.serve())
        pw = await stack.enter_async_context(async_playwright())
        browser = await _launch_tv_browser(pw, cfg, True)
        try:
            await _wait_started(server, server_task)
            page = browser.pages[0] if browser.pages else await browser.new_page()
            await hub.send({"type": "state", "state": "agent", "mode": "kid"})  # shows the studio
            await page.goto(f"http://127.0.0.1:{port}/tv")

            print(f'Request: "{args.text}"', flush=True)
            with timer.stage("safety"):
                verdict = await safety.check_request(args.text, "kid")
            if not verdict.allowed or not verdict.image_prompt:
                return "the safety check did not allow the request"

            with timer.stage("agent"):
                ctx = ToolContext(studio=Studio(page), images=images, safety=safety,
                                  narrate=_no_text, on_tool=_no_tool, choose_tweak=_keep)
                result = await asyncio.wait_for(
                    LlmAgent(llm, cfg.limits.max_tool_calls).run(
                        Job(args.text, verdict.image_prompt, "kid"), ctx),
                    cfg.timeouts.agent_cap_s)

            rec = store.new_record(result.prompt, "kid")
            rec_id = rec.id
            store.save_image(rec.id, result.jpeg)
            with timer.stage("upload"):
                if not await uploader.upload_once(rec):
                    return "the upload to the gallery failed"
            with timer.stage("fetch"):
                response = await http.get(f"{base}/img/{rec.id}.jpg")
                if not response.is_success or not response.content.startswith(b"\xff\xd8\xff"):
                    return f"the picture could not be fetched back (HTTP {response.status_code})"
            return ""
        finally:
            if rec_id is not None:  # always clean up the gallery
                with timer.stage("delete"):
                    if not await uploader.delete(rec_id):
                        print(f"  WARNING: could not delete {rec_id} from the gallery; "
                              "remove it on the organiser page", file=sys.stderr)
            with contextlib.suppress(Exception):
                await browser.close()
            server.should_exit = True
            await asyncio.wait([server_task], timeout=10)


def main(argv: list[str] | None = None, *, secrets: Secrets | None = None) -> int:
    args = parse_args(argv)
    secrets = secrets if secrets is not None else load_secrets()
    if missing := _missing(secrets):
        print(f"Not set in the .env file: {', '.join(missing)}", file=sys.stderr)
        return 2
    timer = _Timer()
    try:
        with tempfile.TemporaryDirectory(prefix="nerdfest-e2e-") as work_dir:
            problem = asyncio.run(_turn(args, secrets, timer, Path(work_dir)))
    except Exception as exc:  # noqa: BLE001 - type only: messages can hold URLs
        problem = f"{type(exc).__name__} during the turn"
    total = timer.total
    if not problem and total > args.max_total_s:
        problem = f"too slow: {total:.1f} s is over the {args.max_total_s:g} s limit"
    if problem:
        print(f"FAIL: {problem} (total {total:.1f} s)")
        return 1
    print(f"PASS (total {total:.1f} s, limit {args.max_total_s:g} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

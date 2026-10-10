"""Run the stall: `uv run python -m kiosk [--fake] [--headless]`.

Serves the pages on 127.0.0.1, shows /tv in a Chromium kiosk window on the TV
(the agent drives the studio frame inside it), opens /organiser in the default
browser, and runs the show and the uploader in the same asyncio loop.

--fake: fake LLM, images, voice and uploader; needs no keys and no network.
--headless: Chromium without a window, and no organiser tab (for tests).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import webbrowser

import httpx
import uvicorn
from google import genai
from playwright.async_api import BrowserContext, async_playwright

from kiosk.agent import LlmAgent, ScriptedAgent
from kiosk.blocklist import Blocklist
from kiosk.config import REPO_ROOT, Config, Secrets, load_config, load_secrets, repo_path
from kiosk.controls import StallControls
from kiosk.events import EventBus
from kiosk.fakes import FakeHappyLlm, FakeImages, FakeUploader, FakeVoice
from kiosk.health import Health, fake_checks, make_checks
from kiosk.images import make_image_service
from kiosk.inputs import KeyInput, TapDetector
from kiosk.llm import make_llm_client
from kiosk.preflight import PreflightDeps, make_runner
from kiosk.safety import Safety
from kiosk.show import Show
from kiosk.slideshow import Slideshow, local_images_source
from kiosk.store import Store
from kiosk.studio import Studio
from kiosk.takehome import build_takehome, picture_qr_svg, signup_qr_svg
from kiosk.uploader import Uploader
from kiosk.voice import Voice
from kiosk.winner import WinnerScreen
from kiosk.web.server import Hub, create_app

log = logging.getLogger("kiosk")

FAKE_GALLERY_URL = "http://gallery.invalid"
FAKE_IMAGE_DELAY_S = 2.0
UPLOAD_HTTP_TIMEOUT_S = 30.0  # per gallery request; the image is at most a few hundred KB
SLIDESHOW_INTERVAL_S = 60.0
SERVER_START_TIMEOUT_S = 15.0
SERVER_STOP_TIMEOUT_S = 10.0
HEADLESS_VIEWPORT = {"width": 1920, "height": 1080}


def fake_secrets() -> Secrets:
    """Secrets for --fake: no keys at all, and a gallery URL that never resolves."""
    return Secrets("", "", "", "", FAKE_GALLERY_URL, "")


async def _launch_tv_browser(pw, cfg: Config, headless: bool) -> BrowserContext:
    # A persistent context ("" = throwaway profile) uses Chromium's own first
    # window, so --kiosk makes that window full screen on the TV.
    if headless:
        return await pw.chromium.launch_persistent_context(
            "", headless=True, viewport=HEADLESS_VIEWPORT
        )
    return await pw.chromium.launch_persistent_context(
        "",
        headless=False,
        no_viewport=True,
        args=["--kiosk", f"--window-position={cfg.display.tv_window_x},0"],
        ignore_default_args=["--enable-automation"],  # no "controlled by software" bar
    )


async def _close_quietly(context: BrowserContext) -> None:
    try:
        await context.close()
    except Exception as exc:  # noqa: BLE001 - Ctrl+C may have killed it already
        log.debug("browser close: %s", type(exc).__name__)


async def _wait_started(server: uvicorn.Server, task: asyncio.Task) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SERVER_START_TIMEOUT_S
    while not server.started:
        if task.done():
            await task  # surfaces the start-up error
            raise RuntimeError("web server stopped during start-up")
        if loop.time() > deadline:
            raise RuntimeError("web server did not start")
        await asyncio.sleep(0.05)


async def run_app(cfg: Config, secrets: Secrets, *, fake: bool, headless: bool, port: int) -> None:
    """Wire every unit together and run until cancelled (or Ctrl+C)."""
    data_dir = repo_path(cfg.files.data_dir)
    if fake:
        data_dir = data_dir / "fake"  # fake pictures never mix with real ones
    store = Store(data_dir)
    bus, hub = EventBus(), Hub()
    blocklist = Blocklist.from_file(repo_path(cfg.files.blocklist))

    async with contextlib.AsyncExitStack() as stack:
        if fake:
            llm = FakeHappyLlm()
            images = FakeImages(delay_s=FAKE_IMAGE_DELAY_S)
            voice = FakeVoice(cfg.preset_ideas, repeat=True)
            uploader = FakeUploader(store)
        else:
            if not (secrets.gallery_url and secrets.gallery_token):
                log.warning("GALLERY_URL or GALLERY_TOKEN is not set: uploads will keep retrying")
            llm = make_llm_client(cfg, secrets)
            images = make_image_service(cfg, secrets)
            google = genai.Client(api_key=secrets.google_api_key)
            voice = Voice(google, cfg.models.stt, bus)
            http = await stack.enter_async_context(httpx.AsyncClient(timeout=UPLOAD_HTTP_TIMEOUT_S))
            uploader = Uploader(store, secrets.gallery_url, secrets.gallery_token, http)
        slideshow = Slideshow(
            None if fake else http, secrets.gallery_url, hub, SLIDESHOW_INTERVAL_S,
            fetch_images=local_images_source(store) if fake else None,
            signup_qr=lambda: signup_qr_svg(cfg),
            picture_qr=lambda id: picture_qr_svg(secrets.gallery_url, id),
        )

        def takehome(rec):
            slideshow.refresh_soon()  # the new picture joins the idle slideshow soon
            return build_takehome(rec, cfg, secrets)

        pw = await stack.enter_async_context(async_playwright())
        browser = await _launch_tv_browser(pw, cfg, headless)
        tasks: list[asyncio.Task] = []
        server: uvicorn.Server | None = None
        server_task: asyncio.Task | None = None
        key_input: KeyInput | None = None
        try:
            page = browser.pages[0] if browser.pages else await browser.new_page()
            safety = Safety(blocklist, llm, cfg.limits.max_request_chars, cfg.preset_tweaks)
            show = Show(
                cfg, bus, hub, store, safety,
                LlmAgent(llm, cfg.limits.max_tool_calls), ScriptedAgent(),
                Studio(page), images, uploader, voice,
                takehome=takehome,
            )
            checks = fake_checks() if fake else make_checks(cfg, secrets, http, google)
            preflight = PreflightDeps(
                cfg, secrets, checks, llm, None if fake else http, uploader,
                None if fake else google, bus, show,
            )
            app = create_app(
                hub, bus, store, StallControls(show, store, uploader, lambda: slideshow.leaderboard),
                check_runner=make_runner(preflight, fake=fake),
            )
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            )
            server_task = asyncio.create_task(server.serve())
            await _wait_started(server, server_task)

            base = f"http://127.0.0.1:{port}"
            await page.goto(f"{base}/tv")
            if not headless:
                await asyncio.to_thread(webbrowser.open, f"{base}/organiser")
            log.info("Stall running at %s/organiser%s", base, " (fake mode)" if fake else "")

            if not headless:  # a global key listener needs a display; tests and CI have none
                key_input = KeyInput(
                    cfg.button.key,
                    TapDetector(cfg.button.hold_threshold_s, cfg.button.tap_window_s),
                    bus,
                    asyncio.get_running_loop(),
                )
                key_input.start()  # ValueError for a bad key name; other failures only warn

            winner = WinnerScreen(
                cfg.voting.close_at, slideshow, hub,
                is_idle=lambda: show.state == "idle" and not show.paused and show.online,
            )
            health = Health(
                checks,
                hub, lambda ok: setattr(show, "online", ok),
                llm_active=lambda: getattr(llm, "active", None) or getattr(llm, "model", None),
                is_paused=lambda: show.paused, queue_size=uploader.queue_size,
            )
            tasks = [
                asyncio.create_task(show.run_forever(), name="show"),
                asyncio.create_task(health.run_forever(), name="health"),
                asyncio.create_task(uploader.run_forever(), name="uploader"),
                asyncio.create_task(slideshow.run_forever(), name="slideshow"),
                asyncio.create_task(winner.run_forever(), name="winner"),
            ]
            done, _ = await asyncio.wait(
                [server_task, *tasks], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not server_task and task.exception() is not None:
                    raise task.exception()
        finally:
            if key_input is not None:
                key_input.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await _close_quietly(browser)  # before the server, so the TV page goes first
            if server is not None and server_task is not None:
                server.should_exit = True
                _, pending = await asyncio.wait([server_task], timeout=SERVER_STOP_TIMEOUT_S)
                for task in pending:
                    task.cancel()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m kiosk", description="Run the AI image stall.")
    parser.add_argument("--fake", action="store_true",
                        help="fake LLM, images, voice and uploader: no keys, no network")
    parser.add_argument("--headless", action="store_true",
                        help="run Chromium without a window and open no organiser tab")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # no per-request lines

    cfg = load_config(REPO_ROOT / "config.toml")
    secrets = fake_secrets() if args.fake else load_secrets()
    try:
        asyncio.run(run_app(cfg, secrets, fake=args.fake, headless=args.headless,
                            port=cfg.display.port))
    except KeyboardInterrupt:
        log.info("Stopped")


if __name__ == "__main__":
    main()

import asyncio
import socket
import threading
import time

import pytest
import uvicorn
from playwright.async_api import async_playwright

from kiosk.events import EventBus
from kiosk.fakes import FakeStudio, _draw_jpeg
from kiosk.store import Store
from kiosk.studio import Studio
from kiosk.web.server import Hub, create_app
from tests.test_server import FakeControls

class RunningApp:
    """The real app served by uvicorn on a background thread."""

    def __init__(self, tmp_path):
        self.hub = Hub()
        app = create_app(self.hub, EventBus(), Store(tmp_path), FakeControls())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=asyncio.run, args=(self._serve(),), daemon=True)

    async def _serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.server.serve()

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)

    def send(self, msg: dict) -> None:
        """Send through the Hub on the server's own loop (its sockets live there)."""
        asyncio.run_coroutine_threadsafe(self.hub.send(msg), self.loop).result(timeout=5)


@pytest.fixture
def app(tmp_path):
    running = RunningApp(tmp_path)
    running.start()
    yield running
    running.stop()


async def _launch(pw):
    try:
        return await pw.chromium.launch()
    except Exception as exc:  # Chromium not installed
        pytest.skip(f"Chromium not available: {exc}")


@pytest.mark.browser
async def test_types_generates_and_shows_image(app):
    async with async_playwright() as pw:
        browser = await _launch(pw)
        try:
            page = await browser.new_page(viewport={"width": 1920, "height": 1080})
            await page.goto(f"{app.url}/tv")
            # The TV only shows the studio frame in the agent and tweak states.
            await page.wait_for_function("window.WebSocket !== undefined")
            for _ in range(50):
                app.send({"type": "state", "state": "agent", "mode": "kid"})
                if await page.locator("#studio").is_visible():
                    break
                await page.wait_for_timeout(100)

            studio = Studio(page)
            await studio.open()
            await studio.type("a dragon", delay_ms=5)
            await studio.click_generate()

            frame = page.frame_locator("#studio")
            assert await frame.locator("#prompt").input_value() == "a dragon"
            assert await frame.locator("#busy").is_visible()

            await studio.show_image(_draw_jpeg("a dragon"))
            src = await frame.locator("#result").get_attribute("src")
            assert src.startswith("data:image/jpeg;base64,")
            assert await frame.locator("#busy").is_hidden()
            assert await frame.locator("#prompt").input_value() == "a dragon"

            await studio.reset()
            assert await frame.locator("#prompt").input_value() == ""
            assert await frame.locator("#result").is_hidden()
            assert await frame.locator("#busy").is_hidden()
        finally:
            await browser.close()


async def test_fake_studio_records_calls_in_order():
    fake = FakeStudio()
    await fake.open()
    await fake.type("a dragon")
    await fake.click_generate()
    await fake.show_image(b"jpeg")
    await fake.reset()
    assert fake.calls == [
        ("open",),
        ("type", "a dragon"),
        ("click_generate",),
        ("show_image", b"jpeg"),
        ("reset",),
    ]

"""Drives the studio page on the TV so visitors watch the agent type and click."""
from __future__ import annotations

import base64

from playwright.async_api import Page

_FRAME_READY_TIMEOUT_MS = 10_000


class Studio:
    def __init__(self, page: Page, frame_selector: str = "#studio"):
        self._frame = page.frame_locator(frame_selector)
        self._prompt = self._frame.locator("#prompt")

    async def open(self) -> None:
        """Wait for the studio frame to be showing, then focus the prompt box."""
        await self._prompt.wait_for(state="visible", timeout=_FRAME_READY_TIMEOUT_MS)
        await self._prompt.focus()

    async def type(self, text: str, delay_ms: int = 35) -> None:
        """Replace the prompt box text. Clearing first means a resumed agent that
        types again (after the agent cap) never shows the prompt twice."""
        await self._prompt.fill("")
        await self._prompt.press_sequentially(text, delay=delay_ms)

    async def click_generate(self) -> None:
        await self._frame.locator("#generate").click()

    async def show_image(self, jpeg: bytes) -> None:
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        # evaluate() runs inside the studio frame, so `window` is the studio's window.
        await self._frame.locator("#result").evaluate(
            "(_, url) => window.showImage(url)", data_url
        )

    async def reset(self) -> None:
        await self._frame.locator("#result").evaluate("() => window.resetStudio()")

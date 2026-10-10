"""Test doubles shared across the kiosk tests."""
from __future__ import annotations

import asyncio
import io
import textwrap

from PIL import Image, ImageDraw

from kiosk.llm import LlmResponse


class FakeLlm:
    """Scripted stand-in for LlmClient. Each respond() pops the next item;
    an Exception item is raised instead of returned."""

    def __init__(self, script: list[LlmResponse | Exception]):
        self._script = list(script)
        self.calls: list[dict] = []

    async def respond(
        self,
        *,
        instructions: str,
        input: list[dict] | str,
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        tool_choice: str = "auto",
    ) -> LlmResponse:
        self.calls.append(
            {
                "instructions": instructions,
                "input": input,
                "tools": tools,
                "json_schema": json_schema,
                "tool_choice": tool_choice,
            }
        )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeImages:
    """Stand-in for ImageService. Returns a 1024x1024 JPEG with the prompt
    drawn on it, records calls, and can be told to fail the next call."""

    model = "fake-image-model"

    def __init__(self, delay_s: float = 0.0):
        self.delay_s = delay_s
        self.calls: list[dict] = []
        self._fail_next: Exception | None = None

    def fail_next(self, exc: Exception) -> None:
        self._fail_next = exc

    async def generate(self, prompt: str) -> bytes:
        self.calls.append({"kind": "generate", "prompt": prompt})
        return await self._respond(prompt)

    async def edit(self, jpeg: bytes, instruction: str) -> bytes:
        self.calls.append({"kind": "edit", "jpeg": jpeg, "instruction": instruction})
        return await self._respond(instruction)

    async def _respond(self, text: str) -> bytes:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self._fail_next is not None:
            exc, self._fail_next = self._fail_next, None
            raise exc
        return _draw_jpeg(text)


def _draw_jpeg(text: str) -> bytes:
    img = Image.new("RGB", (1024, 1024), "#335577")
    draw = ImageDraw.Draw(img)
    lines = textwrap.wrap(text, width=30) or [""]
    y = 40
    for line in lines[:20]:
        draw.text((40, y), line, fill="white")
        y += 20
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


class FakeStudio:
    """Stand-in for Studio. Records every call in order as a tuple:
    ("open",), ("type", text), ("click_generate",), ("show_image", jpeg), ("reset",)."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def open(self) -> None:
        self.calls.append(("open",))

    async def type(self, text: str, delay_ms: int = 35) -> None:
        self.calls.append(("type", text))

    async def click_generate(self) -> None:
        self.calls.append(("click_generate",))

    async def show_image(self, jpeg: bytes) -> None:
        self.calls.append(("show_image", jpeg))

    async def reset(self) -> None:
        self.calls.append(("reset",))

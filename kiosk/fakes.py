"""Test doubles shared across the kiosk tests."""
from __future__ import annotations

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

"""Test doubles shared across the kiosk tests."""
from __future__ import annotations

import asyncio
import io
import itertools
import json
import textwrap

from PIL import Image, ImageDraw

from kiosk.llm import LlmResponse, ToolCall


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


class FakeVoice:
    """Stand-in for Voice. stop() returns the next transcript from the list
    given at construction, or "" once it runs out. With repeat=True the list
    loops forever. Records "start"/"stop" calls."""

    def __init__(self, transcripts: list[str] | None = None, repeat: bool = False):
        self._transcripts = list(transcripts or [])
        self._repeat = repeat
        self.calls: list[str] = []

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> str:
        self.calls.append("stop")
        if not self._transcripts:
            return ""
        text = self._transcripts.pop(0)
        if self._repeat:
            self._transcripts.append(text)
        return text


FAKE_STYLE = ", bright, colourful, family-friendly illustration"
FAKE_TWEAKS = ["Add a party hat", "Make it night time", "Add a friendly kiwi", "Make it snowy"]
_FAKE_STEPS = ["open_studio", "type_prompt", "generate_image", "offer_tweaks", "apply_tweak", "finish"]
_FAKE_NARRATION = {
    "open_studio": "Let's open the picture studio!",
    "type_prompt": "Typing your idea in…",
    "generate_image": "Pressing Generate. Here it comes!",
    "offer_tweaks": "Want to change it? Pick a tweak!",
    "apply_tweak": "Adding your tweak…",
    "finish": "All done. Enjoy your picture!",
}


class FakeHappyLlm:
    """A looping happy-path LLM for `python -m kiosk --fake`. No network.

    Safety calls (json_schema) always approve: a request becomes
    request + FAKE_STYLE, and every tweak is safe. Agent calls (tools) answer
    with the next tool for the conversation so far: open_studio, type_prompt,
    generate_image, offer_tweaks (FAKE_TWEAKS), apply_tweak (the visitor's
    choice, skipped if they kept the picture), then finish. Each agent run
    starts a fresh conversation, so the script loops for every visitor.
    """

    model = "fake-llm"

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    async def respond(
        self,
        *,
        instructions: str,
        input: list[dict] | str,
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        tool_choice: str = "auto",
    ) -> LlmResponse:
        if json_schema is not None:
            return LlmResponse(text=json.dumps(self._judge(json_schema, input)), model=self.model)
        return self._next_tool(input if isinstance(input, list) else [])

    @staticmethod
    def _judge(schema: dict, input: list[dict] | str) -> dict:
        if "safe" in schema.get("properties", {}):
            return {"safe": [True] * len(FAKE_TWEAKS)}
        request = json.loads(input)["request"] if isinstance(input, str) else ""
        return {"allowed": True, "reason": "ok", "image_prompt": request + FAKE_STYLE}

    def _next_tool(self, history: list[dict]) -> LlmResponse:
        calls = [i for i in history if i.get("type") == "function_call"]
        outputs = {i.get("call_id"): i.get("output") for i in history
                   if i.get("type") == "function_call_output"}
        done = [c["name"] for c in calls]
        chosen = "keep"
        for c in calls:
            if c["name"] == "offer_tweaks" and c["call_id"] in outputs:
                chosen = json.loads(outputs[c["call_id"]]).get("chosen", "keep")
        steps = [s for s in _FAKE_STEPS if not (s == "apply_tweak" and chosen == "keep")]
        name = next((s for s in steps if s not in done), "finish")

        args: dict = {"narration": _FAKE_NARRATION[name]}
        if name == "type_prompt":
            first = next((i for i in history if i.get("role") == "user"), None)
            args["text"] = json.loads(first["content"])["image_prompt"] if first else ""
        elif name == "offer_tweaks":
            args["options"] = list(FAKE_TWEAKS)
        elif name == "apply_tweak":
            args["option"] = chosen

        call_id = f"fake_{next(self._ids)}"
        item = {"type": "function_call", "call_id": call_id, "name": name,
                "arguments": json.dumps(args)}
        return LlmResponse(text="", tool_calls=[ToolCall(call_id, name, args)],
                           output_items=[item], model=self.model)


class FakeUploader:
    """Stand-in for Uploader: marks records uploaded at once. No network."""

    def __init__(self, store):
        self._store = store
        self.deleted: list[str] = []

    def enqueue(self, rec) -> None:
        self._store.mark_uploaded(rec.id)

    async def upload_once(self, rec) -> bool:
        self._store.mark_uploaded(rec.id)
        return True

    async def run_forever(self) -> None:
        for rec in self._store.pending_uploads():
            self._store.mark_uploaded(rec.id)
        await asyncio.Event().wait()

    async def delete(self, id: str) -> bool:
        self.deleted.append(id)
        return True

    def queue_size(self) -> int:
        return len(self._store.pending_uploads())

"""Show controller tests. Every collaborator is a fake; every timeout is 0.05 s.

Each test drives one visitor with `drive(h, steps)`. A step is:
  - an Event, put on the bus;
  - a str, which waits for the next `state` message with that state;
  - a dict, which waits for the next hub message equal to it;
  - a float, which sleeps.
"""
import asyncio
import contextlib
import itertools
import json
from dataclasses import fields, replace
from pathlib import Path

import pytest

from kiosk.agent import FIXED_NARRATION, LlmAgent, ScriptedAgent
from kiosk.config import Timeouts, load_config
from kiosk.events import Event, EventBus
from kiosk.fakes import FakeImages, FakeLlm, FakeStudio, FakeVoice
from kiosk.images import ImageBlocked, ImageFailed
from kiosk.llm import LlmResponse, LlmUnavailable, ToolCall
from kiosk.safety import SafetyResult
from kiosk.show import Show
from kiosk.store import Store

PROMPT = "A friendly dragon on a bike, bright, colourful, family-friendly illustration"
OPTIONS = ["Add a hat", "Make it night", "Add a kiwi", "Make it snowy"]
PRESET_TWEAKS = ["preset one", "preset two", "preset three", "preset four"]
ALLOWED = SafetyResult(True, "fine", PROMPT)
BLOCKED = SafetyResult(False, "not allowed", None)
STEP_TIMEOUT_S = 2.0


def make_cfg(**timeouts):
    base = load_config(Path(__file__).resolve().parents[1] / "config.toml")
    fast = {f.name: 0.05 for f in fields(Timeouts)}
    # The agent cap covers the visitor's tweak wait, so it must be longer than
    # tweak_s or every LLM run would hit the cap during the tweak.
    fast["agent_cap_s"] = 1.0
    fast.update(timeouts)
    return replace(base, timeouts=replace(base.timeouts, **fast))


class RecordingHub:
    def __init__(self):
        self.msgs: list[dict] = []
        self._cursor = 0

    async def send(self, msg: dict) -> None:
        self.msgs.append(msg)

    async def wait_for(self, match) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STEP_TIMEOUT_S
        while loop.time() < deadline:
            for i in range(self._cursor, len(self.msgs)):
                if match(self.msgs[i]):
                    self._cursor = i + 1
                    return self.msgs[i]
            await asyncio.sleep(0.002)
        raise AssertionError(f"timed out waiting; messages: {self.msgs}")

    def states(self) -> list[str]:
        return [m["state"] for m in self.msgs if m["type"] == "state"]

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.msgs if m["type"] == kind]

    def texts(self, kind: str) -> list[str]:
        return [m["text"] for m in self.of(kind)]


class FakeSafety:
    """Returns queued results (ALLOWED once they run out); an Exception item is raised."""

    def __init__(self, results=()):
        self.results = list(results)
        self.requests: list[tuple[str, str]] = []

    async def check_request(self, text: str, mode: str) -> SafetyResult:
        self.requests.append((text, mode))
        item = self.results.pop(0) if self.results else ALLOWED
        if isinstance(item, Exception):
            raise item
        return item

    async def check_tweaks(self, options: list[str]) -> list[str]:
        return list(options) if len(options) == 4 else list(PRESET_TWEAKS)


class ListUploader:
    def __init__(self):
        self.recs = []

    def enqueue(self, rec) -> None:
        self.recs.append(rec)


class HangingLlm:
    def __init__(self):
        self.calls = 0

    async def respond(self, **kwargs):
        self.calls += 1
        await asyncio.sleep(3600)


class BrokenStudio(FakeStudio):
    async def open(self) -> None:
        self.calls.append(("open",))
        raise RuntimeError("boom")


_ids = itertools.count(1)


def turn(name: str, **args) -> LlmResponse:
    c = ToolCall(call_id=f"c{next(_ids)}", name=name, arguments={**args, "narration": f"LLM {name}"})
    item = {"type": "function_call", "call_id": c.call_id, "name": name,
            "arguments": json.dumps(c.arguments)}
    return LlmResponse(text="", tool_calls=[c], output_items=[item], model="fake")


def happy_script() -> list[LlmResponse]:
    # apply_tweak is rejected harmlessly if the visitor kept the picture.
    return [
        turn("open_studio"),
        turn("type_prompt", text="whatever"),
        turn("generate_image"),
        turn("offer_tweaks", options=OPTIONS),
        turn("apply_tweak", option="whatever"),
        turn("finish"),
    ]


def takehome_for(rec) -> dict:
    return {"type": "takehome", "image_url": f"/local/{rec.id}.jpg", "qr_svg": "<svg/>",
            "claim": rec.claim_code, "signup_qr_svg": ""}


class Harness:
    def __init__(self, tmp_path, *, cfg=None, llm=None, safety=None, transcripts=(),
                 images=None, studio=None):
        self.cfg = cfg or make_cfg()
        self.bus = EventBus()
        self.hub = RecordingHub()
        self.store = Store(tmp_path / "data")
        self.safety = safety or FakeSafety()
        self.llm = llm if llm is not None else FakeLlm(happy_script())
        self.images = images or FakeImages()
        self.studio = studio or FakeStudio()
        self.uploader = ListUploader()
        self.voice = FakeVoice(list(transcripts))
        self.show = Show(
            self.cfg, self.bus, self.hub, self.store, self.safety,
            LlmAgent(self.llm, self.cfg.limits.max_tool_calls), ScriptedAgent(),
            self.studio, self.images, self.uploader, self.voice, takehome_for, poll_s=0.01,
        )

    def studio_kinds(self) -> list[str]:
        return [c[0] for c in self.studio.calls]


async def feed(h: Harness, steps) -> None:
    for step in steps:
        if isinstance(step, Event):
            h.bus.put(step)
            await asyncio.sleep(0.005)
        elif isinstance(step, str):
            await h.hub.wait_for(lambda m, s=step: m["type"] == "state" and m["state"] == s)
        elif isinstance(step, dict):
            await h.hub.wait_for(lambda m, d=step: m == d)
        else:
            await asyncio.sleep(step)


async def drive(h: Harness, steps, *, forever: bool = False) -> RecordingHub:
    """Feed steps while the show runs. One visitor turn by default (awaited to the
    end); with forever=True, run_forever is cancelled once the steps are done."""
    task = asyncio.create_task(h.show.run_forever() if forever else h.show.run_visitor())
    try:
        await feed(h, steps)
        if not forever:
            await asyncio.wait_for(task, STEP_TIMEOUT_S)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    return h.hub


async def bus_is_empty(bus: EventBus) -> bool:
    return await bus.next(0.01) is None


def typed(text: str) -> Event:
    return Event("typed_request", text=text)


MSG_SORRY = {"type": "message", "text": "Let's try something else!"}


async def test_typed_happy_path_reaches_takehome_and_enqueues(tmp_path):
    h = Harness(tmp_path)
    hub = await drive(h, [typed("a dragon on a bike")])

    assert hub.states() == ["idle", "confirm", "filter", "agent", "tweak", "agent", "takehome"]
    assert {"type": "state", "state": "confirm", "mode": "kid"} in hub.msgs
    assert {"type": "caption", "text": "a dragon on a bike", "final": True} in hub.msgs
    assert h.safety.requests == [("a dragon on a bike", "kid")]
    assert hub.of("tweaks") == [{"type": "tweaks", "options": OPTIONS}]

    [rec] = h.uploader.recs
    assert rec.mode == "kid" and rec.prompt == PROMPT
    assert h.store.get(rec.id) is not None
    assert h.store.image_path(rec.id).read_bytes()[:2] == b"\xff\xd8"
    assert hub.of("takehome") == [takehome_for(rec)]
    assert {"type": "image", "url": f"/local/{rec.id}.jpg"} in hub.msgs

    assert "LLM open_studio" in hub.texts("narration")
    assert hub.of("tool") == [] and hub.of("prompt") == []  # kid mode
    assert h.studio_kinds()[-1] == "reset"
    assert await bus_is_empty(h.bus)


async def test_request_truncated_before_safety(tmp_path):
    h = Harness(tmp_path)
    await drive(h, [typed("x" * 500)])
    [(text, _)] = h.safety.requests
    assert len(text) == h.cfg.limits.max_request_chars


async def test_choose_mode_timeout_defaults_kid(tmp_path):
    h = Harness(tmp_path)
    hub = await drive(h, [Event("taps", n=1), "choose_mode", "listen"])
    assert {"type": "state", "state": "listen", "mode": "kid"} in hub.msgs
    # Nobody holds the button, so the turn ends back at idle.
    assert hub.states() == ["idle", "choose_mode", "listen"]
    assert h.voice.calls == []


async def test_hold_selects_kid(tmp_path):
    h = Harness(tmp_path)
    hub = await drive(h, [Event("hold_start"), "choose_mode", Event("hold_end"), "listen"])
    assert {"type": "state", "state": "listen", "mode": "kid"} in hub.msgs


async def test_double_tap_selects_nerd_and_sends_prompt_and_tool_messages(tmp_path):
    h = Harness(tmp_path)
    hub = await drive(h, [
        Event("taps", n=1), "choose_mode", Event("taps", n=2), "listen", typed("a robot"),
    ])
    assert {"type": "state", "state": "listen", "mode": "nerd"} in hub.msgs
    assert h.safety.requests == [("a robot", "nerd")]
    # The harness LLM is a FakeLlm with no `active` name, so the LLM shows as "scripted".
    assert hub.of("prompt") == [
        {"type": "prompt", "text": PROMPT, "model": FakeImages.model, "llm": "scripted"}
    ]
    tools = hub.of("tool")
    assert [t["name"] for t in tools] == ["open_studio", "type_prompt", "generate_image",
                                         "offer_tweaks", "finish"]
    assert tools[1]["args"]["text"] == PROMPT
    assert "LLM finish" in hub.texts("narration")
    assert h.uploader.recs[0].mode == "nerd"


class NamedLlm(FakeLlm):
    active = "fake-active-llm"


async def test_nerd_mode_sends_prompt_with_models(tmp_path):
    h = Harness(tmp_path, llm=NamedLlm(happy_script()))
    hub = await drive(h, [
        Event("taps", n=1), "choose_mode", Event("taps", n=2), "listen", typed("a robot"),
    ])
    assert hub.of("prompt") == [
        {"type": "prompt", "text": PROMPT, "model": FakeImages.model, "llm": "fake-active-llm"}
    ]


async def test_nerd_prompt_says_scripted_when_llm_is_skipped(tmp_path):
    h = Harness(tmp_path, llm=NamedLlm(happy_script()), safety=FakeSafety([LlmUnavailable("down")]))
    hub = await drive(h, [
        Event("taps", n=1), "choose_mode", Event("taps", n=2), "listen", typed("a robot"),
    ])
    assert [m["llm"] for m in hub.of("prompt")] == ["scripted"]


async def test_kid_mode_sends_no_tool_messages(tmp_path):
    h = Harness(tmp_path, llm=NamedLlm(happy_script()))
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen", typed("a robot"),
    ])
    assert {"type": "state", "state": "listen", "mode": "kid"} in hub.msgs
    assert hub.of("tool") == [] and hub.of("prompt") == []
    assert h.uploader.recs[0].mode == "kid"


async def test_voice_path_uses_transcript(tmp_path):
    h = Harness(tmp_path, transcripts=["a dragon on a bike"])
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), Event("hold_end"), "confirm", Event("taps", n=1),
    ])
    assert h.voice.calls == ["start", "stop"]
    assert {"type": "caption", "text": "a dragon on a bike", "final": True} in hub.msgs
    assert h.safety.requests == [("a dragon on a bike", "kid")]
    assert hub.states()[-1] == "takehome"


async def test_partial_captions_forwarded(tmp_path):
    h = Harness(tmp_path, transcripts=["a dragon"])
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), Event("caption", text="a dra"), Event("hold_end"), "confirm",
    ])
    captions = hub.of("caption")
    assert captions[:2] == [
        {"type": "caption", "text": "a dra", "final": False},
        {"type": "caption", "text": "a dragon", "final": True},
    ]


async def test_empty_transcript_retry_then_ask_organiser(tmp_path):
    h = Harness(tmp_path, transcripts=[])  # every stop() returns ""
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        # First try: the speech service reports an error mid-hold.
        Event("hold_start"), Event("stt_error"),
        {"type": "message", "text": "I didn't catch that."}, "listen",
        # Second try: an empty transcript.
        Event("hold_start"), Event("hold_end"),
        {"type": "message", "text": "Ask the organiser to type it"},
        typed("a kiwi on a skateboard"), "confirm",
    ])
    assert h.voice.calls == ["start", "stop", "start", "stop"]
    assert h.safety.requests == [("a kiwi on a skateboard", "kid")]
    assert hub.states()[-1] == "takehome"


async def test_ask_organiser_times_out_to_idle(tmp_path):
    h = Harness(tmp_path, transcripts=[])
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), Event("hold_end"), "listen",
        Event("hold_start"), Event("hold_end"),
        {"type": "message", "text": "Ask the organiser to type it"},
    ])
    assert h.safety.requests == []
    assert "confirm" not in hub.states()


async def test_confirm_hold_records_again(tmp_path):
    h = Harness(tmp_path, transcripts=["a dragn", "a dragon"])
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), Event("hold_end"), "confirm",
        Event("hold_start"), "listen", Event("hold_end"), "confirm",
    ])
    assert h.voice.calls == ["start", "stop", "start", "stop"]
    assert h.safety.requests == [("a dragon", "kid")]


async def test_blocked_twice_uses_preset_idea(tmp_path):
    h = Harness(tmp_path, safety=FakeSafety([BLOCKED, BLOCKED]))
    hub = await drive(h, [typed("bad one"), MSG_SORRY, "listen", typed("bad two")])
    assert [r for r, _ in h.safety.requests] == ["bad one", "bad two"]
    assert hub.states()[-1] == "takehome"
    prompt = h.images.calls[0]["prompt"]
    assert prompt in h.cfg.preset_ideas
    assert h.uploader.recs[0].prompt == prompt


async def test_llm_unavailable_uses_preset_and_scripted(tmp_path):
    h = Harness(tmp_path, safety=FakeSafety([LlmUnavailable("down")]), llm=FakeLlm([]))
    hub = await drive(h, [typed("a dragon")])
    assert h.llm.calls == []
    assert h.images.calls[0]["prompt"] in h.cfg.preset_ideas
    assert FIXED_NARRATION["open_studio"] in hub.texts("narration")
    assert hub.of("tweaks") == [{"type": "tweaks", "options": PRESET_TWEAKS}]
    assert hub.states()[-1] == "takehome"


async def test_agent_timeout_falls_back_to_scripted(tmp_path):
    h = Harness(tmp_path, cfg=make_cfg(agent_cap_s=0.1), llm=HangingLlm())
    hub = await drive(h, [typed("a dragon")])
    assert h.llm.calls == 1
    assert hub.texts("narration")[0] == FIXED_NARRATION["open_studio"]
    assert h.images.calls[0]["prompt"] == PROMPT
    assert len(h.uploader.recs) == 1


async def test_agent_gave_up_falls_back_to_scripted_with_same_ctx(tmp_path):
    llm = FakeLlm([turn("open_studio"), LlmUnavailable("down")])
    h = Harness(tmp_path, llm=llm)
    hub = await drive(h, [typed("a dragon")])
    # The scripted agent resumes: the studio is opened once, not twice.
    assert h.studio_kinds().count("open") == 1
    assert "LLM open_studio" in hub.texts("narration")
    assert FIXED_NARRATION["type_prompt"] in hub.texts("narration")
    assert len(h.uploader.recs) == 1


async def test_image_blocked_counts_as_blocked(tmp_path):
    first = [turn("open_studio"), turn("type_prompt", text="x"), turn("generate_image")]
    images = FakeImages()
    images.fail_next(ImageBlocked("no"))
    h = Harness(tmp_path, llm=FakeLlm(first + happy_script()), images=images,
                safety=FakeSafety([ALLOWED, BLOCKED]))
    hub = await drive(h, [typed("a dragon"), MSG_SORRY, "listen", typed("a bad one")])
    # Image block (1) + safety block (2) reaches the limit, so a preset idea is used.
    assert images.calls[0]["prompt"] == PROMPT
    assert images.calls[1]["prompt"] in h.cfg.preset_ideas
    # The studio was reset before the second attempt typed into it.
    kinds = h.studio_kinds()
    assert kinds.index("reset") < kinds.index("open", kinds.index("reset"))
    assert hub.states()[-1] == "takehome"


async def test_image_failed_shows_tired_and_resets(tmp_path):
    images = FakeImages()
    images.fail_next(ImageFailed("down"))
    h = Harness(tmp_path, images=images)
    hub = await drive(h, [typed("a dragon")])
    assert {"type": "message", "text": "The image machine is tired! Try again"} in hub.msgs
    assert "takehome" not in hub.states()
    assert h.uploader.recs == []
    assert h.studio_kinds()[-1] == "reset"


async def test_tweak_taps_three_selects_third(tmp_path):
    h = Harness(tmp_path)
    await drive(h, [typed("a dragon"), "tweak", Event("taps", n=3), "takehome"])
    edits = [c for c in h.images.calls if c["kind"] == "edit"]
    assert [e["instruction"] for e in edits] == [OPTIONS[2]]
    assert OPTIONS[2] in h.uploader.recs[0].prompt


@pytest.mark.parametrize("event,expected", [
    (Event("taps", n=9), OPTIONS[3]),  # capped at 4
    (Event("tweak_choice", n=2), OPTIONS[1]),
    (Event("tweak_choice", n=0), None),  # keep
])
async def test_tweak_choice_events(tmp_path, event, expected):
    h = Harness(tmp_path)
    await drive(h, [typed("a dragon"), "tweak", event, "takehome"])
    edits = [c["instruction"] for c in h.images.calls if c["kind"] == "edit"]
    assert edits == ([expected] if expected else [])


async def test_tweak_timeout_keeps(tmp_path):
    h = Harness(tmp_path)
    hub = await drive(h, [typed("a dragon")])
    assert [c["kind"] for c in h.images.calls] == ["generate"]
    assert h.uploader.recs[0].prompt == PROMPT
    assert hub.states()[-1] == "takehome"


async def test_events_ignored_outside_expected_state(tmp_path):
    h = Harness(tmp_path, images=FakeImages(delay_s=0.1))
    hub = await drive(h, [
        typed("a dragon"), "agent",
        Event("taps", n=3), Event("hold_start"), Event("taps", n=2),  # during generation
        "takehome", Event("taps", n=1), Event("hold_start"),  # during take-home
    ])
    assert hub.states() == ["idle", "confirm", "filter", "agent", "tweak", "agent", "takehome"]
    assert [c["kind"] for c in h.images.calls] == ["generate"]  # taps were not a tweak choice
    assert await bus_is_empty(h.bus)


async def test_long_hold_auto_stops(tmp_path):
    h = Harness(tmp_path, transcripts=["a long story"])
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), "confirm",  # never released
    ])
    assert h.voice.calls == ["start", "stop"]
    assert {"type": "caption", "text": "a long story", "final": True} in hub.msgs


async def test_unexpected_exception_resets_to_idle(tmp_path):
    h = Harness(tmp_path, studio=BrokenStudio())
    hub = await drive(h, [
        typed("a dragon"),
        {"type": "log", "text": "show error in agent: RuntimeError"},
        "idle",
    ], forever=True)
    assert h.studio_kinds()[-1] == "reset"
    assert h.uploader.recs == []
    assert await bus_is_empty(h.bus)


async def test_unexpected_exception_while_recording_stops_voice(tmp_path):
    h = Harness(tmp_path)

    async def broken_stop():
        h.voice.calls.append("stop")
        raise RuntimeError("mic gone")

    h.voice.stop = broken_stop
    hub = await drive(h, [
        Event("hold_start"), "choose_mode", Event("hold_end"), "listen",
        Event("hold_start"), Event("hold_end"),
    ])
    assert {"type": "log", "text": "show error in listen: RuntimeError"} in hub.msgs
    assert h.studio_kinds()[-1] == "reset"


async def test_paused_holds_between_visitors(tmp_path):
    h = Harness(tmp_path, images=FakeImages(delay_s=0.05))
    task = asyncio.create_task(h.show.run_forever())
    try:
        await feed(h, [typed("a dragon"), "agent"])
        await h.show.set_paused(True)  # mid-turn: the visitor still finishes
        await feed(h, ["takehome", "paused"])
        h.bus.put(typed("while paused"))
        await asyncio.sleep(0.1)
        assert h.hub.states()[-1] == "paused"
        assert len(h.safety.requests) == 1
        await h.show.set_paused(False)
        await feed(h, ["idle"])
        await asyncio.sleep(0.05)
        assert h.hub.states()[-1] == "idle"  # the press while paused was dropped
        await feed(h, [typed("next visitor"), "agent"])
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    states = h.hub.states()
    assert states[:8] == ["idle", "confirm", "filter", "agent", "tweak", "agent",
                          "takehome", "paused"]
    assert [r for r, _ in h.safety.requests] == ["a dragon", "next visitor"]
    assert len(h.uploader.recs) == 1


async def test_offline_holds_until_online(tmp_path):
    h = Harness(tmp_path)
    h.show.online = False
    task = asyncio.create_task(h.show.run_forever())
    try:
        await feed(h, ["offline"])
        h.show.online = True
        await feed(h, ["idle", typed("a dragon"), "confirm"])
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_hub_failure_does_not_stop_the_show(tmp_path):
    # The first narration fails, then the error log fails too; the next visitor is fine.
    h = Harness(tmp_path, llm=FakeLlm([turn("open_studio")] + happy_script()))
    real_send = h.hub.send
    failures = {"left": 2}

    async def flaky_send(msg):
        if msg["type"] in ("narration", "log") and failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("hub down")
        await real_send(msg)

    h.hub.send = flaky_send
    await drive(h, [typed("a dragon"), "idle", "idle", typed("again"), "takehome"], forever=True)
    assert failures["left"] == 0
    assert len(h.uploader.recs) == 1

import json

import pytest

from kiosk.agent import (
    TOOLS,
    AgentGaveUp,
    AgentResult,
    Job,
    LlmAgent,
    ScriptedAgent,
    ToolContext,
    instructions_for,
)
from kiosk.blocklist import Blocklist
from kiosk.fakes import FakeImages, FakeLlm, FakeStudio
from kiosk.images import ImageFailed
from kiosk.llm import LlmResponse, LlmUnavailable, ToolCall
from kiosk.safety import Safety

PRESETS = ["preset one", "preset two", "preset three", "preset four"]
PROMPT = "A friendly dragon on a bike, bright, colourful, family-friendly illustration"
OPTIONS = ["Add a hat", "Make it night", "Add a kiwi", "Make it snowy"]


@pytest.fixture
def bl(tmp_path):
    f = tmp_path / "bl.txt"
    f.write_text("poop\n", encoding="utf-8")
    return Blocklist.from_file(f)


class Harness:
    """A ToolContext wired to fakes, recording every callback."""

    def __init__(self, bl, safety_script=(), choice="first"):
        self.studio = FakeStudio()
        self.images = FakeImages()
        self.safety_llm = FakeLlm(list(safety_script))
        self.safety = Safety(bl, self.safety_llm, max_request_chars=200, preset_tweaks=PRESETS)
        self.narrations: list[str] = []
        self.tools: list[tuple[str, dict]] = []
        self.offered: list[list[str]] = []
        self.choice = choice
        self.ctx = ToolContext(
            studio=self.studio,
            images=self.images,
            safety=self.safety,
            narrate=self._narrate,
            on_tool=self._on_tool,
            choose_tweak=self._choose,
        )

    async def _narrate(self, text: str) -> None:
        self.narrations.append(text)

    async def _on_tool(self, name: str, args: dict) -> None:
        self.tools.append((name, args))

    async def _choose(self, options: list[str]) -> str | None:
        self.offered.append(list(options))
        if self.choice == "first":
            return options[0]
        return self.choice

    def studio_kinds(self) -> list[str]:
        return [c[0] for c in self.studio.calls]


def all_safe():
    return LlmResponse(text=json.dumps({"safe": [True, True, True, True]}))


_ids = iter(range(1, 10_000))


def call(name: str, **args) -> ToolCall:
    return ToolCall(call_id=f"c{next(_ids)}", name=name, arguments=args)


def turn(*calls: ToolCall) -> LlmResponse:
    items = [
        {"type": "function_call", "call_id": c.call_id, "name": c.name,
         "arguments": json.dumps(c.arguments)}
        for c in calls
    ]
    return LlmResponse(text="", tool_calls=list(calls), output_items=items, model="fake")


def text_turn(text: str = "Sure thing!") -> LlmResponse:
    item = {"type": "message", "content": [{"type": "output_text", "text": text}]}
    return LlmResponse(text=text, tool_calls=[], output_items=[item], model="fake")


def outputs(llm: FakeLlm, call_index: int) -> dict[str, dict]:
    """function_call_output items in the input of one LLM call, by call_id."""
    items = llm.calls[call_index]["input"]
    return {
        i["call_id"]: json.loads(i["output"])
        for i in items
        if isinstance(i, dict) and i.get("type") == "function_call_output"
    }


def job(mode="kid") -> Job:
    return Job(request="a dragon on a bike", image_prompt=PROMPT, mode=mode)


def happy_script(tweak_option=OPTIONS[0]):
    return [
        turn(call("open_studio", narration="Opening up!")),
        turn(call("type_prompt", text=PROMPT, narration="Typing it in")),
        turn(call("generate_image", narration="Here we go")),
        turn(call("offer_tweaks", options=OPTIONS, narration="Pick a tweak")),
        turn(call("apply_tweak", option=tweak_option, narration="Tweaking")),
        turn(call("finish", narration="Ta-da")),
    ]


async def test_happy_path_calls_tools_in_order(bl):
    h = Harness(bl, [all_safe()])
    llm = FakeLlm(happy_script())
    result = await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)

    assert h.studio_kinds() == ["open", "type", "click_generate", "show_image", "show_image"]
    assert h.studio.calls[1] == ("type", PROMPT)
    kinds = [c["kind"] for c in h.images.calls]
    assert kinds == ["generate", "edit"]
    assert h.images.calls[0]["prompt"] == PROMPT
    assert h.images.calls[1]["instruction"] == OPTIONS[0]
    assert h.narrations == [
        "Opening up!", "Typing it in", "Here we go", "Pick a tweak", "Tweaking", "Ta-da",
    ]
    assert [name for name, _ in h.tools] == [
        "open_studio", "type_prompt", "generate_image", "offer_tweaks", "apply_tweak", "finish",
    ]
    assert isinstance(result, AgentResult)
    assert result.prompt == PROMPT
    assert result.tweak == OPTIONS[0]
    assert result.jpeg == h.studio.calls[-1][1]
    assert h.ctx.done
    # Every call is stateless: the history is threaded through `input`.
    assert all(c["tools"] == TOOLS for c in llm.calls)
    assert list(outputs(llm, 1).values()) == [{"ok": True}]
    offer_out = list(outputs(llm, 4).values())[-1]
    assert offer_out == {"chosen": OPTIONS[0]}


async def test_type_prompt_must_match_job_prompt_or_is_replaced(bl):
    h = Harness(bl, [all_safe()])
    script = happy_script()
    script[1] = turn(call("type_prompt", text="something the safety check never saw",
                          narration="Typing"))
    await LlmAgent(FakeLlm(script), max_tool_calls=10).run(job(), h.ctx)

    typed = [c for c in h.studio.calls if c[0] == "type"]
    assert typed == [("type", PROMPT)]
    assert h.images.calls[0]["prompt"] == PROMPT
    assert h.ctx.typed_prompt == PROMPT
    assert dict(h.tools)["type_prompt"]["text"] == PROMPT


async def test_tool_cap_raises_gave_up(bl):
    h = Harness(bl)
    llm = FakeLlm([turn(call("open_studio", narration="Hi")) for _ in range(11)])
    with pytest.raises(AgentGaveUp):
        await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)
    assert len(llm.calls) == 11


async def test_tool_cap_counts_invalid_calls(bl):
    h = Harness(bl)
    bad = ToolCall(call_id="bad", name="generate_image", arguments={"_invalid": "{oops"})
    llm = FakeLlm([turn(bad, bad, bad, bad, bad), turn(bad, bad, bad, bad, bad), turn(bad)])
    with pytest.raises(AgentGaveUp):
        await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)
    assert h.images.calls == []


async def test_generate_before_type_is_rejected_with_tool_error_then_recovers(bl):
    h = Harness(bl, [all_safe()])
    early = call("generate_image", narration="Too soon")
    script = happy_script()
    script.insert(1, turn(early))
    llm = FakeLlm(script)
    result = await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)

    assert outputs(llm, 2)[early.call_id] == {"error": "type the prompt first"}
    assert "Too soon" not in h.narrations
    assert [c["kind"] for c in h.images.calls] == ["generate", "edit"]
    assert result.prompt == PROMPT


async def test_offer_tweaks_passes_through_safety_check_tweaks(bl):
    unsafe = ["Add a hat", "Make it poop", "Something rude", "Add a kiwi"]
    safety_reply = LlmResponse(text=json.dumps({"safe": [True, True, False, True]}))
    h = Harness(bl, [safety_reply], choice="first")
    script = happy_script()
    script[3] = turn(call("offer_tweaks", options=unsafe, narration="Pick one"))
    # The LLM tries to apply an option the visitor never saw.
    script[4] = turn(call("apply_tweak", option="Make it rude", narration="Tweaking"))
    result = await LlmAgent(FakeLlm(script), max_tool_calls=10).run(job(), h.ctx)

    expected = ["Add a hat", "preset one", "preset two", "Add a kiwi"]
    assert h.offered == [expected]
    # Blocklisted entries are blanked before the safety LLM sees them.
    assert json.loads(h.safety_llm.calls[0]["input"]) == ["Add a hat", "", "Something rude",
                                                          "Add a kiwi"]
    assert h.images.calls[1]["instruction"] == "Add a hat"
    assert result.tweak == "Add a hat"


async def test_choose_tweak_none_means_keep(bl):
    h = Harness(bl, [all_safe()], choice=None)
    script = happy_script()
    rogue = call("apply_tweak", option=OPTIONS[1], narration="Tweaking anyway")
    script[4] = turn(rogue)
    llm = FakeLlm(script)
    result = await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)

    assert list(outputs(llm, 4).values())[-1] == {"chosen": "keep"}
    assert "error" in outputs(llm, 5)[rogue.call_id]
    assert [c["kind"] for c in h.images.calls] == ["generate"]
    assert result.tweak is None
    assert result.jpeg == h.ctx.jpeg


async def test_chosen_option_not_offered_is_treated_as_keep(bl):
    h = Harness(bl, [all_safe()], choice="Something nobody approved")
    script = happy_script()
    del script[4]
    result = await LlmAgent(FakeLlm(script), max_tool_calls=10).run(job(), h.ctx)
    assert [c["kind"] for c in h.images.calls] == ["generate"]
    assert result.tweak is None


async def test_narration_is_capped_and_sanity_checked(bl):
    h = Harness(bl, [all_safe()])
    script = happy_script()
    script[0] = turn(call("open_studio", narration="x" * 500))
    script[1] = turn(call("type_prompt", text=PROMPT, narration="   "))
    script[2] = turn(call("generate_image"))
    script[3] = turn(call("offer_tweaks", options=OPTIONS, narration=42))
    await LlmAgent(FakeLlm(script), max_tool_calls=10).run(job(), h.ctx)
    assert h.narrations[:4] == [
        "x" * 120, "Typing the prompt…", "Making your picture…", "Here are some tweaks…",
    ]


async def test_three_turns_without_tool_calls_gives_up(bl):
    h = Harness(bl)
    llm = FakeLlm([text_turn(), text_turn(), text_turn()])
    with pytest.raises(AgentGaveUp):
        await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)
    assert len(llm.calls) == 3


async def test_llm_unavailable_becomes_gave_up(bl):
    h = Harness(bl)
    agent = LlmAgent(FakeLlm([LlmUnavailable("down")]), max_tool_calls=10)
    with pytest.raises(AgentGaveUp):
        await agent.run(job(), h.ctx)
    assert agent.llm is not None


async def test_image_failed_propagates(bl):
    h = Harness(bl)
    h.images.fail_next(ImageFailed("tired"))
    script = happy_script()[:3]
    with pytest.raises(ImageFailed):
        await LlmAgent(FakeLlm(script), max_tool_calls=10).run(job(), h.ctx)


async def test_scripted_resumes_after_llm_garbage(bl):
    h = Harness(bl)  # check_tweaks([]) returns presets without an LLM call
    llm = FakeLlm(happy_script()[:3] + [text_turn(), text_turn(), text_turn()])
    with pytest.raises(AgentGaveUp):
        await LlmAgent(llm, max_tool_calls=10).run(job(), h.ctx)
    jpeg_from_llm_run = h.ctx.jpeg
    assert jpeg_from_llm_run is not None

    result = await ScriptedAgent().run(job(), h.ctx)

    assert [c["kind"] for c in h.images.calls] == ["generate", "edit"]
    assert h.images.calls[1]["jpeg"] == jpeg_from_llm_run
    assert h.images.calls[1]["instruction"] == PRESETS[0]
    assert h.offered == [PRESETS]
    assert h.studio_kinds() == ["open", "type", "click_generate", "show_image", "show_image"]
    assert h.safety_llm.calls == []
    assert h.narrations[3:] == ["Here are some tweaks…", "Applying your tweak…", "All done!"]
    assert result.prompt == PROMPT
    assert result.tweak == PRESETS[0]
    assert h.ctx.done


async def test_scripted_from_scratch_runs_every_step(bl):
    h = Harness(bl, choice=None)
    result = await ScriptedAgent().run(job(), h.ctx)
    assert h.studio_kinds() == ["open", "type", "click_generate", "show_image"]
    assert h.narrations == [
        "Opening the image tool…", "Typing the prompt…", "Making your picture…",
        "Here are some tweaks…", "All done!",
    ]
    assert result.tweak is None


def test_tools_each_require_narration():
    names = [t["name"] for t in TOOLS]
    assert names == [
        "open_studio", "type_prompt", "generate_image", "offer_tweaks", "apply_tweak", "finish",
    ]
    for tool in TOOLS:
        assert tool["type"] == "function"
        assert "narration" in tool["parameters"]["required"]
    offer = next(t for t in TOOLS if t["name"] == "offer_tweaks")
    options = offer["parameters"]["properties"]["options"]
    assert options["minItems"] == options["maxItems"] == 4


def test_instructions_differ_by_mode():
    kid = instructions_for("kid")
    nerd = instructions_for("nerd")
    assert "simple words" in kid and "creature" in kid
    assert "explain" in nerd
    assert kid != nerd

"""The fakes behind `python -m kiosk --fake` must drive the real agent and safety code."""
import asyncio

from kiosk.agent import LlmAgent, Job, ToolContext
from kiosk.blocklist import Blocklist
from kiosk.fakes import FakeHappyLlm, FakeImages, FakeStudio, FakeUploader, FakeVoice, _draw_jpeg
from kiosk.safety import Safety
from kiosk.store import Store

STYLE = ", bright, colourful, family-friendly illustration"


def safety_with(llm) -> Safety:
    return Safety(Blocklist([]), llm, 200, ["p1", "p2", "p3", "p4"])


async def test_happy_llm_allows_request_and_builds_prompt():
    result = await safety_with(FakeHappyLlm()).check_request("a dragon riding a bike", "kid")
    assert result.allowed
    assert result.image_prompt == "a dragon riding a bike" + STYLE


async def test_happy_llm_says_tweaks_are_safe():
    options = ["a", "b", "c", "d"]
    assert await safety_with(FakeHappyLlm()).check_tweaks(options) == options


async def run_agent(choose):
    llm = FakeHappyLlm()
    studio, images = FakeStudio(), FakeImages()
    offered: list[list[str]] = []

    async def choose_tweak(options):
        offered.append(options)
        return choose(options)

    async def noop(*_):
        return None

    ctx = ToolContext(studio=studio, images=images, safety=safety_with(llm),
                      narrate=noop, on_tool=noop, choose_tweak=choose_tweak)
    job = Job(request="a dragon", image_prompt="a dragon" + STYLE, mode="kid")
    result = await LlmAgent(llm, max_tool_calls=10).run(job, ctx)
    return result, studio, images, offered


async def test_happy_llm_runs_the_whole_agent_with_a_tweak():
    result, studio, images, offered = await run_agent(lambda options: options[1])
    assert len(offered[0]) == 4
    assert result.tweak == offered[0][1]
    assert [c["kind"] for c in images.calls] == ["generate", "edit"]
    assert images.calls[1]["instruction"] == offered[0][1]
    assert ("type", "a dragon" + STYLE) in studio.calls


async def test_happy_llm_skips_apply_when_visitor_keeps():
    result, _, images, _ = await run_agent(lambda options: None)
    assert result.tweak is None
    assert [c["kind"] for c in images.calls] == ["generate"]


async def test_happy_llm_loops_for_the_next_visitor():
    llm = FakeHappyLlm()
    for _ in range(2):
        ctx = ToolContext(studio=FakeStudio(), images=FakeImages(), safety=safety_with(llm),
                          narrate=_anoop, on_tool=_anoop, choose_tweak=_first)
        job = Job(request="x", image_prompt="x" + STYLE, mode="nerd")
        result = await LlmAgent(llm, max_tool_calls=10).run(job, ctx)
        assert result.tweak is not None


async def _anoop(*_):
    return None


async def _first(options):
    return options[0]


async def test_fake_voice_can_repeat():
    voice = FakeVoice(["one", "two"], repeat=True)
    heard = [await voice.stop() for _ in range(5)]
    assert heard == ["one", "two", "one", "two", "one"]


async def test_fake_uploader_marks_uploaded_without_network(tmp_path):
    store = Store(tmp_path)
    old = store.new_record("old", "kid")
    store.save_image(old.id, _draw_jpeg("old"))
    uploader = FakeUploader(store)
    task = asyncio.create_task(uploader.run_forever())
    await asyncio.sleep(0.01)
    assert store.pending_uploads() == []  # resumed on start
    new = store.new_record("new", "kid")
    uploader.enqueue(new)
    assert store.pending_uploads() == []
    assert uploader.queue_size() == 0
    assert await uploader.delete(new.id) is True
    task.cancel()

import json
from types import SimpleNamespace

import pytest

from kiosk.fakes import FakeLlm
from kiosk.llm import Backend, LlmClient, LlmResponse, LlmUnavailable, ToolCall


class FakeResponses:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def backend(name, outcome):
    responses = FakeResponses(outcome)
    client = SimpleNamespace(responses=responses)
    return Backend(name, f"{name}-model", client), responses


def message(text):
    return SimpleNamespace(
        output=[{"type": "message", "content": [{"type": "output_text", "text": text}]}]
    )


def function_call(arguments, name="type_prompt", call_id="c1"):
    return SimpleNamespace(
        output=[
            {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
        ]
    )


def make(primary_outcome, fallback_outcome=None):
    p, pr = backend("openrouter", primary_outcome)
    f, fr = backend("fallback", fallback_outcome if fallback_outcome is not None else message("fb"))
    return LlmClient(p, f, timeout_s=7), pr, fr


async def test_primary_ok():
    client, pr, fr = make(message("hi"))
    out = await client.respond(instructions="be brief", input="hello")
    assert out.text == "hi"
    assert out.model == "openrouter-model"
    assert client.active == "openrouter"
    assert len(pr.calls) == 1 and fr.calls == []
    call = pr.calls[0]
    assert call["model"] == "openrouter-model"
    assert call["instructions"] == "be brief"
    assert call["input"] == "hello"
    assert call["store"] is False
    assert call["timeout"] == 7
    assert "tools" not in call and "text" not in call


async def test_primary_fails_twice_then_fallback():
    client, pr, fr = make(RuntimeError("boom"), message("from fallback"))
    out = await client.respond(instructions="x", input="y")
    assert out.text == "from fallback"
    assert len(pr.calls) == 2
    assert len(fr.calls) == 1
    assert client.active == "fallback"


async def test_primary_retried_first_on_next_call_after_fallback():
    client, pr, fr = make(RuntimeError("boom"))
    await client.respond(instructions="x", input="y")
    assert client.active == "fallback"
    pr.outcome = message("back")
    out = await client.respond(instructions="x", input="y")
    assert out.text == "back"
    assert client.active == "openrouter"
    assert len(pr.calls) == 3


async def test_primary_recovers_on_retry():
    client, pr, fr = make(RuntimeError("blip"))
    outcomes = [RuntimeError("blip"), message("ok")]

    async def create(**kwargs):
        pr.calls.append(kwargs)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    pr.create = create
    out = await client.respond(instructions="x", input="y")
    assert out.text == "ok"
    assert client.active == "openrouter"
    assert fr.calls == []


async def test_both_fail_raises_unavailable():
    client, pr, fr = make(RuntimeError("a"), RuntimeError("b"))
    with pytest.raises(LlmUnavailable):
        await client.respond(instructions="x", input="y")
    assert len(pr.calls) == 2 and len(fr.calls) == 1


async def test_parses_function_calls():
    resp = function_call(json.dumps({"prompt": "a kiwi"}))
    client, _, _ = make(resp)
    out = await client.respond(instructions="x", input="y", tools=[{"type": "function"}])
    assert out.tool_calls == [ToolCall("c1", "type_prompt", {"prompt": "a kiwi"})]
    assert out.output_items == resp.output


async def test_bad_tool_json_becomes_empty_args_with_flag():
    client, _, _ = make(function_call("{not json"))
    out = await client.respond(instructions="x", input="y", tools=[{"type": "function"}])
    assert out.tool_calls[0].arguments == {"_invalid": "{not json"}


async def test_tools_sent_with_tool_choice():
    client, pr, _ = make(message("ok"))
    tools = [{"type": "function", "name": "type_prompt"}]
    await client.respond(instructions="x", input="y", tools=tools)
    assert pr.calls[0]["tools"] == tools
    assert pr.calls[0]["tool_choice"] == "auto"
    await client.respond(instructions="x", input="y", tools=tools, tool_choice="required")
    assert pr.calls[1]["tool_choice"] == "required"


async def test_json_schema_sets_text_format():
    client, pr, _ = make(message("{}"))
    schema = {"type": "object", "properties": {}}
    await client.respond(instructions="x", input="y", json_schema=schema)
    assert pr.calls[0]["text"] == {
        "format": {"type": "json_schema", "name": "out", "schema": schema, "strict": True}
    }


async def test_fake_llm_pops_script_and_records_calls():
    ok = LlmResponse(text="a", tool_calls=[], output_items=[], model="m")
    fake = FakeLlm([ok, RuntimeError("x")])
    assert await fake.respond(instructions="i", input="q") is ok
    with pytest.raises(RuntimeError):
        await fake.respond(instructions="i2", input="q2", tools=[{"t": 1}])
    assert [c["instructions"] for c in fake.calls] == ["i", "i2"]
    assert fake.calls[1]["tools"] == [{"t": 1}]


@pytest.mark.parametrize("which,model", [("openrouter", "openrouter-model"), ("fallback", "fallback-model")])
async def test_probe_calls_one_backend_once_without_failover(which, model):
    client, pr, fr = make(message("ready"), message("ready"))
    out = await client.probe(which, instructions="say ready", input="go")
    assert out.text == "ready" and out.model == model
    called, other = (pr, fr) if which == "openrouter" else (fr, pr)
    assert len(called.calls) == 1 and other.calls == []
    assert called.calls[0]["store"] is False and called.calls[0]["timeout"] == 7
    assert client.active == "openrouter"  # a probe never changes the active backend


async def test_probe_raises_instead_of_failing_over():
    client, pr, fr = make(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await client.probe("openrouter", instructions="x", input="y")
    assert len(pr.calls) == 1 and fr.calls == []

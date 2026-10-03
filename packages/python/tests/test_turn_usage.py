from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from castia import Agent
from castia.building.harness import AgentTestHarness
from castia.inference.model import Model
from castia.runtime.usage import TurnUsage, record_response_usage, usage_scope


def _usage(i, o, *, cached=0, reasoning=0):
    return SimpleNamespace(
        input_tokens=i,
        output_tokens=o,
        total_tokens=i + o,
        input_tokens_details=SimpleNamespace(cached_tokens=cached),
        output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )


def test_usage_scope_aggregates_objects_and_dicts_and_ignores_outside_scope():
    record_response_usage(SimpleNamespace(usage=_usage(100, 100)))
    with usage_scope() as usage:
        record_response_usage(SimpleNamespace(usage=_usage(10, 5, cached=4, reasoning=2)))
        record_response_usage({"usage": {"input_tokens": 3, "output_tokens": 1}})
        record_response_usage(SimpleNamespace(usage=None))
        record_response_usage(None)

    assert usage.to_responses_usage() == {
        "input_tokens": 13,
        "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0},
        "output_tokens": 6,
        "output_tokens_details": {"reasoning_tokens": 2},
        "total_tokens": 19,
    }
    assert usage.calls == 2


def test_usage_scope_is_shared_with_child_tasks():
    async def child():
        record_response_usage(SimpleNamespace(usage=_usage(2, 3)))

    async def run():
        with usage_scope() as usage:
            await asyncio.gather(child(), child())
        return usage

    assert asyncio.run(run()).total_tokens == 10


def test_usage_scope_closed_in_foreign_context_does_not_raise():
    import contextvars

    scope = usage_scope()
    contextvars.copy_context().run(scope.__enter__)
    scope.__exit__(None, None, None)


class _ToolLoopResponses:
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            call = SimpleNamespace(type="function_call", name="echo", arguments="{}", call_id="c1")
            return SimpleNamespace(output=[call], output_text="", usage=_usage(10, 2))
        return SimpleNamespace(output=[], output_text="done", usage=_usage(20, 4))


def _model(responses) -> Model:
    model = Model.__new__(Model)
    model._deployment = "dep"
    model._instructions = None
    model._reasoning = {}
    model._tool_definitions = ()
    model._client = SimpleNamespace(
        get_openai_client=lambda: SimpleNamespace(responses=responses)
    )
    return model


def test_model_tool_loop_records_usage_for_every_call():
    from castia.inference.tools import Tool

    async def echo(activity):
        return {"ok": True}

    tool = Tool(name="echo", description="Echo.", parameters={"type": "object", "properties": {}}, impl=echo)
    model = _model(_ToolLoopResponses())

    async def run():
        with usage_scope() as usage:
            out = await model.respond_with_tools("hi", tools=[tool], activity=object())
        return out, usage

    out, usage = asyncio.run(run())
    assert out == "done"
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens, usage.calls) == (30, 6, 36, 2)


def test_model_stream_records_completed_event_usage():
    class StreamResponses:
        async def create(self, **kwargs):
            async def events():
                yield SimpleNamespace(type="response.output_text.delta", delta="hi")
                yield SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=_usage(7, 1)))

            return events()

    model = _model(StreamResponses())

    async def run():
        with usage_scope() as usage:
            chunks = [delta async for delta in model.stream("x")]
        return chunks, usage

    chunks, usage = asyncio.run(run())
    assert chunks == ["hi"]
    assert usage.total_tokens == 8


def test_prompty_client_proxy_records_non_stream_usage():
    from castia.prompty import _TraceContextResponses

    class Responses:
        async def create(self, **kwargs):
            return SimpleNamespace(usage=_usage(5, 5))

        def sync_create(self, **kwargs):
            return SimpleNamespace(usage=_usage(1, 1))

    proxy = _TraceContextResponses(Responses())

    async def run():
        with usage_scope() as usage:
            await proxy.create(model="m", input="x")
            stream = proxy.create(model="m", input="x", stream=True)
            await stream
        return usage

    assert asyncio.run(run()).total_tokens == 10


def test_responses_body_reports_turn_usage_for_json_and_sse():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        record_response_usage(SimpleNamespace(usage=_usage(11, 4, cached=3, reasoning=1)))
        record_response_usage(SimpleNamespace(usage=_usage(9, 6)))
        return "answer"

    expected = TurnUsage(
        input_tokens=20, output_tokens=10, total_tokens=30, cached_tokens=3, reasoning_tokens=1
    ).to_responses_usage()

    async def run():
        async with AgentTestHarness(app) as test:
            body = (await test.client.post("/responses", json={"input": "q"})).json()
            assert body["usage"] == expected
            stored = (await test.client.get(f"/responses/{body['id']}")).json()
            assert stored["usage"] == expected

            stream = await test.client.post("/responses", json={"input": "q", "stream": True})
            completed = [
                line for line in stream.text.splitlines() if line.startswith("data:") and '"response.completed"' in line
            ]
            assert json.loads(completed[0][len("data:"):])["response"]["usage"] == expected

    asyncio.run(run())

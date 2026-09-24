"""Unit tests for instruction + reasoning-effort threading through ``Model``.

The optimizer tunes an agent's system prompt (and, for the RFT/model-switch
step, points it at a reasoning model), but that only matters if the resolved
``instructions`` and ``reasoning`` kwargs actually reach the Responses API call.
These cases pin that ``respond`` / ``stream`` / ``respond_with_tools`` all pass
``instructions=`` and spread ``**self._reasoning`` into ``responses.create(...)``.
``Model`` is built via ``__new__`` with a fake OpenAI client so no Azure client
or network is touched.
"""

from __future__ import annotations

import asyncio
import copy
from typing import ClassVar

import pytest

from castia.inference.model import (
    Model,
    _instructions_param,
    _reasoning_param,
    _user_message_input,
)
from castia.integrations.toolbox import toolbox_mcp_tool
from castia.observe.tracing import clear_trace_sinks, register_trace_sink


class _Response:
    output_text = "ok"
    output: ClassVar[list] = []


class _FakeResponses:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.clear()
        self._sink.update(kwargs)
        if kwargs.get("stream"):
            return _empty_stream()
        return _Response()


async def _empty_stream():
    return
    yield  # pragma: no cover - makes this an async generator


class _FakeOpenAI:
    def __init__(self, sink: dict) -> None:
        self._responses = _FakeResponses(sink)

    @property
    def responses(self):
        return self._responses


class _FakeClient:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def get_openai_client(self):
        return _FakeOpenAI(self._sink)


def _model(
    instructions: str | None,
    reasoning: dict | None = None,
    tool_definitions: tuple[dict, ...] = (),
) -> tuple[Model, dict]:
    sink: dict = {}
    model = Model.__new__(Model)
    model._deployment = "dep"
    model._instructions = instructions
    model._reasoning = reasoning or {}
    model._tool_definitions = tool_definitions
    model._client = _FakeClient(sink)
    return model, sink


def test_respond_threads_instructions():
    model, sink = _model("be terse")
    out = asyncio.run(model.respond("hi"))
    assert out == "ok"
    assert sink["instructions"] == "be terse"
    assert sink["input"] == _user_message_input("hi")
    assert sink["model"] == "dep"


def test_respond_emits_local_model_trace():
    records = []
    model, _sink = _model("be terse")
    register_trace_sink("memory", records.append)
    try:
        assert asyncio.run(model.respond("hi")) == "ok"
    finally:
        clear_trace_sinks()

    assert [record.name for record in records] == ["castia.model.respond"]
    assert records[0].kind == "model"
    assert records[0].attributes["castia.model.phase"] == "single"
    assert records[0].attributes["gen_ai.request.model"] == "dep"
    assert records[0].attributes["castia.model.output_text_length"] == 2


def test_respond_omits_none_instructions():
    model, sink = _model(None)
    asyncio.run(model.respond("hi"))
    assert "instructions" not in sink


def test_stream_threads_instructions():
    model, sink = _model("stream prompt")

    async def _drain():
        async for _ in model.stream("hi"):  # pragma: no cover - empty stream
            pass

    asyncio.run(_drain())
    assert sink["instructions"] == "stream prompt"
    assert sink["input"] == _user_message_input("hi")
    assert sink["stream"] is True


def test_respond_with_tools_threads_instructions():
    model, sink = _model("tool prompt")
    out = asyncio.run(
        model.respond_with_tools("hi", tools=[], activity=object())
    )
    assert out == "ok"
    assert sink["instructions"] == "tool prompt"
    assert sink["input"] == _user_message_input("hi")


def test_user_message_input_uses_recordable_content_part():
    assert _user_message_input("show me policy") == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "show me policy"}],
        }
    ]


def test_respond_with_tools_applies_toolbox_rewrites_to_extra_specs():
    spec = toolbox_mcp_tool(
        "https://x/mcp",
        server_label="contracts",
        allowed_tools=("knowledge_base_retrieve",),
        descriptions={"knowledge_base_retrieve": "Original guidance."},
    )
    model, sink = _model(
        "tool prompt",
        tool_definitions=(
            {
                "function": {
                    "name": "knowledge_base_retrieve",
                    "description": "Rewritten guidance.",
                }
            },
        ),
    )

    asyncio.run(
        model.respond_with_tools(
            "hi", tools=[], activity=object(), extra_specs=[spec]
        )
    )

    sent_spec = sink["tools"][0]
    assert "x-castia-optimizer-tool-definitions" not in sent_spec
    assert "x-castia-server-description" not in sent_spec
    assert "Rewritten guidance." in sent_spec["server_description"]


def test_respond_with_tools_keeps_recordable_input_and_emits_phase_events(monkeypatch):
    events = []

    class Span:
        def is_recording(self):
            return True

        def add_event(self, name, attributes=None):
            events.append((name, attributes or {}))

    class Tool:
        name = "lookup_policy"

        def spec(self):
            return {"type": "function", "function": {"name": self.name}}

        async def run(self, activity, **kwargs):
            return {"ok": True, "policy": kwargs["policy"]}

    class Responses:
        def __init__(self):
            self.inputs = []

        async def create(self, **kwargs):
            self.inputs.append(copy.deepcopy(kwargs["input"]))
            if len(self.inputs) == 1:
                return type("Response", (), {
                    "output": [
                        type("Call", (), {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup_policy",
                            "arguments": '{"policy":"travel"}',
                        })()
                    ],
                    "output_text": "",
                })()
            return type("Response", (), {"output": [], "output_text": "done"})()

    responses = Responses()
    model = Model.__new__(Model)
    model._deployment = "dep"
    model._instructions = None
    model._reasoning = {}
    model._tool_definitions = ()
    model._client = type("Client", (), {
        "get_openai_client": lambda self: type("OpenAI", (), {"responses": responses})()
    })()
    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: Span())

    out = asyncio.run(model.respond_with_tools("check travel", tools=[Tool()], activity=object()))

    assert out == "done"
    assert responses.inputs[0] == _user_message_input("check travel")
    assert responses.inputs[1][0] == _user_message_input("check travel")[0]
    assert responses.inputs[1][-1]["type"] == "function_call_output"
    event_names = [name for name, _ in events]
    assert "castia.model.tool_calls.requested" in event_names
    assert "castia.tool.call.started" in event_names
    assert "castia.tool.call.completed" in event_names
    assert "castia.model.final_response.completed" in event_names


def test_respond_with_tools_emits_local_model_and_tool_traces():
    records = []

    class Tool:
        name = "lookup_policy"

        def spec(self):
            return {"type": "function", "function": {"name": self.name}}

        async def run(self, activity, **kwargs):
            return {"ok": True, "policy": kwargs["policy"]}

    class Responses:
        def __init__(self):
            self.inputs = []

        async def create(self, **kwargs):
            self.inputs.append(copy.deepcopy(kwargs["input"]))
            if len(self.inputs) == 1:
                return type("Response", (), {
                    "output": [
                        type("Call", (), {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup_policy",
                            "arguments": '{"policy":"travel"}',
                        })()
                    ],
                    "output_text": "",
                })()
            return type("Response", (), {"output": [], "output_text": "done"})()

    responses = Responses()
    model = Model.__new__(Model)
    model._deployment = "dep"
    model._instructions = None
    model._reasoning = {}
    model._tool_definitions = ()
    model._client = type("Client", (), {
        "get_openai_client": lambda self: type("OpenAI", (), {"responses": responses})()
    })()
    register_trace_sink("memory", records.append)
    try:
        out = asyncio.run(
            model.respond_with_tools("check travel", tools=[Tool()], activity=object())
        )
    finally:
        clear_trace_sinks()

    assert out == "done"
    assert [record.name for record in records] == [
        "castia.tool.execute",
        "castia.model.respond_with_tools",
    ]
    tool_record, model_record = records
    assert tool_record.parent_id == model_record.span_id
    assert tool_record.kind == "tool"
    assert tool_record.attributes["gen_ai.tool.name"] == "lookup_policy"
    assert tool_record.attributes["castia.tool.status"] == "ok"
    assert tool_record.attributes["castia.tool.argument_count"] == 1
    assert model_record.attributes["castia.model.phase"] == "tool_loop"
    assert model_record.attributes["castia.model.iteration"] == 2
    assert model_record.attributes["castia.model.output_text_length"] == 4
    assert model_record.attributes["castia.tool.available_count"] == 1


# -- reasoning-effort passthrough (RFT / model-switch) ----------------------


def test_reasoning_param_maps_levels_and_rejects_typos():
    assert _reasoning_param(None) == {}
    assert _reasoning_param("high") == {"reasoning": {"effort": "high"}}
    # Case/whitespace are normalized so config values are forgiving.
    assert _reasoning_param(" Medium ") == {"reasoning": {"effort": "medium"}}
    for bad in ("", "hi", "extreme"):
        with pytest.raises(ValueError):
            _reasoning_param(bad)


def test_instructions_param_omits_none():
    assert _instructions_param(None) == {}
    assert _instructions_param("be terse") == {"instructions": "be terse"}


def test_respond_passes_reasoning_effort():
    model, sink = _model(None, reasoning={"reasoning": {"effort": "high"}})
    asyncio.run(model.respond("solve"))
    assert sink["reasoning"] == {"effort": "high"}


def test_respond_omits_reasoning_for_plain_chat_models():
    # No effort set -> the field is absent, so a chat model is called unchanged.
    model, sink = _model(None)
    asyncio.run(model.respond("hi"))
    assert "reasoning" not in sink


def test_stream_passes_reasoning_effort():
    model, sink = _model(None, reasoning={"reasoning": {"effort": "low"}})

    async def _drain():
        async for _ in model.stream("hi"):  # pragma: no cover - empty stream
            pass

    asyncio.run(_drain())
    assert sink["reasoning"] == {"effort": "low"}
    assert sink["stream"] is True


def test_bad_reasoning_effort_fails_fast_at_construction(monkeypatch):
    # A typo must raise when the Model is built -- before any turn -- rather than
    # surfacing as a 400 mid-conversation. Validation runs ahead of the network
    # client, so no Azure endpoint/credentials are needed to hit it.
    monkeypatch.delenv("MODEL_REASONING_EFFORT", raising=False)
    with pytest.raises(ValueError, match="reasoning_effort"):
        Model(deployment="dep", reasoning_effort="extreme")


def test_bad_reasoning_effort_from_env_fails_fast(monkeypatch):
    # The MODEL_REASONING_EFFORT operator override flows through the same
    # validation, so a bad env value also fails fast at construction.
    monkeypatch.setenv("MODEL_REASONING_EFFORT", "turbo")
    with pytest.raises(ValueError, match="reasoning_effort"):
        Model(deployment="dep")

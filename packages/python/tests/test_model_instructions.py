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
from typing import ClassVar

import pytest

from castia.model import Model, _instructions_param, _reasoning_param
from castia.toolbox import toolbox_mcp_tool


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
    assert sink["input"] == "hi"
    assert sink["model"] == "dep"


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
    assert sink["stream"] is True


def test_respond_with_tools_threads_instructions():
    model, sink = _model("tool prompt")
    out = asyncio.run(
        model.respond_with_tools("hi", tools=[], activity=object())
    )
    assert out == "ok"
    assert sink["instructions"] == "tool prompt"


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

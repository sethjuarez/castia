from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import castia.prompty as castia_prompty
from castia.optimizing.config import AgentConfig

prompty = pytest.importorskip("prompty")
pytest.importorskip("jinja2")

from castia.prompty import (
    McpToolboxError,
    ToolboxMcpClient,
    configured_prompty_runner,
    prompty_agent_from_config,
    register_prompty_otel_tracing,
    register_toolbox_function,
    serialize_mcp_result,
    toolbox_prompty_tools,
)


def test_prompty_agent_from_config_projects_optimizer_config():
    config = AgentConfig("gpt-6-astra", "Answer from policy.", "local:baseline")

    agent = prompty_agent_from_config(config, connection_name="foundry-default")

    assert agent.name == "castia-prompty-agent"
    assert agent.inputs[0].name == "text"
    assert agent.instructions == "system:\nAnswer from policy.\n\nuser:\n{{ text }}"
    assert agent.model.id == "gpt-6-astra"
    assert agent.model.provider == "foundry"
    assert agent.model.api_type == "responses"
    assert agent.model.connection.name == "foundry-default"
    assert agent.metadata["castia.optimizer_contract"] == ".agent_configs"


def test_configured_prompty_runner_wraps_in_memory_agent():
    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Be brief.", "default"),
        enable_otel=False,
    )

    assert runner.agent.instructions == "system:\nBe brief.\n\nuser:\n{{ text }}"
    assert runner.agent.model.id == "gpt-4o"


def test_prompty_runner_turn_does_not_request_structured_cast(monkeypatch):
    async def local_tool() -> str:
        return "tool"

    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Be brief.", "default"),
        tool_functions={"local_tool": local_tool},
        max_iterations=3,
        enable_otel=False,
    )
    calls = []

    async def fake_turn_async(agent, inputs, **kwargs):
        calls.append((agent, inputs, kwargs))
        return "plain text answer"

    monkeypatch.setattr(prompty, "turn_async", fake_turn_async)

    assert asyncio.run(runner.turn("hello")) == "plain text answer"
    assert calls[0][0] is runner.agent
    assert calls[0][1] == {"text": "hello"}
    assert set(calls[0][2]["tools"]) == {"local_tool"}
    assert calls[0][2]["tools"]["local_tool"] is not local_tool
    assert calls[0][2]["max_iterations"] == 3


def test_prompty_runner_wraps_tool_functions_in_tool_spans(monkeypatch):
    from contextlib import contextmanager

    events = []

    @contextmanager
    def fake_execute_tool(name):
        events.append(("enter", name))
        yield
        events.append(("exit", name))

    def local_tool(topic: str) -> str:
        events.append(("call", topic))
        return "done"

    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Use tools.", "default"),
        tool_functions={"local_tool": local_tool},
        enable_otel=False,
    )

    async def fake_turn_async(agent, inputs, **kwargs):
        return await kwargs["tools"]["local_tool"](topic="canvas")

    monkeypatch.setattr(prompty, "turn_async", fake_turn_async)
    monkeypatch.setattr("castia.observe.tracing.execute_tool", fake_execute_tool)

    assert asyncio.run(runner.turn("hello")) == "done"
    assert events == [("enter", "local_tool"), ("call", "canvas"), ("exit", "local_tool")]


def _castia_lookup_tool(calls, *, kind="graph_iq"):
    from castia.inference.tools import Tool

    async def impl(activity, *, query: str, limit: int = 3) -> dict:
        calls.append((activity, query, limit))
        return {"ok": True, "query": query}

    return Tool(
        name="lookup",
        description="Look something up.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to find."},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        impl=impl,
        kind=kind,
    )


def test_prompty_tools_from_castia_projects_schema_activity_and_kind(monkeypatch):
    from prompty.core.tool_dispatch import dispatch_tool_async

    calls, spans = [], []
    monkeypatch.setattr("castia.observe.tracing.execute_tool", _recording_execute_tool(spans))
    tool = _castia_lookup_tool(calls)

    definitions, functions = castia_prompty.prompty_tools_from_castia([tool])

    assert [d.name for d in definitions] == ["lookup"]
    assert definitions[0].description == "Look something up."
    params = {p.name: p for p in definitions[0].parameters}
    assert params["query"].kind == "string" and params["query"].required is True
    assert params["limit"].kind == "integer" and params["limit"].required is False

    activity = object()

    async def run():
        token = castia_prompty._CURRENT_ACTIVITY.set(activity)
        try:
            return await dispatch_tool_async("lookup", '{"query":"fees"}', functions, None, {})
        finally:
            castia_prompty._CURRENT_ACTIVITY.reset(token)

    assert json.loads(asyncio.run(run())) == {"ok": True, "query": "fees"}
    assert calls == [(activity, "fees", 3)]
    assert [(s.name, s.attributes["tool_type"]) for s in spans] == [("execute_tool lookup", "graph_iq")]


def test_prompty_runner_turn_passes_activity_to_castia_tools(monkeypatch):
    calls = []
    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Use tools.", "default"),
        tools=[_castia_lookup_tool(calls)],
        enable_otel=False,
    )
    assert [tool.name for tool in runner.agent.tools] == ["lookup"]

    async def fake_turn_async(agent, inputs, **kwargs):
        assert "activity" not in inputs
        return await kwargs["tools"]["lookup"](query="travel")

    monkeypatch.setattr(prompty, "turn_async", fake_turn_async)
    activity = object()

    assert json.loads(asyncio.run(runner.turn("hi", activity=activity))) == {"ok": True, "query": "travel"}
    assert calls == [(activity, "travel", 3)]
    assert castia_prompty._CURRENT_ACTIVITY.get() is None


def test_prompty_runner_turn_defaults_to_ambient_activity(monkeypatch):
    from castia.runtime.context import turn_scope

    calls = []
    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Use tools.", "default"),
        tools=[_castia_lookup_tool(calls)],
        enable_otel=False,
    )

    async def fake_turn_async(agent, inputs, **kwargs):
        return await kwargs["tools"]["lookup"](query="x")

    monkeypatch.setattr(prompty, "turn_async", fake_turn_async)
    activity = object()

    async def run():
        with turn_scope(activity):
            return await runner.turn("hi")

    asyncio.run(run())
    assert calls[0][0] is activity


def test_prompty_tools_from_castia_keeps_enum_array_and_object_schema():
    from prompty.providers.openai.executor import _schema_to_wire

    from castia.inference.tools import Tool

    async def impl(activity, **kwargs):
        return {}

    tool = Tool(
        name="search",
        description="Search.",
        parameters={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "deep"]},
                "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}},
                "filter": {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}},
                    "required": ["owner"],
                },
            },
            "required": ["mode"],
        },
        impl=impl,
    )

    definitions, _ = castia_prompty.prompty_tools_from_castia([tool])
    wire = _schema_to_wire(definitions[0].parameters)

    assert wire["required"] == ["mode"]
    assert wire["properties"]["mode"] == {"type": "string", "enum": ["fast", "deep"]}
    assert wire["properties"]["tags"]["items"] == {"type": "string", "enum": ["a", "b"]}
    assert wire["properties"]["filter"]["properties"] == {"owner": {"type": "string"}}
    assert wire["properties"]["filter"]["required"] == ["owner"]


def test_configured_prompty_runner_appends_tools_to_prompty_file(tmp_path):
    path = tmp_path / "agent.prompty"
    path.write_text(
        """---
name: sidecar
model:
  id: gpt-4o
  provider: foundry
  connection:
    kind: reference
    name: default
tools:
  - name: lookup
    kind: function
    description: From file.
---
system:
Hi.
""",
        encoding="utf-8",
    )
    extra = prompty.FunctionTool(name="other", description="Other.", parameters=[])

    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "x", "default"),
        tools=[extra, _castia_lookup_tool([])],
        prompty_path=path,
        enable_otel=False,
    )

    assert [tool.name for tool in runner.agent.tools] == ["lookup", "other"]
    assert runner.agent.tools[0].description == "From file."
    assert "lookup" in runner.tool_functions


def test_configured_prompty_runner_explicit_tool_functions_win(monkeypatch):
    async def override(**_kwargs):
        return "override"

    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Use tools.", "default"),
        tools=[_castia_lookup_tool([])],
        tool_functions={"lookup": override},
        enable_otel=False,
    )
    assert runner.tool_functions["lookup"] is override


def test_prompty_otel_registration_filters_content_without_opt_in(monkeypatch):
    calls = []
    provider = FakeProvider()

    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append((name, tracer)))

    assert register_prompty_otel_tracing(provider=provider) is True
    assert calls and calls[0][0] == "otel"

    with calls[0][1]("turn_async") as add:
        add("signature", "prompty.core.turn_async")
        add("inputs", {"text": "private prompt"})
        add("result", "private answer")

    assert provider.spans[0].name == "prompty turn_async"
    assert provider.spans[0].attributes["castia.prompty.signature"] == "prompty.core.turn_async"
    assert "castia.prompty.inputs" not in provider.spans[0].attributes
    assert "castia.prompty.result" not in provider.spans[0].attributes
    assert "gen_ai.input.messages" not in provider.spans[0].attributes
    assert "gen_ai.output.messages" not in provider.spans[0].attributes

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    calls.clear()
    provider = FakeProvider()
    assert register_prompty_otel_tracing(provider=provider) is True
    with calls[0][1]("turn_async") as add:
        add("inputs", {"text": "recorded prompt"})
        add("result", "recorded answer")
    assert provider.spans[0].attributes["castia.prompty.inputs"] == '{"text": "recorded prompt"}'
    assert provider.spans[0].attributes["castia.prompty.result"] == "recorded answer"
    assert provider.spans[0].attributes["gen_ai.input.messages"] == '[{"role": "user", "content": "recorded prompt"}]'
    assert provider.spans[0].attributes["gen_ai.output.messages"] == '[{"role": "assistant", "content": "recorded answer"}]'
    assert provider.spans[0].status is not None


def test_prompty_otel_registration_uses_active_provider(monkeypatch):
    calls = []
    provider = FakeProvider()

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append(("add", name, tracer)))

    assert register_prompty_otel_tracing(tracer_name="castia-prompty", provider=provider) is True
    assert calls[0][0:2] == ("add", "otel")
    assert callable(calls[0][2])
    with calls[0][2]("prepare_async"):
        pass
    assert provider.tracer_names == []


def test_prompty_otel_registration_traces_internal_spans_when_enabled(monkeypatch):
    calls = []
    provider = FakeProvider()

    monkeypatch.setenv("CASTIA_PROMPTY_TRACE_INTERNAL", "true")
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append(("add", name, tracer)))

    assert register_prompty_otel_tracing(tracer_name="castia-prompty", provider=provider) is True
    with calls[0][2]("prepare_async"):
        pass

    assert provider.tracer_names == ["castia-prompty"]
    assert provider.spans[0].name == "prompty prepare_async"


def test_prompty_timeline_records_turn_and_tool_details(monkeypatch):
    from contextlib import contextmanager

    provider = FakeProvider()
    tool_span = FakeSpan("execute_tool local_tool", {})
    timeline = castia_prompty._TurnTimeline(include_content=True, events=[])
    token = castia_prompty._CURRENT_TIMELINE.set(timeline)

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")

    @contextmanager
    def fake_execute_tool(_name):
        yield tool_span

    monkeypatch.setattr("castia.observe.tracing.execute_tool", fake_execute_tool)
    backend = castia_prompty._prompty_otel_backend(
        tracer_name="prompty",
        provider=provider,
        include_content=True,
        include_internal=False,
    )

    try:
        with backend("turn_async") as add:
            add("inputs", {"text": "hello"})
            traced = castia_prompty._traced_tool_functions(
                {"local_tool": lambda topic: "tool result"}
            )
            assert asyncio.run(traced["local_tool"](topic="canvas")) == "tool result"
            add("result", "answer")
    finally:
        castia_prompty._CURRENT_TIMELINE.reset(token)

    turn_timeline = json.loads(provider.spans[0].attributes["castia.turn.timeline"])
    assert [event["type"] for event in turn_timeline] == ["turn_start", "tool", "turn_end"]
    assert turn_timeline[0] == {"type": "turn_start", "input": "hello", "step": 1}
    assert turn_timeline[1] == {
        "type": "tool",
        "name": "local_tool",
        "status": "ok",
        "arguments": {"kwargs": {"topic": "canvas"}},
        "step": 2,
        "output": "tool result",
    }
    assert turn_timeline[2] == {"type": "turn_end", "output": "answer", "step": 3}
    assert provider.spans[0].attributes["castia.turn.summary"] == "turn_start -> tool:local_tool -> turn_end"
    assert provider.spans[0].attributes["castia.turn.tool_call_count"] == 1
    assert tool_span.attributes["castia.step.index"] == 2
    assert tool_span.attributes["castia.step.kind"] == "tool"
    assert tool_span.attributes["gen_ai.tool.arguments"] == '{"kwargs": {"topic": "canvas"}}'
    assert tool_span.attributes["gen_ai.tool.output"] == "tool result"


def test_prompty_timeline_omits_content_without_opt_in(monkeypatch):
    from contextlib import contextmanager

    provider = FakeProvider()
    tool_span = FakeSpan("execute_tool local_tool", {})
    timeline = castia_prompty._TurnTimeline(include_content=False, events=[])
    token = castia_prompty._CURRENT_TIMELINE.set(timeline)

    @contextmanager
    def fake_execute_tool(_name):
        yield tool_span

    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr("castia.observe.tracing.execute_tool", fake_execute_tool)
    backend = castia_prompty._prompty_otel_backend(
        tracer_name="prompty",
        provider=provider,
        include_content=False,
        include_internal=False,
    )

    try:
        with backend("turn_async") as add:
            add("inputs", {"text": "private prompt"})
            traced = castia_prompty._traced_tool_functions(
                {"local_tool": lambda topic: "private result"}
            )
            assert asyncio.run(traced["local_tool"](topic="canvas")) == "private result"
            add("result", "private answer")
    finally:
        castia_prompty._CURRENT_TIMELINE.reset(token)

    turn_timeline = json.loads(provider.spans[0].attributes["castia.turn.timeline"])
    assert turn_timeline == [
        {"type": "turn_start", "step": 1},
        {"type": "tool", "name": "local_tool", "status": "ok", "step": 2},
        {"type": "turn_end", "step": 3},
    ]
    assert "gen_ai.tool.arguments" not in tool_span.attributes
    assert "gen_ai.tool.output" not in tool_span.attributes


class FakeProvider:
    def __init__(self):
        self.spans = []
        self.tracer_names = []

    def get_tracer(self, name):
        self.tracer_names.append(name)
        return FakeTracer(self)


class FakeTracer:
    def __init__(self, provider):
        self.provider = provider

    def start_as_current_span(self, name, *, attributes=None, **_kwargs):
        span = FakeSpan(name, attributes or {})
        self.provider.spans.append(span)
        return span


class FakeSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes)
        self.status = None
        self.exceptions = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, status):
        self.status = status

    def record_exception(self, exc):
        self.exceptions.append(exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_toolbox_mcp_client_lists_and_calls_tools_with_bearer():
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request, payload))
        assert request.headers["Authorization"] == "Bearer TOKEN"
        if payload["method"] == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [{"name": "lookup"}]}},
            )
        assert payload["method"] == "tools/call"
        assert payload["params"] == {"name": "lookup", "arguments": {"query": "travel"}}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": "grounded answer"}]},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = ToolboxMcpClient(
                "https://example.test/toolboxes/contracts/mcp?api-version=v1",
                token_provider=lambda: "TOKEN",
                client=http,
            )
            assert await client.list_tools() == [{"name": "lookup"}]
            assert serialize_mcp_result(await client.call_tool("lookup", {"query": "travel"})) == "grounded answer"

    asyncio.run(run())
    assert [payload["method"] for _, payload in requests] == ["tools/list", "tools/call"]


def test_toolbox_mcp_client_reports_json_rpc_errors():
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32000, "message": "denied"}},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = ToolboxMcpClient("https://example.test/mcp", token_provider=lambda: "TOKEN", client=http)
            with pytest.raises(McpToolboxError, match="denied"):
                await client.call_tool("lookup", {})

    asyncio.run(run())


def test_register_toolbox_function_dispatches_through_prompty_registry():
    from prompty.core.tool_dispatch import clear_tools, dispatch_tool_async

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": f"found {payload['params']['arguments']['query']}"}]},
            },
        )

    async def run():
        clear_tools()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = ToolboxMcpClient("https://example.test/mcp", token_provider=lambda: "TOKEN", client=http)
            register_toolbox_function("contracts-kb-mcp___knowledge_base_retrieve", client=client)
            result = await dispatch_tool_async(
                "contracts-kb-mcp___knowledge_base_retrieve",
                '{"query":"travel"}',
                {},
                None,
                {},
            )
            assert result == "found travel"

    asyncio.run(run())
    clear_tools()


def _toolbox_echo_client(http):
    return ToolboxMcpClient("https://example.test/mcp", token_provider=lambda: "TOKEN", client=http)


def _toolbox_echo_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    query = payload["params"]["arguments"].get("query", "")
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"type": "text", "text": f"found {query}"}]}},
    )


def _recording_execute_tool(spans):
    from contextlib import contextmanager

    @contextmanager
    def fake_execute_tool(name, **kwargs):
        span = FakeSpan(f"execute_tool {name}", {"tool_type": kwargs.get("tool_type")})
        spans.append(span)
        yield span

    return fake_execute_tool


def test_register_toolbox_function_traces_global_registry_dispatch(monkeypatch):
    from prompty.core.tool_dispatch import clear_tools, dispatch_tool_async

    spans = []
    monkeypatch.setattr("castia.observe.tracing.execute_tool", _recording_execute_tool(spans))

    async def run():
        clear_tools()
        async with httpx.AsyncClient(transport=httpx.MockTransport(_toolbox_echo_handler)) as http:
            register_toolbox_function("kb_retrieve", client=_toolbox_echo_client(http))
            return await dispatch_tool_async("kb_retrieve", '{"query":"travel"}', {}, None, {})

    try:
        assert asyncio.run(run()) == "found travel"
    finally:
        clear_tools()
    assert [span.name for span in spans] == ["execute_tool kb_retrieve"]
    assert spans[0].attributes["tool_type"] == "toolbox"
    assert spans[0].attributes["castia.tool.status"] == "ok"


def test_toolbox_function_in_tool_functions_is_traced_once(monkeypatch):
    from prompty.core.tool_dispatch import clear_tools

    spans = []
    monkeypatch.setattr("castia.observe.tracing.execute_tool", _recording_execute_tool(spans))

    async def run():
        clear_tools()
        async with httpx.AsyncClient(transport=httpx.MockTransport(_toolbox_echo_handler)) as http:
            callback = register_toolbox_function("kb_retrieve", client=_toolbox_echo_client(http))
            traced = castia_prompty._traced_tool_functions({"kb_retrieve": callback})
            assert traced["kb_retrieve"] is callback
            return await traced["kb_retrieve"](query="fees")

    try:
        assert asyncio.run(run()) == "found fees"
    finally:
        clear_tools()
    assert len(spans) == 1


def test_traced_tool_preserves_signature_and_rewraps_on_identity_change(monkeypatch):
    import inspect

    spans = []
    monkeypatch.setattr("castia.observe.tracing.execute_tool", _recording_execute_tool(spans))

    async def lookup(*, query: str) -> str:
        return query

    first = castia_prompty._traced_tool("lookup", lookup, tool_type="toolbox")
    assert inspect.signature(first) == inspect.signature(lookup)
    assert inspect.iscoroutinefunction(first)
    assert castia_prompty._traced_tool("lookup", first, tool_type="toolbox") is first

    renamed = castia_prompty._traced_tool("search", first)
    assert asyncio.run(renamed(query="x")) == "x"
    assert [(span.name, span.attributes["tool_type"]) for span in spans] == [("execute_tool search", None)]


def test_toolbox_tool_handler_traces_kind_dispatch(monkeypatch):
    from types import SimpleNamespace

    spans = []
    monkeypatch.setattr("castia.observe.tracing.execute_tool", _recording_execute_tool(spans))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_toolbox_echo_handler)) as http:
            handler = castia_prompty.ToolboxToolHandler(_toolbox_echo_client(http))
            return await handler.execute_tool_async(SimpleNamespace(name="kb_retrieve"), {"query": "travel"}, None, {})

    assert asyncio.run(run()) == "found travel"
    assert [span.name for span in spans] == ["execute_tool kb_retrieve"]
    assert spans[0].attributes["tool_type"] == "toolbox"


def test_toolbox_prompty_tools_create_function_tools_for_model_wire():
    tools = toolbox_prompty_tools(
        ("contracts-kb-mcp___knowledge_base_retrieve",),
        descriptions={
            "contracts-kb-mcp___knowledge_base_retrieve": "Retrieve policy passages.",
        },
        param_guidance={
            "contracts-kb-mcp___knowledge_base_retrieve": {
                "query": "A concise policy search query.",
            }
        },
    )

    tool = tools[0]
    assert tool.kind == "function"
    assert tool.name == "contracts-kb-mcp___knowledge_base_retrieve"
    assert tool.description == "Retrieve policy passages."
    assert tool.parameters[0].name == "query"
    assert tool.parameters[0].description == "A concise policy search query."


def test_serialize_mcp_result_rejects_mcp_error_result():
    with pytest.raises(McpToolboxError, match="boom"):
        serialize_mcp_result({"isError": True, "content": [{"type": "text", "text": "boom"}]})


def test_serialize_mcp_result_compacts_foundryiq_reference_payloads():
    result = {
        "content": [
            {
                "type": "text",
                "text": "Travel meals are reimbursable when the expense is reasonable and itemized with curly “quotes”.",
            },
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "kind": "reference",
                        "ref_id": "policy-1",
                        "uri": "https://contoso.example/policies/travel",
                        "sourceData": {
                            "title": "Travel & Expense Policy",
                            "content": "bulky source data that should not be emitted",
                            "irrelevant": "x" * 1000,
                        },
                        "snippet": "Meals must be itemized, including breakfast, lunch, and dinner.",
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "kind": "reference",
                        "ref_id": "policy-2",
                        "uri": "https://contoso.example/policies/receipts",
                        "sourceData": {
                            "title": "Receipt Requirements",
                            "snippet": "Receipts are required for hotel and airfare.",
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    }

    serialized = serialize_mcp_result(result)

    assert "Travel meals are reimbursable" in serialized
    assert "curly “quotes”" in serialized
    assert "References:" in serialized
    assert "[policy-1] Travel & Expense Policy - https://contoso.example/policies/travel" in serialized
    assert "Meals must be itemized" in serialized
    assert "[policy-2] Receipt Requirements - https://contoso.example/policies/receipts" in serialized
    assert "Receipts are required for hotel and airfare." in serialized
    assert '"kind": "reference"' not in serialized
    assert "bulky source data" not in serialized
    assert "irrelevant" not in serialized


def test_serialize_mcp_result_uses_actual_json_error_message():
    result = {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "kind": "error",
                        "message": "FoundryIQ denied access to the selected source.",
                    }
                ),
            }
        ],
    }

    with pytest.raises(McpToolboxError, match="FoundryIQ denied access"):
        serialize_mcp_result(result)

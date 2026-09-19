from __future__ import annotations

import asyncio
import json

import httpx
import pytest

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


def test_prompty_otel_registration_filters_content_without_opt_in(monkeypatch):
    calls = []
    emitted = []

    def fake_otel_tracer(*, tracer_name, provider):
        def backend(name):
            class Backend:
                def __enter__(self):
                    emitted.append(("span", name))
                    return lambda key, value: emitted.append((key, value))

                def __exit__(self, exc_type, exc, tb):
                    return False

            return Backend()

        return backend

    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append((name, tracer)))
    monkeypatch.setattr("prompty.tracing.otel.otel_tracer", fake_otel_tracer)

    assert register_prompty_otel_tracing() is True
    assert calls and calls[0][0] == "otel"

    with calls[0][1]("turn_async") as add:
        add("signature", "prompty.core.turn_async")
        add("inputs", {"text": "private prompt"})
        add("result", "private answer")

    assert ("signature", "prompty.core.turn_async") in emitted
    assert all(key not in {"inputs", "result"} for key, _value in emitted)

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    calls.clear()
    assert register_prompty_otel_tracing() is True
    with calls[0][1]("turn_async") as add:
        add("inputs", {"text": "recorded prompt"})
        add("result", "recorded answer")
    assert ("inputs", {"text": "recorded prompt"}) in emitted
    assert ("result", "recorded answer") in emitted


def test_prompty_otel_registration_uses_active_provider(monkeypatch):
    calls = []

    def fake_otel_tracer(*, tracer_name, provider):
        calls.append(("otel_tracer", tracer_name, provider))
        return "otel-backend"

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append(("add", name, tracer)))
    monkeypatch.setattr("prompty.tracing.otel.otel_tracer", fake_otel_tracer)

    assert register_prompty_otel_tracing(tracer_name="castia-prompty", provider="provider") is True
    assert calls[0] == ("otel_tracer", "castia-prompty", "provider")
    assert calls[1][0:2] == ("add", "otel")
    assert callable(calls[1][2])


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
                "https://example.test/toolboxes/contracts/mcp",
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
    with pytest.raises(McpToolboxError):
        serialize_mcp_result({"isError": True, "content": [{"type": "text", "text": "boom"}]})

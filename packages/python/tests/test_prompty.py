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
    runner = configured_prompty_runner(
        AgentConfig("gpt-4o", "Be brief.", "default"),
        enable_otel=False,
    )
    calls = []

    async def fake_invoke_async(agent, inputs, **kwargs):
        calls.append((agent, inputs, kwargs))
        return "plain text answer"

    monkeypatch.setattr(prompty, "invoke_async", fake_invoke_async)

    assert asyncio.run(runner.turn("hello")) == "plain text answer"
    assert calls == [(runner.agent, {"text": "hello"}, {})]


def test_prompty_otel_registration_respects_content_recording_opt_in(monkeypatch):
    calls = []
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(prompty.Tracer, "add", lambda name, tracer: calls.append((name, tracer)))

    assert register_prompty_otel_tracing() is False
    assert calls == []

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    assert register_prompty_otel_tracing() is True
    assert calls and calls[0][0] == "otel"


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

"""Offline protocol and Prompty wiring checks; no Azure or model calls."""

import asyncio

from castia.building import AgentTestHarness
from main import (
    allowed_tool_names,
    app,
    local_agent_fact,
    local_function_tools,
    prompty_tool_definitions,
    register_local_functions,
    runner_provider,
    validate_startup,
)


class EchoRunner:
    async def turn(self, text):
        return f"Prompty echo: {text}"


def test_responses_protocol_uses_runner_dependency():
    async def check():
        async with AgentTestHarness(
            app,
            dependency_overrides={runner_provider: EchoRunner},
        ) as test:
            ready = await test.client.get("/readiness")
            assert ready.status_code == 200
            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "Prompty echo: hello"

    asyncio.run(check())


def test_startup_validation_loads_env_and_instructions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")

    validate_startup()


def test_allowed_tool_names_are_trimmed(monkeypatch):
    monkeypatch.setenv("PROMPTY_TOOLBOX_ALLOWED_TOOLS", " lookup, , retrieve ")

    assert allowed_tool_names() == ("lookup", "retrieve")


def test_prompty_tool_definitions_use_castia_prompty_helpers(monkeypatch):
    calls = []

    def fake_toolbox_prompty_tools(names, *, descriptions, param_guidance):
        calls.append((names, descriptions, param_guidance))
        return ["tool:" + name for name in names]

    import castia.prompty

    monkeypatch.setattr(
        castia.prompty,
        "toolbox_prompty_tools",
        fake_toolbox_prompty_tools,
    )

    tools = prompty_tool_definitions(("lookup",))
    assert tools[0].name == "local_agent_fact"
    assert tools[1:] == ["tool:lookup"]
    names, descriptions, param_guidance = calls[0]
    assert names == ("lookup",)
    assert descriptions["lookup"] == "Call the configured Foundry toolbox MCP tool."
    assert param_guidance["lookup"]["query"] == "A concise search or retrieval query."


def test_local_function_tool_dispatches_through_prompty_registry():
    from prompty.core.tool_dispatch import clear_tools, dispatch_tool_async

    async def check():
        clear_tools()
        register_local_functions()
        result = await dispatch_tool_async(
            "local_agent_fact",
            '{"topic":"canvas"}',
            {},
            None,
            {},
        )
        assert result == local_agent_fact("canvas")

    asyncio.run(check())
    clear_tools()


def test_local_function_tool_definition_describes_parameter():
    tool = local_function_tools()[0]

    assert tool.kind == "function"
    assert tool.name == "local_agent_fact"
    assert tool.parameters[0].name == "topic"


def test_runner_provider_registers_prompty_and_optional_toolbox(monkeypatch):
    calls = []

    class FakeToolboxClient:
        pass

    class FakeRunner:
        pass

    def fake_configured_runner(config, *, tools, tool_functions):
        calls.append(("runner", config.model, tools, tool_functions))
        return FakeRunner()

    import castia.prompty

    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    monkeypatch.setenv("PROMPTY_TOOLBOX_ALLOWED_TOOLS", "lookup")
    monkeypatch.setattr(
        castia.prompty,
        "register_foundry_default_connection",
        lambda: calls.append(("connection",)),
    )
    monkeypatch.setattr(
        castia.prompty,
        "register_prompty_otel_tracing",
        lambda: calls.append(("otel",)),
    )
    monkeypatch.setattr(castia.prompty, "ToolboxMcpClient", FakeToolboxClient)
    monkeypatch.setattr(
        castia.prompty,
        "register_toolbox_function",
        lambda name, *, client: calls.append(("toolbox", name, client)),
    )
    monkeypatch.setattr(
        castia.prompty,
        "toolbox_prompty_tools",
        lambda names, **kwargs: ["tool:" + name for name in names],
    )
    monkeypatch.setattr(
        castia.prompty,
        "configured_prompty_runner",
        fake_configured_runner,
    )

    assert isinstance(runner_provider(), FakeRunner)
    assert calls[0] == ("connection",)
    assert calls[1] == ("otel",)
    assert calls[2][0:2] == ("toolbox", "lookup")
    assert isinstance(calls[2][2], FakeToolboxClient)
    assert calls[3][0:2] == ("runner", "gpt-5.5")
    assert calls[3][2][0].name == "local_agent_fact"
    assert calls[3][2][1:] == ["tool:lookup"]
    assert set(calls[3][3]) == {"local_agent_fact", "lookup"}
    assert calls[3][3]["local_agent_fact"] is local_agent_fact

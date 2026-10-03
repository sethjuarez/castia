"""Offline protocol and Prompty wiring checks; no Azure or model calls."""

import asyncio

from castia.building import AgentTestHarness

import main
from main import (
    allowed_tool_names,
    app,
    build_runner_provider,
    configure_prompty_tracing,
    local_agent_fact,
    local_review_checkpoint,
    local_tools,
    local_trace_marker,
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


def test_local_tools_are_castia_tools_with_kind():
    tools = local_tools()

    assert [tool.name for tool in tools] == [
        "local_agent_fact",
        "local_trace_marker",
        "local_review_checkpoint",
    ]
    assert {tool.kind for tool in tools} == {"local_example"}
    assert list(tools[1].parameters["properties"]) == ["topic", "stage"]


def test_local_tools_run_host_side():
    fact, trace, review = local_tools()

    async def check():
        assert await fact.run(None, topic="canvas") == {"result": local_agent_fact("canvas")}
        assert await trace.run(None, topic="canvas", stage="second-tool") == {
            "result": local_trace_marker("canvas", "second-tool")
        }
        assert await review.run(None, topic="canvas", previous="second-tool") == {
            "result": local_review_checkpoint("canvas", "second-tool")
        }

    asyncio.run(check())


def test_build_runner_provider_uses_castia_helper(monkeypatch):
    calls = []

    import castia.prompty

    def fake_provider(config, *, tools, toolbox):
        calls.append((config, [tool.name for tool in tools], toolbox))
        return "provider"

    monkeypatch.setattr(castia.prompty, "prompty_runner_provider", fake_provider)

    monkeypatch.delenv("PROMPTY_TOOLBOX_ALLOWED_TOOLS", raising=False)
    assert build_runner_provider() == "provider"
    monkeypatch.setenv("PROMPTY_TOOLBOX_ALLOWED_TOOLS", "lookup")
    build_runner_provider()

    assert calls[0][0] == main.CONFIG_ROOT
    assert calls[0][1] == ["local_agent_fact", "local_trace_marker", "local_review_checkpoint"]
    assert calls[0][2] is False
    assert calls[1][2] == ["lookup"]


def test_configure_prompty_tracing_registers_at_startup(monkeypatch):
    calls = []

    import castia.prompty

    monkeypatch.setattr(
        castia.prompty,
        "register_prompty_otel_tracing",
        lambda: calls.append(("otel",)),
    )

    configure_prompty_tracing()

    assert calls == [("otel",)]

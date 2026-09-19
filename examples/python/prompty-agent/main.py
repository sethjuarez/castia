"""Prompty-backed Castia agent example for local Foundry runtime experiments."""

import os
import re
from pathlib import Path

from castia import Agent, Depends

app = Agent(name="prompty-agent")
AGENT_ROOT = Path(__file__).parent
CONFIG_ROOT = AGENT_ROOT / ".agent_configs"
PROJECT_ENDPOINT = re.compile(r"^https://[^/\s]+/api/projects/[^/\s]+$")
LOCAL_FACT_TOOL = "local_agent_fact"
LOCAL_TRACE_TOOL = "local_trace_marker"


def load_local_env() -> None:
    env_path = AGENT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return
    load_dotenv(env_path, override=False)


def resolved_agent_config():
    from castia import load_agent_config

    config = load_agent_config(CONFIG_ROOT)
    if not (config.instructions or "").strip():
        raise RuntimeError(
            "No baseline instructions were loaded from .agent_configs/baseline. "
            "Keep metadata.yaml + instructions.md with the agent."
        )
    return config


def validate_startup() -> None:
    load_local_env()
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
    missing = [
        name
        for name, value in {
            "FOUNDRY_PROJECT_ENDPOINT": endpoint,
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": deployment,
        }.items()
        if not value or value.startswith("<")
    ]
    if missing:
        raise RuntimeError(
            "Fill .env from .env.example before starting the agent; missing "
            + ", ".join(missing)
            + "."
        )
    if not PROJECT_ENDPOINT.match(endpoint):
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT must look like "
            "https://<account>.services.ai.azure.com/api/projects/<project>."
        )
    os.environ["FOUNDRY_PROJECT_ENDPOINT"] = endpoint
    resolved_agent_config()


app.startup_check(validate_startup)


def configure_prompty_tracing() -> None:
    from castia.prompty import register_prompty_otel_tracing

    register_prompty_otel_tracing()


app.startup_check(configure_prompty_tracing)


def allowed_tool_names() -> tuple[str, ...]:
    raw = os.environ.get("PROMPTY_TOOLBOX_ALLOWED_TOOLS", "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def local_agent_fact(topic: str = "prompty") -> str:
    return (
        f"Local fact tool refreshed for '{topic}'. "
        "This version marker comes from Python code in examples/python/prompty-agent/main.py."
    )


def local_trace_marker(topic: str = "trace", stage: str = "follow-up") -> str:
    return (
        f"Trace marker refreshed for topic '{topic}' at stage '{stage}'. "
        "This second local Python function now carries a deploy-visible text change."
    )


def local_function_tools() -> list[object]:
    import prompty

    return [
        prompty.FunctionTool(
            name=LOCAL_FACT_TOOL,
            description=(
                "Return a deterministic local fact proving the Prompty loop can "
                "call host-side Python functions."
            ),
            parameters=[
                prompty.Property(
                    name="topic",
                    kind="string",
                    description="Short topic label to include in the local function result.",
                    required=False,
                )
            ],
        ),
        prompty.FunctionTool(
            name=LOCAL_TRACE_TOOL,
            description=(
                "Return a deterministic trace marker proving the Prompty loop can "
                "execute multiple host-side Python functions in one turn."
            ),
            parameters=[
                prompty.Property(
                    name="topic",
                    kind="string",
                    description="Short topic label to include in the trace marker.",
                    required=False,
                ),
                prompty.Property(
                    name="stage",
                    kind="string",
                    description="Short stage label for this trace marker.",
                    required=False,
                ),
            ],
        ),
    ]


def register_local_functions() -> None:
    from prompty.core.tool_dispatch import register_tool

    register_tool(LOCAL_FACT_TOOL, local_agent_fact)
    register_tool(LOCAL_TRACE_TOOL, local_trace_marker)


def toolbox_tool_definitions(tool_names: tuple[str, ...]) -> list[object]:
    if not tool_names:
        return []
    from castia.prompty import toolbox_prompty_tools

    return toolbox_prompty_tools(
        tool_names,
        descriptions={
            name: "Call the configured Foundry toolbox MCP tool."
            for name in tool_names
        },
        param_guidance={
            name: {"query": "A concise search or retrieval query."}
            for name in tool_names
        },
    )


def prompty_tool_definitions(tool_names: tuple[str, ...]) -> list[object]:
    return [*local_function_tools(), *toolbox_tool_definitions(tool_names)]


def runner_provider():
    from castia.prompty import (
        ToolboxMcpClient,
        configured_prompty_runner,
        register_foundry_default_connection,
        register_toolbox_function,
    )

    register_foundry_default_connection()
    register_local_functions()

    tool_names = allowed_tool_names()
    tools = prompty_tool_definitions(tool_names)
    tool_functions = {
        LOCAL_FACT_TOOL: local_agent_fact,
        LOCAL_TRACE_TOOL: local_trace_marker,
    }
    if tool_names:
        client = ToolboxMcpClient()
        for name in tool_names:
            tool_functions[name] = register_toolbox_function(name, client=client)

    return configured_prompty_runner(
        resolved_agent_config(),
        tools=tools,
        tool_functions=tool_functions,
    )


RunnerDependency = Depends(runner_provider)


@app.responses()
async def reply(text: str, runner=RunnerDependency) -> str:
    return await runner.turn(text)


if __name__ == "__main__":
    app.run()

"""Prompty-backed Castia agent example for local Foundry runtime experiments."""

import os
import re
from pathlib import Path

from castia import Agent, Depends

app = Agent(name="prompty-agent")
AGENT_ROOT = Path(__file__).parent
CONFIG_ROOT = AGENT_ROOT / ".agent_configs"
PROJECT_ENDPOINT = re.compile(r"^https://[^/\s]+/api/projects/[^/\s]+$")


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


def allowed_tool_names() -> tuple[str, ...]:
    raw = os.environ.get("PROMPTY_TOOLBOX_ALLOWED_TOOLS", "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def prompty_tool_definitions(tool_names: tuple[str, ...]) -> list[object]:
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


def runner_provider():
    from castia.prompty import (
        ToolboxMcpClient,
        configured_prompty_runner,
        register_foundry_default_connection,
        register_prompty_otel_tracing,
        register_toolbox_function,
    )

    register_foundry_default_connection()
    register_prompty_otel_tracing()

    tool_names = allowed_tool_names()
    tools = prompty_tool_definitions(tool_names)
    if tool_names:
        client = ToolboxMcpClient()
        for name in tool_names:
            register_toolbox_function(name, client=client)

    return configured_prompty_runner(resolved_agent_config(), tools=tools)


RunnerDependency = Depends(runner_provider)


@app.responses()
async def reply(text: str, runner=RunnerDependency) -> str:
    return await runner.turn(text)


if __name__ == "__main__":
    app.run()

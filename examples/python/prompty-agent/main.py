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
LOCAL_REVIEW_TOOL = "local_review_checkpoint"


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


def local_review_checkpoint(topic: str = "trace", previous: str = "none") -> str:
    return (
        f"Review checkpoint captured for topic '{topic}' after '{previous}'. "
        "This third local Python function verifies another tool crank in the same turn."
    )


def _string_params(**descriptions: str) -> dict:
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": text}
            for name, text in descriptions.items()
        },
        "required": [],
    }


def local_tools() -> list[object]:
    """Castia tools run host-side by the Prompty loop; ``kind`` -> gen_ai.tool.type."""
    from castia.inference.tools import Tool

    async def fact(activity, *, topic: str = "prompty") -> dict:
        return {"result": local_agent_fact(topic)}

    async def trace(activity, *, topic: str = "trace", stage: str = "follow-up") -> dict:
        return {"result": local_trace_marker(topic, stage)}

    async def review(activity, *, topic: str = "trace", previous: str = "none") -> dict:
        return {"result": local_review_checkpoint(topic, previous)}

    return [
        Tool(
            name=LOCAL_FACT_TOOL,
            description=(
                "Return a deterministic local fact proving the Prompty loop can "
                "call host-side Python functions."
            ),
            parameters=_string_params(
                topic="Short topic label to include in the local function result.",
            ),
            impl=fact,
            kind="local_example",
        ),
        Tool(
            name=LOCAL_TRACE_TOOL,
            description=(
                "Return a deterministic trace marker proving the Prompty loop can "
                "execute multiple host-side Python functions in one turn."
            ),
            parameters=_string_params(
                topic="Short topic label to include in the trace marker.",
                stage="Short stage label for this trace marker.",
            ),
            impl=trace,
            kind="local_example",
        ),
        Tool(
            name=LOCAL_REVIEW_TOOL,
            description=(
                "Return a deterministic review checkpoint proving the Prompty loop can "
                "continue with another host-side Python function after earlier tool results."
            ),
            parameters=_string_params(
                topic="Short topic label to include in the review checkpoint.",
                previous="Short label for the prior tool result being reviewed.",
            ),
            impl=review,
            kind="local_example",
        ),
    ]


def build_runner_provider():
    """One cached provider: Foundry connection, config, toolbox, and runner."""
    from castia.prompty import prompty_runner_provider

    tool_names = allowed_tool_names()
    return prompty_runner_provider(
        CONFIG_ROOT,
        tools=local_tools(),
        toolbox=list(tool_names) if tool_names else False,
    )


_providers: list = []


async def runner_provider():
    # Built on first turn, after startup checks have loaded .env.
    if not _providers:
        _providers.append(build_runner_provider())
    return await _providers[0]()


RunnerDependency = Depends(runner_provider)


@app.responses()
async def reply(text: str, runner=RunnerDependency) -> str:
    return await runner.turn(text)


if __name__ == "__main__":
    app.run()

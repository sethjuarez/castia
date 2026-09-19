"""Minimal Castia starter for local and hosted Foundry agent demos."""

import os
import re
from pathlib import Path

from castia import Agent, Depends, Teams

app = Agent(name="minimal-agent")
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
            "Install castia[optimize] and keep metadata.yaml + instructions.md with the agent."
        )
    return config


def validate_startup() -> None:
    load_local_env()
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
    missing = [
        name for name, value in {
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


def model_provider():
    # Resolve candidates only on first use, never during module registration.
    from castia import configured_model

    return configured_model(resolved_agent_config())()


ModelDependency = Depends(model_provider)


@app.activity(Teams.direct)
@app.responses()
@app.invocations()
async def reply(text: str, model=ModelDependency) -> str:
    answer = await model.respond(text)
    return f"{answer}\n\nSmoke test marker: local-to-hosted path is current."


if hasattr(app, "responses_stream"):

    @app.responses_stream()
    async def reply_stream(text: str, model=ModelDependency):
        async for delta in model.stream(text):
            yield delta
        yield "\n\nSmoke test marker: local-to-hosted path is current."


if __name__ == "__main__":
    validate_startup()
    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8088"))
    app.run(host=host, port=port)

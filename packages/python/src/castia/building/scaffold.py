"""Create a small runnable project, without provisioning or overwriting files."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


@dataclass(frozen=True)
class ScaffoldReport:
    root: Path
    files: tuple[str, ...]
    app_target: str = "main:app"

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "files": list(self.files),
            "app_target": self.app_target,
            "provisioned": False,
        }


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Project paths must not contain symlinks or junctions.")


def _project_files(name: str, model: str) -> dict[str, str]:
    try:
        requirement = f"castia[optimize]=={version('castia')}"
    except PackageNotFoundError:
        requirement = "castia[optimize]"
    requirements = "-e .\n"
    main = f'''"""Minimal Castia starter for local and hosted Foundry agent demos."""

import os
import re
from pathlib import Path

from castia import Agent, Depends, Teams

app = Agent(name={name!r})
AGENT_ROOT = Path(__file__).parent
CONFIG_ROOT = AGENT_ROOT / ".agent_configs"
PROJECT_ENDPOINT = re.compile(r"^https://[^/\\s]+/api/projects/[^/\\s]+$")


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
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
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
    missing = []
    if not endpoint or endpoint.startswith("<"):
        missing.append("FOUNDRY_PROJECT_ENDPOINT")
    if not deployment or deployment.startswith("<"):
        missing.append("AZURE_AI_MODEL_DEPLOYMENT_NAME")
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


@app.activity(Teams.direct)
@app.responses()
@app.invocations()
async def reply(text: str, model=Depends(model_provider)) -> str:
    return await model.respond(text)


if __name__ == "__main__":
    validate_startup()
    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8088"))
    app.run(host=host, port=port)
'''
    smoke = '''"""Offline protocol contract; no model, identity, or Azure calls."""

import asyncio

from castia.building import AgentTestHarness
from main import app, model_provider, validate_startup


class EchoModel:
    async def respond(self, text):
        return f"Echo: {text}"


def test_protocols():
    async def check():
        async with AgentTestHarness(
            app, dependency_overrides={model_provider: EchoModel}
        ) as test:
            ready = await test.client.get("/readiness")
            assert ready.status_code == 200
            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "Echo: hello"
            response = await test.client.post("/invocations", json={"message": "hello"})
            assert response.json()["output"] == "Echo: hello"
            response = await test.client.post("/activity/messages", json={
                "type": "message", "id": "turn-1", "channelId": "msteams",
                "serviceUrl": "https://connector.invalid", "text": "hello",
                "conversation": {"id": "chat-1", "conversationType": "personal"},
                "from": {"id": "user"}, "recipient": {"id": "bot"},
            })
            assert response.status_code == 200
            assert test.egress[-1].body["text"] == "Echo: hello"
    asyncio.run(check())


def test_startup_validation_loads_env_and_instructions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    validate_startup()
'''
    protocols = "".join(
        f"      - protocol: {protocol}\n        version: 2.0.0\n"
        for protocol in ("activity", "responses", "invocations")
    )
    return {
        "main.py": main,
        "pyproject.toml": (
            "[project]\n"
            f"name = {json.dumps(name)}\n"
            "version = \"0.1.0\"\n"
            "description = \"A minimal Castia starter agent for Microsoft Foundry.\"\n"
            "requires-python = \">=3.11\"\n"
            "dependencies = [\n"
            f"    {json.dumps(requirement)},\n"
            "    \"python-dotenv>=1.0.1\",\n"
            "]\n\n"
            "[project.optional-dependencies]\n"
            "test = [\n"
            "    \"pytest>=8\",\n"
            "]\n\n"
            "[build-system]\n"
            "requires = [\"setuptools>=68\"]\n"
            "build-backend = \"setuptools.build_meta\"\n\n"
            "[tool.setuptools]\n"
            "py-modules = [\"main\"]\n\n"
            "[tool.uv]\n"
            "package = false\n"
        ),
        "requirements.txt": requirements,
        "requirements-dev.txt": "-e .[test]\n",
        "Dockerfile": (
            "# Optional container alternative; azure.yaml defaults to remote code build.\n"
            "FROM python:3.13-slim\nWORKDIR /app\n"
            "ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1\n"
            "COPY pyproject.toml requirements.txt main.py ./\n"
            "COPY .agent_configs .agent_configs\n"
            "RUN python -m pip install --no-cache-dir -r requirements.txt\n"
            "EXPOSE 8088\nCMD [\"python\", \"main.py\"]\n"
        ),
        ".dockerignore": ".git\n.venv\n.env\n.env.*\n__pycache__\ntests\n",
        ".gitignore": ".venv/\n.env\n.env.*\n!.env.example\n__pycache__/\n.pytest_cache/\n",
        ".env.example": (
            "FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>\n"
            "AZURE_AI_MODEL_DEPLOYMENT_NAME=<deployment-name>\n"
        ),
        "DEPLOYMENT.md": (
            "# Deploy to an existing Foundry project\n\n"
            "This scaffold creates local files only. It does not create a project, "
            "model deployment, registry, identity, or infrastructure.\n\n"
            "## Run locally first\n\n"
            "Copy `.env.example` to `.env`, fill `FOUNDRY_PROJECT_ENDPOINT` and "
            "`AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run the app from the agent root:\n\n"
            "```powershell\n"
            "uv sync --project .\n"
            "$env:HOST = \"127.0.0.1\"\n"
            "uv run --directory . python main.py\n"
            "```\n\n"
            "The entrypoint validates `.env` and `.agent_configs/baseline` before "
            "serving. The app binds to `0.0.0.0` by default so hosted ingress "
            "can reach it. Set `HOST=127.0.0.1` for local-only runs. Use the "
            "Foundry Agent Playground health check against "
            "`http://localhost:8088` before sending a model prompt.\n\n"
            "Before code deployment, select your existing azd environment and populate "
            "its context with values verified against that existing project:\n\n"
            "| Setting | Meaning |\n| --- | --- |\n"
            "| `AZURE_LOCATION` | The existing project's Azure region; required for code deploy. |\n"
            "| `AZURE_AI_PROJECT_ID` | Full project ARM resource ID, not an endpoint URL. |\n"
            "| `AZURE_SUBSCRIPTION_ID` | Subscription containing the project. |\n"
            "| `FOUNDRY_PROJECT_ENDPOINT` | Existing project's data-plane endpoint for local/process checks; do not put it in hosted `azure.yaml` env. |\n"
            "| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Existing model deployment name. |\n\n"
            "The ARM ID has the shape "
            "`/subscriptions/<subscription>/resourceGroups/<group>/providers/"
            "Microsoft.CognitiveServices/accounts/<account>/projects/<project>`.\n\n"
            "Authenticate azd in the intended tenant with access to that subscription. "
            "azd resolves the tenant from the subscription; setting `AZURE_TENANT_ID` "
            "alone is not authentication. This project does not verify credentials or RBAC.\n\n"
            "Run `python -m castia build check --deployment` after exporting the same "
            "nonsecret context into your shell. The check reads process settings, not "
            "azd's persisted environment, and does not load `.env.example`. Without "
            "`--deployment`, missing deployment context is advisory so offline protocol "
            "development can continue. Passing this gate is not a live deployment guarantee.\n\n"
            "Foundry hosted agents reserve all `FOUNDRY_*` and `AGENT_*` container "
            "variables. Keep `FOUNDRY_PROJECT_ENDPOINT` in `.env` for local dev or "
            "in host-side process/azd context for Castia checks; do not declare it "
            "under `services.<agent>.env` in `azure.yaml`.\n\n"
            "`pyproject.toml` is the canonical dependency source. "
            "`requirements.txt` exists only as the Foundry remote-build entrypoint "
            "and contains `-e .` so pip installs this local project and reads the "
            "dependency list from `pyproject.toml`.\n\n"
            "The default manifest uses Python 3.13 with remote dependency build; no "
            "local Docker/ACR setup is needed for this mode. The included Dockerfile is "
            "an explicit alternative; see the commented manifest instructions.\n"
        ),
        "azure.yaml": (
            "# Deployment intent only. No infrastructure is created by this scaffold.\n"
            f"name: {name}\nservices:\n  {name}:\n"
            "    project: .\n    host: azure.ai.agent\n    language: python\n"
            f"    kind: hosted\n    name: {name}\n"
            "    description: A Castia agent serving Responses, Invocations and Teams.\n"
            "    codeConfiguration:\n      runtime: python_3_13\n"
            "      entryPoint: main.py\n      dependencyResolution: remote_build\n"
            "    # Optional Docker alternative: remove codeConfiguration, set language: docker,\n"
            "    # and add docker: {path: ./Dockerfile, context: ., remoteBuild: true}.\n"
            "    protocols:\n" + protocols +
            "    env:\n"
            "      AZURE_AI_MODEL_DEPLOYMENT_NAME: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}\n"
            "    container:\n      resources:\n        cpu: '0.5'\n        memory: 1Gi\n"
        ),
        ".agent_configs/baseline/metadata.yaml": (
            f"model: {json.dumps(model)}\ninstruction_file: instructions.md\n"
        ),
        ".agent_configs/baseline/instructions.md": (
            "Answer the user's question accurately and concisely. "
            "Say when you do not know; do not invent facts.\n"
        ),
        "eval.yaml": (
            f"name: {name}-eval\nagent:\n  name: {name}\n"
            "  config: .agent_configs/baseline/metadata.yaml\n"
            "evaluators:\n  - builtin.task_adherence\n"
            "dataset:\n  local_uri: eval-seed.jsonl\n"
        ),
        "eval-seed.jsonl": json.dumps(
            {
                "query": "What is 2 + 2?",
                "ground_truth": "4",
            }
        ) + "\n",
        "tests/test_agent.py": smoke,
    }


def scaffold_project(
    target: str | os.PathLike[str],
    *,
    name: str = "my-agent",
    model: str = "gpt-4o",
) -> ScaffoldReport:
    """Create an agent project in a new or empty directory.

    Reject traversal components, symlink/junction ancestors, unsafe identifiers,
    and *any* existing content (including hidden files). Files are exclusively
    created; on failure only this call's newly created files/directories are
    removed. Never writes outside ``target`` except creating missing ancestors.
    Neither configuration values nor credentials are read from the environment.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("name must be a lowercase letter followed by letters, digits or '-'.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", model):
        raise ValueError("model must be a deployment identifier, not a path or expression.")
    raw = Path(target)
    if ".." in raw.parts:
        raise ValueError("Project target must not contain '..' traversal.")
    root = raw.absolute()
    _no_links(root)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError("Project target must be a new or empty directory.")
    files = _project_files(name, model)
    created_files: list[Path] = []
    created_dirs: list[Path] = []

    def mkdir(path: Path) -> None:
        if not path.exists():
            mkdir(path.parent)
            path.mkdir()
            created_dirs.append(path)

    try:
        mkdir(root)
        for relative, content in files.items():
            path = root.joinpath(*relative.split("/"))
            _no_links(path)
            mkdir(path.parent)
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                created_files.append(path)
                handle.write(content)
    except BaseException:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    return ScaffoldReport(root, tuple(files))

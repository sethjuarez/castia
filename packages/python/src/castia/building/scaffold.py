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
    main = f'''"""Responses, Invocations and Teams share one model-backed handler."""

from pathlib import Path

from castia import Agent, Depends, Teams

app = Agent(name={name!r})


def model_provider():
    # Resolve candidates only on first use, never during module registration.
    from castia import configured_model, load_agent_config

    config = load_agent_config(Path(__file__).parent / ".agent_configs")
    return configured_model(config)()


@app.activity(Teams.direct)
@app.responses()
@app.invocations()
async def reply(text: str, model=Depends(model_provider)) -> str:
    return await model.respond(text)


if __name__ == "__main__":
    app.run()
'''
    smoke = '''"""Offline protocol contract; no model, identity, or Azure calls."""

import asyncio

from castia.building import AgentTestHarness
from main import app, model_provider


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
'''
    protocols = "".join(
        f"      - protocol: {protocol}\n        version: 2.0.0\n"
        for protocol in ("activity", "responses", "invocations")
    )
    return {
        "main.py": main,
        "requirements.txt": requirement + "\n",
        "requirements-dev.txt": "-r requirements.txt\npytest>=8\n",
        "Dockerfile": (
            "# Optional container alternative; azure.yaml defaults to remote code build.\n"
            "FROM python:3.13-slim\nWORKDIR /app\n"
            "ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1\n"
            "COPY requirements.txt .\n"
            "RUN python -m pip install --no-cache-dir -r requirements.txt\n"
            "COPY main.py .\nCOPY .agent_configs .agent_configs\n"
            "EXPOSE 8088\nCMD [\"python\", \"main.py\"]\n"
        ),
        ".dockerignore": ".git\n.venv\n.env\n.env.*\n__pycache__\ntests\n",
        ".gitignore": ".venv/\n.env\n.env.*\n!.env.example\n__pycache__/\n.pytest_cache/\n",
        ".env.example": (
            "# Set these in your shell; Castia does not auto-load this file.\n"
            "FOUNDRY_PROJECT_ENDPOINT=\n"
            f"AZURE_AI_MODEL_DEPLOYMENT_NAME={model}\n"
            "OPTIMIZATION_LOCAL_DIR=.agent_configs\n"
            "# Existing-project deployment context; also set in the selected azd environment.\n"
            "AZURE_LOCATION=\nAZURE_AI_PROJECT_ID=\nAZURE_SUBSCRIPTION_ID=\n"
            "# azd resolves the tenant from the authenticated subscription at deployment.\n"
            "# See DEPLOYMENT.md; no resources are created by this scaffold.\n"
        ),
        "DEPLOYMENT.md": (
            "# Deploy to an existing Foundry project\n\n"
            "This scaffold creates local files only. It does not create a project, "
            "model deployment, registry, identity, or infrastructure.\n\n"
            "Before code deployment, select your existing azd environment and populate "
            "its context with values verified against that existing project:\n\n"
            "| Setting | Meaning |\n| --- | --- |\n"
            "| `AZURE_LOCATION` | The existing project's Azure region; required for code deploy. |\n"
            "| `AZURE_AI_PROJECT_ID` | Full project ARM resource ID, not an endpoint URL. |\n"
            "| `AZURE_SUBSCRIPTION_ID` | Subscription containing the project. |\n"
            "| `FOUNDRY_PROJECT_ENDPOINT` | Existing project's data-plane endpoint. |\n"
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
            "      FOUNDRY_PROJECT_ENDPOINT: ${FOUNDRY_PROJECT_ENDPOINT}\n"
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

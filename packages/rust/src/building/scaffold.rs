use crate::model::BuildScaffoldRuntime;
use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

#[derive(Debug, Clone)]
pub struct BuildError(String);

impl std::fmt::Display for BuildError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for BuildError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaBuildScaffoldRuntime;

#[async_trait::async_trait]
impl BuildScaffoldRuntime for CastiaBuildScaffoldRuntime {
    fn scaffold_files(&self, name: &String, model: &String) -> Value {
        scaffold_files(name, model).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn scaffold_files(name: &str, model: &str) -> Result<Value, BuildError> {
    validate_identifiers(name, model)?;
    let files = project_files(name, model)?;
    let mut names = files.keys().cloned().collect::<Vec<_>>();
    names.sort_unstable();
    Ok(json!({
        "app_target": "main:app",
        "provisioned": false,
        "file_count": names.len(),
        "files": names,
        "content_digest": content_digest(&files),
        "snippets": {
            "main": format!("app = Agent(name='{}')", name),
            "pyproject": format!("name = {}", json_string(name)),
            "manifest": "runtime: python_3_13",
            "metadata": format!("model: {}", json_string(model)),
        },
    }))
}

pub fn project_files(name: &str, model: &str) -> Result<Map<String, Value>, BuildError> {
    validate_identifiers(name, model)?;
    Ok(project_files_unchecked(name, model)
        .into_iter()
        .map(|(path, content)| (path.to_string(), Value::String(content)))
        .collect())
}

fn validate_identifiers(name: &str, model: &str) -> Result<(), BuildError> {
    static NAME: OnceLock<Regex> = OnceLock::new();
    static MODEL: OnceLock<Regex> = OnceLock::new();
    if !NAME
        .get_or_init(|| Regex::new(r"^[a-z][a-z0-9-]{0,62}$").expect("valid regex"))
        .is_match(name)
    {
        return Err(BuildError(
            "name must be a lowercase letter followed by letters, digits or '-'.".to_string(),
        ));
    }
    if !MODEL
        .get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$").expect("valid regex"))
        .is_match(model)
    {
        return Err(BuildError(
            "model must be a deployment identifier, not a path or expression.".to_string(),
        ));
    }
    Ok(())
}

fn project_files_unchecked(name: &str, model: &str) -> Vec<(&'static str, String)> {
    let requirement = "castia[optimize]";
    let protocols = ["activity", "responses", "invocations"]
        .into_iter()
        .map(|protocol| format!("      - protocol: {protocol}\n        version: 2.0.0\n"))
        .collect::<String>();
    vec![
        ("main.py", main_py(name)),
        (
            "pyproject.toml",
            format!(
                "[project]\nname = {}\nversion = \"0.1.0\"\ndescription = \"A minimal Castia starter agent for Microsoft Foundry.\"\nrequires-python = \">=3.11\"\ndependencies = [\n    {},\n    \"python-dotenv>=1.0.1\",\n]\n\n[project.optional-dependencies]\ntest = [\n    \"pytest>=8\",\n]\n\n[tool.uv]\npackage = false\n",
                json_string(name),
                json_string(requirement),
            ),
        ),
        (
            "requirements.txt",
            format!("{requirement}\npython-dotenv>=1.0.1\n"),
        ),
        (
            "requirements-dev.txt",
            "-r requirements.txt\npytest>=8\n".to_string(),
        ),
        (
            "Dockerfile",
            "# Optional container alternative; azure.yaml defaults to remote code build.\nFROM python:3.13-slim\nWORKDIR /app\nENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1\nCOPY requirements.txt .\nRUN python -m pip install --no-cache-dir -r requirements.txt\nCOPY main.py .\nCOPY .agent_configs .agent_configs\nEXPOSE 8088\nCMD [\"python\", \"main.py\"]\n".to_string(),
        ),
        (
            ".dockerignore",
            ".git\n.venv\n.env\n.env.*\n__pycache__\ntests\n".to_string(),
        ),
        (
            ".gitignore",
            ".venv/\n.env\n.env.*\n!.env.example\n__pycache__/\n.pytest_cache/\n".to_string(),
        ),
        (
            ".env.example",
            "FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>\nAZURE_AI_MODEL_DEPLOYMENT_NAME=<deployment-name>\n".to_string(),
        ),
        ("DEPLOYMENT.md", deployment_md().to_string()),
        (
            "azure.yaml",
            format!(
                "# Deployment intent only. No infrastructure is created by this scaffold.\nname: {name}\nservices:\n  {name}:\n    project: .\n    host: azure.ai.agent\n    language: python\n    kind: hosted\n    name: {name}\n    description: A Castia agent serving Responses, Invocations and Teams.\n    codeConfiguration:\n      runtime: python_3_13\n      entryPoint: main.py\n      dependencyResolution: remote_build\n    # Optional Docker alternative: remove codeConfiguration, set language: docker,\n    # and add docker: {{path: ./Dockerfile, context: ., remoteBuild: true}}.\n    protocols:\n{protocols}    env:\n      AZURE_AI_MODEL_DEPLOYMENT_NAME: ${{AZURE_AI_MODEL_DEPLOYMENT_NAME}}\n      FOUNDRY_PROJECT_ENDPOINT: ${{FOUNDRY_PROJECT_ENDPOINT}}\n    container:\n      resources:\n        cpu: '0.5'\n        memory: 1Gi\n"
            ),
        ),
        (
            ".agent_configs/baseline/metadata.yaml",
            format!("model: {}\ninstruction_file: instructions.md\n", json_string(model)),
        ),
        (
            ".agent_configs/baseline/instructions.md",
            "Answer the user's question accurately and concisely. Say when you do not know; do not invent facts.\n".to_string(),
        ),
        (
            "eval.yaml",
            format!(
                "name: {name}-eval\nagent:\n  name: {name}\n  config: .agent_configs/baseline/metadata.yaml\nevaluators:\n  - builtin.task_adherence\ndataset:\n  local_uri: eval-seed.jsonl\n"
            ),
        ),
        (
            "eval-seed.jsonl",
            "{\"query\": \"What is 2 + 2?\", \"ground_truth\": \"4\"}\n".to_string(),
        ),
        ("tests/test_agent.py", smoke_py().to_string()),
    ]
}

fn main_py(name: &str) -> String {
    r###""""Minimal Castia starter for local and hosted Foundry agent demos."""

import os
import re
from pathlib import Path

from castia import Agent, Depends, Teams

app = Agent(name='__NAME__')
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
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return
    load_dotenv(env_path, override=False)


def resolved_agent_config():
    from castia import load_agent_config

    config = load_agent_config(CONFIG_ROOT)
    if not (config.instructions or "").strip():
        raise RuntimeError("No baseline instructions were loaded from .agent_configs/baseline.")
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
        raise RuntimeError("Fill .env from .env.example before starting the agent; missing " + ", ".join(missing) + ".")
    if not PROJECT_ENDPOINT.match(endpoint):
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT must look like https://<account>.services.ai.azure.com/api/projects/<project>.")
    os.environ["FOUNDRY_PROJECT_ENDPOINT"] = endpoint
    resolved_agent_config()


def model_provider():
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
"###
    .replace("__NAME__", name)
}

fn deployment_md() -> &'static str {
    "# Deploy to an existing Foundry project\n\nThis scaffold creates local files only. It does not create a project, model deployment, registry, identity, or infrastructure.\n\nUse `uv run --directory . python main.py` for local startup after filling `.env` from `.env.example`.\n\nBefore code deployment, select your existing azd environment and provide `AZURE_LOCATION`, `AZURE_AI_PROJECT_ID`, `AZURE_SUBSCRIPTION_ID`, `FOUNDRY_PROJECT_ENDPOINT`, and `AZURE_AI_MODEL_DEPLOYMENT_NAME`. azd resolves the tenant from the subscription; setting `AZURE_TENANT_ID` alone is not authentication.\n"
}

fn smoke_py() -> &'static str {
    r#""""Offline protocol contract; no model, identity, or Azure calls."""

import asyncio

from castia.building import AgentTestHarness
from main import app, model_provider, validate_startup


class EchoModel:
    async def respond(self, text):
        return f"Echo: {text}"


def test_protocols():
    async def check():
        async with AgentTestHarness(app, dependency_overrides={model_provider: EchoModel}) as test:
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
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    validate_startup()
"#
}

fn json_string(value: &str) -> String {
    serde_json::to_string(value).expect("string serialization cannot fail")
}

fn content_digest(files: &Map<String, Value>) -> String {
    let mut names = files.keys().collect::<Vec<_>>();
    names.sort_unstable();
    let mut hasher = Sha256::new();
    for name in names {
        hasher.update(name.as_bytes());
        hasher.update([0]);
        hasher.update(files[name].as_str().unwrap_or_default().as_bytes());
        hasher.update([0]);
    }
    format!("{:x}", hasher.finalize())
}

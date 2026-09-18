# Use Castia from an agent

Use this guide when writing an application that depends on the Python package.
Castia requires Python 3.11 or later. It serves Foundry Responses, Activity, and
Invocations handlers, with model dependencies and native MCP tools.

## Start with the installed package

These commands use PowerShell. `$python` keeps every command on the same
interpreter after changing directories.

```powershell
uv venv --python 3.13
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
uv pip install --python $python --prerelease=allow "castia[deploy,optimize,test]"
& $python -m castia build scaffold .\my-agent --name my-agent --model gpt-4o
Set-Location .\my-agent
```

On Linux or macOS, select `.venv/bin/python` instead. For an existing uv project,
`uv add --prerelease=allow "castia[deploy,optimize,test]"` and `uv run python`
provide the same package and CLI.

`deploy` supplies YAML tooling. `optimize` adds the candidate resolver and YAML
tooling. `test` adds pytest and YAML tooling. Castia does not install the Azure
CLI or azd; install and authenticate those separately for live work.
Scaffolding creates files only. Its generated `DEPLOYMENT.md` describes the
existing Foundry resources and azd settings needed for deployment.

## New starter agent: do this, not an Agent Framework sample

When creating a new Castia starter in a clean repo, keep the implementation
Castia-native. Do not scaffold an Agent Framework sample and call it done. The
starter app should import Castia directly, register a Responses handler, and
load `.agent_configs` lazily inside the model provider.

Minimum starter files:

```text
starter-castia-agent\
  main.py
  pyproject.toml
  requirements.txt
  .env.example
  .gitignore
  azure.yaml
  .agent_configs\
    baseline\
      instructions.md
      metadata.yaml
```

Use `pyproject.toml` as the local app contract and keep `requirements.txt` as
the hosted code-deploy runtime mirror:

```toml
[project]
name = "starter-castia-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "castia[optimize]==0.6.0",
    "python-dotenv>=1.0.1",
]

[project.optional-dependencies]
test = ["pytest>=8"]

[tool.uv]
package = false
```

`.env.example` for the user to copy to `.env`:

```text
FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=<deployment-name>
```

Add `.env` to `.gitignore`. The playground reads `.env` from the selected agent
folder and uses those values for local start and hosted deploy guidance. The
entrypoint should load `.env`, then fail before serving if either setting is
missing, placeholder-shaped, or malformed.

`main.py` for a no-tools starter:

```python
from pathlib import Path

from castia import Agent, Depends, Model, configured_model, load_agent_config

app = Agent(name="starter-castia-agent")


def model_provider() -> Model:
    config = load_agent_config(Path(__file__).parent / ".agent_configs")
    if not (config.instructions or "").strip():
        raise RuntimeError("No baseline instructions loaded from .agent_configs.")
    return configured_model(config)()


@app.responses()
async def reply(text: str, model: Model = Depends(model_provider)) -> str:
    return await model.respond(text)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088)
```

`azure.yaml` for Foundry code deploy:

```yaml
name: starter-castia-agent
services:
  starter-castia-agent:
    project: .
    host: azure.ai.agent
    language: python
    kind: hosted
    name: starter-castia-agent
    description: A Castia starter agent serving Foundry Responses.
    codeConfiguration:
      runtime: python_3_13
      entryPoint: main.py
      dependencyResolution: remote_build
    protocols:
      - protocol: responses
        version: 2.0.0
    env:
      AZURE_AI_MODEL_DEPLOYMENT_NAME: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}
```

Baseline config:

```yaml
# .agent_configs/baseline/metadata.yaml
model: gpt-4o
instruction_file: instructions.md
```

Before live local testing, copy `.env.example` to `.env`, fill
`FOUNDRY_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run from
the app root so `.env` and `.agent_configs` resolve naturally:

```powershell
uv sync --project <agent-root>
$env:HOST = "127.0.0.1"
uv run --directory <agent-root> python main.py
```

Do not commit `.env`,
endpoints, subscription IDs, tenant IDs, resource groups, tokens, `.azure`,
`.venv`, `__pycache__`, `appPackage.zip`, or `TEAMS_APP_SETUP.md`.

## Write a complete Responses agent

Replace the scaffold's `main.py` with this file. It keeps the generated baseline
under `.agent_configs/baseline/` and serves only Responses. Model creation and
candidate loading happen on first use, after telemetry setup.

<!-- example: responses-agent -->
```python
from pathlib import Path

from castia import (
    Agent,
    Depends,
    Model,
    configured_model,
    load_agent_config,
    toolbox_mcp_tool,
    toolbox_token,
)

app = Agent(name="my-agent")


def model_provider() -> Model:
    config = load_agent_config(Path(__file__).parent / ".agent_configs")
    return configured_model(config)()


def toolbox_specs(token: str | None = None) -> list[dict]:
    tool = toolbox_mcp_tool(
        token=token,
        allowed_tools=("web",),
        server_description="Use web for current public facts. Cite the sources you use.",
        descriptions={"web": "Search current public web information."},
        param_guidance={"web": {"search_query": "A concise public web search query."}},
    )
    return [tool] if tool else []


app.tools(toolbox_specs)


@app.responses()
async def reply(text: str, model: Model = Depends(model_provider)) -> str:
    specs = toolbox_specs()
    if specs:
        specs = toolbox_specs(token=await toolbox_token())
    return await model.respond_with_tools(
        text, tools=[], activity=None, extra_specs=specs,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8088)
```

Set `HOST=127.0.0.1` for local-only runs. Hosted deployments need `0.0.0.0`
so platform ingress can reach the process.

Foundry hosted agents reserve all `FOUNDRY_*` and `AGENT_*` container
variables. Keep `FOUNDRY_PROJECT_ENDPOINT` in `.env` for local development or
process/azd host-side context for Castia checks; do not declare it under a
hosted service's `env:` block in `azure.yaml`.

`app.tools(...)` declares a pure provider for baseline generation. It must not
fetch tokens or make network calls. The handler adds a fresh toolbox bearer
when serving a request. Without toolbox configuration, this example uses the
model with no tools.

`web` and `search_query` are names from the toolbox used in the verified example
below. Inspect your own server's `tools/list` result before choosing names.
Castia's guidance describes existing parameters; it does not rename them,
replace the upstream schema, or implement MCP execution locally.

### Test without Azure

Replace the generated `tests/test_agent.py` too. The generated test targets the
original three-protocol scaffold; this application serves only Responses.

<!-- example: responses-test -->
```python
import asyncio

from castia.building import AgentTestHarness
from main import app, model_provider


class OfflineModel:
    async def respond_with_tools(self, text, *, tools, activity, extra_specs):
        return f"Echo: {text}"


def test_responses(monkeypatch):
    async def offline_token():
        return "offline-test-token"

    monkeypatch.setattr("main.toolbox_token", offline_token)

    async def check():
        async with AgentTestHarness(
            app, dependency_overrides={model_provider: OfflineModel},
        ) as test:
            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.status_code == 200
            assert response.json()["output_text"] == "Echo: hello"

    asyncio.run(check())
```

Run these from the generated project. The first command reconciles
`azure.yaml` with the new Responses-only registration; it does not deploy.

```powershell
& $python -m castia deploy --app main:app
& $python -m castia optimize --app main:app
& $python -m castia build check .
& $python -m castia build test . --timeout 60
& $python -m castia eval check --config eval.yaml
```

`build check` reports missing model/project settings as failures. Configure the
nonsecret settings below before using it as a readiness gate. Offline protocol
tests still run with an explicit model override. Passing them proves local
adapter behavior, not cloud access or model quality.
The scaffold's evaluation seed is an example, not a quality benchmark. Replace
it with reviewed cases and configure the deployed agent and evaluator models
before authorizing an evaluation or optimizer job.

## Choose credentials and authorize a live call

Use an existing project and an existing model deployment. `gpt-4o` below must
be the deployment's name, not a request to create one.

```powershell
az login --tenant "<your-tenant-id>"
$env:TOOLBOX_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>/toolboxes/<toolbox>/mcp?api-version=v1"
uv run --directory <agent-root> python main.py
```

`Model` and `toolbox_token()` use `DefaultAzureCredential`. Locally that can use
your Azure CLI login. In a hosted process it can use managed identity. The
identity needs access to the selected project, deployment, and toolbox.
Castia does not grant roles, deploy models, create toolboxes, or load `.env`
files for arbitrary applications. The starter app deliberately loads `.env`
with `python-dotenv`; other apps must load or export settings before
constructing `Model`.

`TOOLBOX_ENDPOINT` is optional. Other supported forms and their precedence are
in [Consume a toolbox](README.md#consume-a-toolbox). Pass the chosen settings
through the hosted service's environment when deploying; an azd environment
entry alone does not put it in the container.

In a second PowerShell window, readiness is local and does not invoke a model.
The POST invokes live Foundry inference and may execute the toolbox. Get
approval for that spend and tool access before sending it.

```powershell
Invoke-RestMethod http://127.0.0.1:8088/readiness
Invoke-RestMethod http://127.0.0.1:8088/responses -Method Post -ContentType "application/json" -Body '{"input":"Find the current Python release and cite a source."}'
```

This is a local consumer calling live Foundry services. It does not prove a
hosted deployment, a Teams response, or Graph authorization.

## Resolve candidates deliberately

The scaffold writes `metadata.yaml` with `model` and `instruction_file`, plus
`instructions.md`. `castia optimize` writes declared tools to `tools.json` and
adds both `tool_file` and `tools_file` metadata pointers. Run it with toolbox
configuration present if the baseline should contain the web tool.

`load_agent_config` returns `model`, `instructions`, `source`, and
`tool_definitions`. Check `source` before a live test. A missing resolver or a
resolution failure falls back to environment defaults without instructions.
That fallback is useful for startup but does not prove the candidate loaded.
The default model environment variable does not override a resolved baseline's
model.

Candidate precedence is inline `OPTIMIZATION_CONFIG`, then the configured
resolver endpoint, then local files, then environment defaults.
The explicit path in `model_provider` anchors local files to the application.
Set `OPTIMIZATION_CANDIDATE_ID` to select an applied candidate. Restart the
process after changing its configuration because dependencies are cached.

See [Optimizer-readiness](README.md#optimizer-readiness) for the request preview,
explicit job submission, status, apply, and azd deployment handoff. The optimizer
requires a deployed Responses-only agent. A local HTTP process is insufficient;
for an existing multi-protocol agent, `app.responses_only()` makes a sibling
that still needs its own deployment.

### What has been verified live

[Issue #13's consumer proof](https://github.com/sethjuarez/castia/issues/13#issuecomment-5691920220)
recorded 16/16 native MCP web calls using `gpt-4o` version `2024-11-20`.
It covered direct guidance and controlled candidate fixtures through
`apply_candidate_config` -> `load_agent_config` -> `configured_model` ->
`Model.respond_with_tools`. Only `server_description` varied. Native MCP
execution and the upstream `search_query` schema stayed unchanged.

The consumer ran locally against live Foundry inference and toolbox services.
These were controlled candidate fixtures, not newly optimizer-generated
candidates. This evidence does not establish a new hosted deployment, a
quality improvement, or RFT training.

## Pick the next operation

| Operation | Boundary | Details |
| --- | --- | --- |
| `build scaffold/check/test`, `deploy --check`, baseline `optimize --check` | Offline; imported application code must remain free of network side effects | [Build guide](LIFECYCLE.md#build-a-project-and-exercise-its-protocols) |
| `eval check`, `eval generate/run --dry-run`, `optimize run --dry-run` | Offline validation or request preview; no service job | [Evaluation](README.md#building-a-scored-eval-suite), [optimizer](README.md#optimizer-readiness) |
| `observe suite check`, `observe drift --dry-run` | Offline schema and coverage checks; no live proof | [Observation](LIFECYCLE.md#inspect-traces-and-run-a-drift-suite) |
| Inference, `eval generate/run`, `optimize run`, `observe ... --live` | Live calls; model, tool, evaluator, or optimizer work can be billable | Same guides; approve the target and limits first |
| Optimizer status/apply/cancel, `eval update`, trace queries | Remote reads or mutations; not offline and not a new optimizer submission | Inspect effects and permissions before calling |
| `lifecycle` snapshots, datasets, comparisons, promotion | Local evidence; callbacks can execute arbitrary code and live operations | [Lifecycle evidence](LIFECYCLE.md#keep-evaluation-evidence-together) |
| `finetune check/grader`, `finetune submit --dry-run` | Offline only; SFT/DPO/RFT wire contracts remain provisional | [Fine-tuning](README.md#fine-tuning-sft-dpo-and-rft) |

`--live` does not authorize optimizer submission in observe; that also needs
`--allow-optimizer-submit`. Neither authorizes training. SFT, DPO, or RFT
submission and checkpoint deployment require separate approval. No observation
command trains a model.

## Import and feature boundaries

Prefer the short public API (`from castia import Agent, Model`). Lower-level
imports use capability paths, such as `castia.inference.tools`,
`castia.integrations.graph.mail`, `castia.optimizing.jobs`, and
`castia.observe.configuration`. The [package map](README.md#package-organization)
lists the owners. The 31 former flat submodules are intentionally removed;
there are no aliases. Update old import and monkeypatch strings before running
an older example.

Python is the shipped SDK. The Rust package and Typra generation are
experimental and not published. Prompty integration and a managed agent-memory
API are not shipped. Do not invent their imports, dependencies, or CLI commands.

For trace queries and content-recording controls, read
[the lifecycle guide](LIFECYCLE.md) and [the trace guide](TRACING.md).
The repository's usage skills link to these consumer docs; the installed
package's `python -m castia <family> --help` is the flag reference.

# Prompty agent example

This example exercises Castia's optional Prompty runtime harness while keeping
`.agent_configs` as the optimizer contract. The app serves the Foundry Responses
protocol with Castia, then delegates each user turn to `castia.prompty`.

It demonstrates:

- `castia[optimize,prompty]` as an explicit dependency boundary;
- `register_foundry_default_connection()` using the same Foundry endpoint and
  managed identity / developer credential path as Castia model calls;
- `register_prompty_otel_tracing()` on the active Microsoft OTel provider, with
  prompt/response content gated by
  `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`;
- `configured_prompty_runner(config)` projecting `.agent_configs` into a
  Prompty-backed turn runner;
- a local Python function tool named `local_agent_fact`;
- optional host-side toolbox MCP execution through Prompty function tools.

## Offline validation

From the repository root:

```powershell
Set-Location packages\python
uv venv --python 3.13
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
uv pip install --python $python --prerelease=allow -e ".[deploy,optimize,prompty,test]"
Set-Location ..\..\examples\python\prompty-agent
& $python -m pytest -q
```

The tests monkeypatch the Prompty runner and toolbox client, so they do not
require Azure credentials, model access, or network calls.

This example's deployment files install Castia from the
`sethjuarez/prompty-runtime-harness` branch while the Prompty runtime harness is
pre-release. After a release includes `castia.prompty`, replace that direct Git
dependency with the released `castia[optimize,prompty]` version.

## Local live run

Copy `.env.example` to `.env`, fill `FOUNDRY_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run from this directory:

```powershell
& $python main.py
Invoke-RestMethod http://127.0.0.1:8088/readiness
Invoke-RestMethod http://127.0.0.1:8088/responses `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"input":"Use the local_agent_fact tool to prove local functions work."}'
```

The `local_agent_fact` tool is always available and is registered in-process with
Prompty's function-tool dispatcher. To expose toolbox-backed tools to the same
Prompty loop, set
`PROMPTY_TOOLBOX_ALLOWED_TOOLS` to a comma-separated list of MCP tool names, for
example `contracts-kb-mcp___knowledge_base_retrieve`. The example resolves the
toolbox endpoint through Castia's existing `TOOLBOX_*` environment conventions
and mints the toolbox token for `https://ai.azure.com/.default`.

This example is trace-focused, so hosted deployment opts into
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true` in `azure.yaml`. Remove or
set that value to `false` for deployments where prompt/response text must not be
written to Application Insights.

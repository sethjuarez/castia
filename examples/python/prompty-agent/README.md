# Prompty agent example

This example exercises Castia's optional Prompty runtime harness while keeping
`.agent_configs` as the optimizer contract. The app serves the Foundry Responses
protocol with Castia, then delegates each user turn to `castia.prompty`.

It demonstrates:

- `castia[optimize,prompty]` as an explicit dependency boundary;
- `prompty_runner_provider(...)`, which registers the Foundry connection, loads
  `.agent_configs`, optionally preflights the toolbox, and caches the Prompty
  runner;
- local Castia `Tool` objects (`local_agent_fact`, `local_trace_marker`,
  `local_review_checkpoint`) run host-side by the Prompty loop, each emitting an
  `execute_tool` span with `gen_ai.tool.type=local_example`;
- `register_prompty_otel_tracing()` on the active Microsoft OTel provider, with
  prompt/response content gated by
  `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`;
- token usage summed across the tool loop in the Responses `usage` object;
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

This example's deployment files install the released `castia[optimize,prompty]`
extra so local `uv run --directory . python main.py` and hosted remote builds
resolve the same Prompty runtime harness without a source checkout.

## Local live run

Copy `.env.example` to `.env`, fill `FOUNDRY_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run from this directory:

```powershell
uv run --directory . python main.py
Invoke-RestMethod http://127.0.0.1:8088/readiness
Invoke-RestMethod http://127.0.0.1:8088/responses `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"input":"Run a three-step local tool trace. First call local_agent_fact with topic canvas. After you have that result, call local_trace_marker with topic canvas and stage second-tool. After you have that result, call local_review_checkpoint with topic canvas and previous second-tool. Quote all three tool results exactly, in order."}'
```

The three local tools are Castia `Tool` objects passed to
`prompty_runner_provider(tools=...)`; Castia projects their JSON schemas into
Prompty function tools and runs them host-side. Asking for all three in one turn
creates multiple `execute_tool` spans for trace timeline inspection. To expose
toolbox-backed tools to the same Prompty loop, set
`PROMPTY_TOOLBOX_ALLOWED_TOOLS` to a comma-separated list of MCP tool names, for
example `contracts-kb-mcp___knowledge_base_retrieve`. The provider resolves the
toolbox endpoint through Castia's `TOOLBOX_*` environment conventions,
preflights `tools/list`, and raises `ToolboxRuntimeConfigError` with
diagnostics when a named tool is missing.

This example is trace-focused, so hosted deployment opts into
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true` in `azure.yaml`. Remove or
set that value to `false` for deployments where prompt/response text must not be
written to Application Insights.

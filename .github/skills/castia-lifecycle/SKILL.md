---
name: castia-lifecycle
description: "Use the castia Python SDK to build a Foundry agent, attach native MCP tools, run offline checks, evaluate or optimize an agent, and prepare separately authorized RFT work. Covers canonical imports, uv extras, credentials, evidence boundaries, and free versus billable operations. Start with the consumer agent guide for complete runnable examples."
---

# Castia package usage

Use the [consumer agent guide](../../../packages/python/AGENTS.md) as the starting
point. It contains a complete Responses application and an executable offline
test, with a pure toolbox provider for baseline generation and token acquisition
only in the request handler.

Read the current CLI help before assembling commands. Run application commands
from the application's root, where `main.py`, `azure.yaml`, `eval.yaml`, and
`.agent_configs/` live. Running from the SDK source directory targets the wrong
project unless an explicit path is supplied.

## Install and import

```powershell
uv venv --python 3.13
uv pip install --prerelease=allow "castia[deploy,optimize,test]"
.\.venv\Scripts\python.exe -m castia --help
```

Python 3.11+ is supported. On Linux/macOS, use `.venv/bin/python`.
For an existing uv application, use
`uv add --prerelease=allow "castia[deploy,optimize,test]"`.
The extras supply YAML tooling, the optional candidate resolver, and tests.
Azure CLI and azd are separate installations.

Prefer `from castia import Agent, Depends, Model, configured_model`.
Lower-level imports use capability packages, such as
`castia.inference.model`, `castia.integrations.toolbox`,
`castia.evaluation.suite`, and `castia.optimizing.jobs`.
The former 31 flat submodules are removed, without aliases. Update old import
and monkeypatch paths using the [package map](../../../packages/python/README.md#package-organization).
Python is shipped; Typra generation, Rust, Prompty integration, and managed
agent memory are not shipped.

## Runtime and credentials

Register a complete `Agent`, use `@app.responses()`, and guard `app.run()` with
`if __name__ == "__main__"`. The local adapter accepts
`POST /responses` with `{"input": "..."}` and returns `output_text`.
The guide binds to loopback; hosted processes need `0.0.0.0`.

`Model` reads `FOUNDRY_PROJECT_ENDPOINT` and, absent an explicit deployment,
`AZURE_AI_MODEL_DEPLOYMENT_NAME`. Its `DefaultAzureCredential` can use a local
Azure CLI login or hosted managed identity. Neither Castia nor setting an
environment variable grants access to the project or model.
Castia does not load `.env` automatically.

For candidate resolution, anchor `load_agent_config` to the app's
`.agent_configs` directory, pass the result to `configured_model`, and inspect
`AgentConfig.source`. Resolution is best-effort and can fall back to environment
defaults without instructions. A successful response alone cannot prove a
candidate was applied.

For native MCP, use `toolbox_mcp_tool`, `toolbox_token`, and
`Model.respond_with_tools(..., tools=[], activity=None, extra_specs=[tool])`.
Read [the optimizer skill](../castia-optimizer/SKILL.md) for the pure provider,
upstream schema, and candidate guidance constraints.

## Choose the operation and its approval boundary

| Goal | Start offline | Live boundary |
| --- | --- | --- |
| Build | `build scaffold`, `build check`, `build test` | Separate azd deployment to an explicitly selected project |
| Observe | `observe suite check`, `observe drift --dry-run` | `--live` may incur model/tool costs; optimizer submission needs its own flag |
| Evaluate | `eval check`, `eval generate/run --dry-run` | `eval generate/run` submit billable work; `eval update` mutates remote assets |
| Optimize | `optimize --check`, `optimize run --dry-run` | `optimize run` submits a billable job; apply writes a fetched candidate locally |
| Record evidence | `lifecycle snapshot`, dataset and comparison records | Reviewed callbacks can execute code or live work; promotion is explicit |
| Prepare RFT | `finetune check/grader`, `finetune submit --dry-run` | Training and checkpoint deployment each need separate authorization |

Offline commands that import the user's app or a callback execute that code.
Keep registration and tool providers free of network side effects.
Status, trace queries, cancellation, and candidate retrieval are remote
operations even when they do not create a new training or optimizer job.

Evaluation suites use `eval.yaml`; optimizer baselines use `.agent_configs`.
Lifecycle evidence has its own snapshot/dataset/run schemas.
RFT accepts explicit JSONL splits and a grader; it does not consume `eval.yaml`
as a universal configuration.

## References and evidence

- [Python README](../../../packages/python/README.md) covers tool builders,
  evaluation suites, optimizer config, and provisional RFT.
- [LIFECYCLE.md](../../../packages/python/LIFECYCLE.md) covers build, observation,
  evidence, deployment verification, and existing fine-tuning job management.
- [TRACING.md](../../../packages/python/TRACING.md) covers trace labels and queries.
- [Issue #13](https://github.com/sethjuarez/castia/issues/13#issuecomment-5691920220)
  proves local consumer calls through live Foundry inference and native MCP,
  including controlled candidate fixtures. It does not prove a fresh
  optimizer-generated candidate, a new hosted deployment, or training.

RFT wire acceptance remains provisional. Offline validators do not establish
that Foundry accepts the submitted grader/hyperparameter shape. Do not start
training merely to complete a lifecycle checklist.

---
name: castia-optimizer
description: "Use Castia's Foundry Agent Optimizer lifecycle, canonical .agent_configs baselines, native MCP toolbox guidance, and optimize run/status/cancel/apply commands. Covers offline previews, candidate resolution, live evidence limits, and the separate azd deployment handoff."
---

# Castia optimizer usage

Read the [consumer agent guide](https://github.com/sethjuarez/castia/blob/main/packages/python/AGENTS.md)
for the complete runnable application. The [Python README](https://github.com/sethjuarez/castia/blob/main/packages/python/README.md#optimizer-readiness)
owns the detailed optimizer commands. This skill records the constraints to
check before calling them.

## Ownership and project shape

Castia owns baseline files, toolbox guidance sidecars, request construction,
job inspection/cancellation, and candidate application. azd owns hosted
deployment. `python -m castia deploy` only reconciles manifest protocols.

Run from the consumer application root. Install
`uv pip install --prerelease=allow "castia[deploy,optimize]"` in its environment.
Use canonical modules such as `castia.optimizing.config`,
`castia.optimizing.baseline`, and `castia.optimizing.jobs`; former flat modules
are removed, without redirects.

The project needs `eval.yaml` and `.agent_configs/baseline/`. Baseline metadata
uses this shape.

```yaml
model: gpt-4o
instruction_file: instructions.md
tool_file: tools.json
tools_file: tools.json
```

Use the same pure tool provider in `app.tools(...)` and in the request handler.
The provider must not acquire credentials or call remote services.
`python -m castia optimize` writes `tools.json` and both metadata pointers.
Anchor `load_agent_config` to the application's config directory, pass it to
`configured_model`, and verify its `source` before claiming the candidate loaded.
Restart after changing a cached dependency's config.

The optimizer requires a deployed Responses-only agent. For an agent serving
other protocols too, `app.responses_only()` makes a sibling with the same
Responses handler and declared tools. It still needs a separate deployment.

## Preserve native MCP execution

Use upstream names discovered through `tools/list`. In the verified web
toolbox, those are `web` and `search_query`.

```python
from castia import toolbox_mcp_tool

def web_tools():
    tool = toolbox_mcp_tool(
        allowed_tools=("web",),
        server_description="Use web for current public facts and cite the sources.",
        descriptions={"web": "Search current public web information."},
        param_guidance={"web": {"search_query": "A concise public web search query."}},
    )
    return [tool] if tool else []
```

At request time, rebuild the same spec with `token=await toolbox_token()` or
the configured project connection. Pass it through `Model.respond_with_tools`.
Registering it with `app.tools` alone does not attach it to a model request.
The [consumer example](https://github.com/sethjuarez/castia/blob/main/packages/python/AGENTS.md#write-a-complete-responses-agent)
shows both paths without putting a bearer in baseline files.

Tool guidance changes `server_description`; it does not replace the remote
schema or implement a local function wrapper. Castia emits selected tool names
such as `web` to `tools.json`, stores optimizer guidance in a private
`x-castia-optimizer-tool-definitions` sidecar, and removes `x-castia-*` fields
before sending Responses payloads. The older `toolbox___web` spelling is an
accepted override-key alias, not the name to emit in new configs.

[Issue #13](https://github.com/sethjuarez/castia/issues/13#issuecomment-5691920220)
recorded 16/16 native web calls on `gpt-4o` version `2024-11-20`. A local
consumer exercised direct guidance and controlled fixtures through
`apply_candidate_config`, `load_agent_config`, `configured_model`, and
`Model.respond_with_tools`. Only `server_description` varied; upstream schema
and native execution were unchanged. It did not create a hosted deployment or
generate a fresh optimizer candidate. Do not cite it as quality-improvement
or training evidence.

## Commands and effects

| Command suffix after `python -m castia` | Effect |
| --- | --- |
| `optimize --check` | FREE offline baseline drift check; writes nothing |
| `optimize` | FREE offline baseline reconciliation; writes local files |
| `optimize run --dry-run` | FREE offline payload preview |
| `optimize run` | Billable job submission; waits unless `--no-wait` |
| `optimize status --watch` | Remote polling; does not create a job |
| `optimize apply` | Fetches a candidate and writes local config; does not deploy |
| `optimize cancel` | Requests cancellation of an existing remote job |

Inspect the preview's prompt, tools, evaluator references, dataset, deployed
agent, models, and candidate cap before authorizing submission.
After cancellation, inspect status to establish that the job stopped.
The cancellation command reports a request, not completed cleanup.
Use explicit project settings. The current wire contract is
`/agent_optimization_jobs`, API version `v1`,
`Foundry-Features: AgentsOptimization=V2Preview`, `{"inputs": ...}`, and scope
`https://ai.azure.com/.default`.

After apply, set `OPTIMIZATION_LOCAL_DIR` and `OPTIMIZATION_CANDIDATE_ID` in the
intended service environment, then use the reviewed azd workflow.
Setting them in a local shell does not update an already running hosted agent.
Verify both deployment state and actual runtime behavior before promotion.

## Drift checks and training boundary

[LIFECYCLE.md](https://github.com/sethjuarez/castia/blob/main/packages/python/LIFECYCLE.md#inspect-traces-and-run-a-drift-suite)
describes local observation suites, limits, and cleanup reporting.
`observe drift --dry-run` is offline. `--live` permits configured live probes;
optimizer submission additionally needs `--allow-optimizer-submit`.
No observation command submits training or applies/deploys a candidate.

The repository's `foundry-optimizer-live.yml` is an optional manual GitHub
smoke, with no schedule. It uses the `foundry-live` environment and submits
billable work against an existing agent. Check the workflow's current inputs
and environment declarations before using it; do not infer authorization from
its presence in the repository.

RFT remains provisional and separately authorized. Native MCP or optimizer
success does not establish that a grader or training request is accepted.

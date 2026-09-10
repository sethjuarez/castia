---
name: castia-optimizer
description: "Make Castia Python hosted agents optimizer-ready and run the Castia-owned Foundry Agent Optimizer lifecycle. Use for toolbox MCP guidance overrides, `.agent_configs` baseline drift, `tools.json`, `python -m castia optimize run/status/cancel/apply`, daily live optimizer drift checks, or applying optimizer candidates before azd deployment. Triggers include: 'castia optimize', 'Foundry Agent Optimizer', 'toolbox optimizer', 'tools.json', '.agent_configs', 'OPTIMIZATION_CANDIDATE_ID', 'optimizer candidate', 'preview service drift', and 'federated toolbox tools'."
---

# castia-optimizer — own optimizer correctness, hand deployment to azd

Use this skill when an agent is working on Castia's optimizer path, especially
toolbox/federated MCP tools, `.agent_configs`, or live Foundry Agent Optimizer
jobs.

## Boundary

Castia owns optimizer correctness:

- canonical `.agent_configs/baseline/` files;
- toolbox optimizer sidecar definitions;
- request construction for `python -m castia optimize run`;
- job inspection/cancellation;
- local candidate materialization with `python -m castia optimize apply`.

`azd` remains the hosted-agent deployment rail. Do not replace `azd deploy`; use
Castia to prepare/apply config, then hand the selected candidate to azd through
environment variables.

## Free vs billable commands

Run free gates before any service-side job:

| Command | Cost | Purpose |
|---|---:|---|
| `python -m castia optimize --check` | Free | Report `.agent_configs/baseline` drift without writing. |
| `python -m castia optimize` | Free | Refresh baseline `tools.json` and metadata pointers. |
| `python -m castia optimize run --dry-run` | Free | Print the exact Foundry optimizer payload. |
| `python -m castia optimize run` | Billable | Submit and wait for a Foundry optimizer job. |
| `python -m castia optimize status --watch` | Free | Poll a Castia-submitted optimizer job. |
| `python -m castia optimize apply` | Free | Fetch a candidate config and write it under `.agent_configs/<candidate-id>/`. |
| `python -m castia optimize cancel` | Free | Cancel the latest or specified optimizer job. |

## Required project shape

An optimizer-ready Castia agent has:

```text
eval.yaml
.agent_configs/
  baseline/
    metadata.yaml
    instructions.md
    tools.json          # optional, but required for tool-description optimization
```

`metadata.yaml` should include both optimizer metadata spellings for ecosystem
compatibility:

```yaml
model: gpt-4o
instruction_file: instructions.md
tool_file: tools.json
tools_file: tools.json
```

Runtime code should consume config through `configured_model()`:

```python
from castia import Depends, Model, Router, configured_model

router = Router()
gpt = configured_model()

@router.responses()
async def reply(text: str, model: Model = Depends(gpt)) -> str:
    return await model.respond(text)
```

The optimizer requires a Responses-capable hosted agent. If the app also
supports Activity or Invocations, expose/deploy a responses-only sibling with
`Agent.responses_only()` for optimization.

## Toolbox/federated MCP tools

For toolbox tools, the service-visible tool name is the MCP tool name, such as
`web`; it is not `server_label___web`. Use post-facto overrides when creating
the raw MCP spec so the optimizer can see and improve the tool definition:

```python
from castia import toolbox_mcp_tool, toolbox_token

tool = toolbox_mcp_tool(
    token=await toolbox_token(),
    allowed_tools=("web",),
    server_description="Use the web toolbox only for current public facts.",
    descriptions={
        "web": "Search current public web information.",
    },
    param_guidance={
        "web": {
            "query": "A concise public web search query.",
        },
    },
)
```

Castia emits these definitions into `tools.json` via a private
`x-castia-optimizer-tool-definitions` sidecar and strips all `x-castia-*`
metadata before sending Responses payloads. Keep legacy `server_label___tool`
names only as aliases when applying candidates; do not emit that spelling in new
optimizer configs.

## Native optimizer workflow

1. Refresh and check the baseline:

   ```bash
   cd packages/python
   uv pip install -e ".[deploy,optimize,test]"
   python -m castia optimize
   python -m castia optimize --check
   ```

2. Preview the service payload:

   ```bash
   FOUNDRY_PROJECT_ENDPOINT=https://.../api/projects/<project> \
     python -m castia optimize run --dry-run
   ```

3. Submit only after the dry-run payload contains the expected prompt, tools,
   evaluator refs, dataset, model, and candidate cap:

   ```bash
   FOUNDRY_PROJECT_ENDPOINT=https://.../api/projects/<project> \
     python -m castia optimize run
   ```

4. Inspect or poll the job:

   ```bash
   python -m castia optimize status --watch
   ```

5. Apply the winning candidate locally:

   ```bash
   python -m castia optimize apply
   ```

6. Deploy with azd using the applied candidate:

   ```bash
   export OPTIMIZATION_LOCAL_DIR=.agent_configs
   export OPTIMIZATION_CANDIDATE_ID=<candidate-id>
   azd deploy
   ```

On Windows PowerShell, use `$env:OPTIMIZATION_LOCAL_DIR = ".agent_configs"` and
`$env:OPTIMIZATION_CANDIDATE_ID = "<candidate-id>"`.

## Service wire facts

The Castia client matches the current azd/live service contract:

- endpoint path: `/agent_optimization_jobs`;
- API version: `v1`;
- required header: `Foundry-Features: AgentsOptimization=V2Preview`;
- submit body envelope: `{"inputs": <optimizer-request>}`;
- auth scope: `https://ai.azure.com/.default`.

If a live job starts failing, compare the request emitted by
`optimize run --dry-run` against these facts before changing higher-level code.

## Daily live drift workflow

The repo includes `.github/workflows/foundry-optimizer-live.yml` as the preview
service drift canary. It runs daily and on manual dispatch, builds a throwaway
eval suite on the runner, submits one billable optimizer candidate, waits for
completion, and applies the best candidate locally.

Enable it by configuring the `foundry-live` GitHub environment:

| Name | Type | Purpose |
|---|---|---|
| `AZURE_CLIENT_ID` | Secret | OIDC app/client id for Azure login. |
| `AZURE_TENANT_ID` | Secret | Tenant id. |
| `AZURE_SUBSCRIPTION_ID` | Secret | Subscription id. |
| `FOUNDRY_PROJECT_ENDPOINT` | Variable | Target Foundry project endpoint. |
| `FOUNDRY_OPTIMIZER_AGENT_NAME` | Variable | Pre-deployed smoke agent name. |
| `FOUNDRY_OPTIMIZER_AGENT_VERSION` | Variable | Optional pinned agent version. |
| `FOUNDRY_EVAL_MODEL` | Variable | Optional evaluator model, defaults to `gpt-4o`. |
| `FOUNDRY_OPTIMIZE_MODEL` | Variable | Optional optimizer model, defaults to `gpt-5`. |

The Azure principal must have enough Foundry access to submit optimizer jobs and
invoke the pre-deployed smoke agent/toolbox.

## Quality gate for optimizer changes

Before concluding optimizer work, run from `packages/python`:

```bash
uv pip install -e ".[deploy,optimize,test]"
python -m pytest -q -W error
uvx ruff check .
uv build
git --no-pager diff --check
```

For changes to the live workflow, also parse all workflow YAML files locally:

```bash
python -c "from ruamel.yaml import YAML; import pathlib; [YAML().load(open(p, encoding='utf-8')) for p in pathlib.Path('../../.github/workflows').glob('*.yml')]"
```

Rubber-duck or code-review optimizer changes that touch service wire shape,
candidate application, GitHub Actions, or release automation.

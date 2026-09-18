# Build, observe, and improve an agent

For a complete consumer application, start with [AGENTS.md](AGENTS.md).
It installs the extras, runs a Responses agent with native MCP, and tests the
same code offline. This guide covers the evidence and operational workflows.

Castia keeps lifecycle work in separate packages. Existing imports such as
`from castia import Agent, Model, Message` and existing CLI commands still work.

| Package | Owns |
| --- | --- |
| `castia.building` | Project generation, readiness checks, and local protocol tests |
| `castia.evaluation` | Evaluation-suite configuration, rubrics, and azd evaluation commands |
| `castia.observe` | Telemetry configuration, tracing, execution records, App Insights queries, functional checks, and reports |
| `castia.optimizing` | Candidate configuration, baseline generation, and optimizer jobs |
| `castia.lifecycle` | Snapshots, datasets, evaluations, candidate evidence, and promotion records |
| `castia.delivery` | Manifest generation and explicit deployment of a named service through azd |
| `castia.finetuning` | SFT/DPO/RFT preparation/submission and inspection of existing training jobs |

Runtime helpers load without initializing lifecycle tooling or cloud clients.
Azure and OpenAI clients are created only by operations that need them. The
[package map](README.md#package-organization) also covers protocols, runtime,
hosting, messaging, inference, and integrations. Use those canonical paths for
lower-level imports; the old flat modules have been removed.

## What a passing check means

A local protocol test exercises the Castia HTTP adapter with captured outgoing
messages. It cannot establish that Teams delivered a card or that a hosted
identity can access Graph.

A live model check exercises inference. It cannot establish that a hosted
Activity endpoint works. A live optimizer job exercises evaluation and candidate
generation. It cannot establish that the selected candidate was deployed.

Keep these results separate. Missing configuration, expired credentials, absent
test fixtures, and deferred training must remain visible in the report.
An untested feature is never a pass.

The feature catalog is a coverage inventory. Some features require a dedicated
probe supplied by the application, especially channel interactions and Graph
operations. Do not point write tests at real inboxes, conversations, or files.
Use isolated fixtures with explicit expected effects and cleanup.

## Run locally

For an application using a published package, follow
[the consumer setup](AGENTS.md#start-with-the-installed-package). The commands
below are for contributors testing the SDK source.

From `packages/python`, create the test environment if it is missing.

```powershell
uv venv --python 3.13
uv pip install -e ".[deploy,optimize,test]"
.\.venv\Scripts\python.exe -m pytest -q -W error
.\.venv\Scripts\python.exe -m castia --help
```

On Linux and macOS, the environment interpreter is `.venv/bin/python`.
Use a local Azure login for live operations. Never put tokens, credentials, or a
dump of your environment into a snapshot or committed configuration.

Always select the project explicitly. An azd default environment can point to a
different project from the one being tested.

## Build a project and exercise its protocols

```powershell
python -m castia build scaffold .\my-agent --name my-agent --model gpt-4o
python -m castia build check .\my-agent
python -m castia build check .\my-agent --deployment
python -m castia build test .\my-agent --timeout 60
```

Scaffolding refuses conflicting files. It writes an agent entrypoint, deployment
intent, baseline instructions, an evaluation seed, and a local protocol test.
It does not create Azure resources.

The readiness report identifies missing settings and mismatched local artifacts.
`--deployment` also requires region, subscription, and project ARM ID settings.
It checks supplied or process settings, not a saved azd environment, and labels
cloud checks it did not perform. `build test` runs the
project's pytest suite in a subprocess with offline guards and a timeout.
The guards are test isolation, not a security sandbox for untrusted Python.

The in-process test API uses the actual HTTP adapters.

```python
from castia import Agent
from castia.building import AgentTestHarness

app = Agent(name="echo")

@app.responses()
async def echo(text: str) -> str:
    return text

async def check_echo():
    async with AgentTestHarness(app) as harness:
        response = await harness.client.post("/responses", json={"input": "hello"})
        assert response.json()["output_text"] == "hello"
```

For model-backed handlers, supply explicit dependency overrides.
The harness captures connector egress and restores patched state on exit,
including cancellation. Harness contexts are exclusive within a process.

## Keep evaluation evidence together

`castia.lifecycle` stores versioned, content-addressed records. An agent snapshot
names its source hashes, dependency versions, model configuration, instructions,
and tools. A dataset records reviewed examples and disjoint training and heldout
IDs. Runs retain per-example metrics and failures across repeated evaluations.
Decisions bind the compared runs and acceptance rules to the candidate.

```powershell
python -m castia lifecycle snapshot --root .\my-agent --config snapshot.json
python -m castia lifecycle dataset --traces reviewed.jsonl --redactor adapters:redact --redaction-version v1 --allow-code
python -m castia lifecycle evaluate --agent $agent --dataset $dataset --evaluator evaluator.json --callback adapters:evaluate --repeats 3 --concurrency 2 --timeout 30 --allow-code
python -m castia lifecycle compare $baselineRun $candidateRun --gate gate.json
python -m castia lifecycle stage --root .\my-agent --file .agent_configs/baseline/instructions.md --baseline $baselineAgent --agent $candidateAgent
```

Use the artifact IDs printed by each command. `--store` chooses the local
artifact directory. The snapshot config has `source_files` as relative paths,
`dependencies` and `model` as objects, `instructions` as a string, and `tools`
as an optional list of objects. Include the candidate configuration files in
the snapshot before evaluating it.

The adapters are application code. Castia does not know what a correct answer
means for your agent. The evaluation callback receives the agent snapshot,
example, and repetition index and returns measured metrics. The default gate
requires `quality`; missing required metrics fail the comparison. Configure
latency or cost gates only when the evaluator supplies those measurements.
Timeouts rely on cooperative async callbacks. Give external requests their own
timeouts; cancelling a coroutine does not cancel a remote job.

Dataset input uses the `ReviewedTrace` fields, including `reviewer`, `group`,
and `approved`. Related examples belong to the same group so they cannot cross
the split. Reference origins are `human`, `authoritative`, or `deterministic`.
Use `deterministic` for answers computed by a rule or test fixture; the stored
dataset and JSONL export preserve that origin.
Review and redaction are explicit attestations and callbacks, not
automatic proof that a trace is safe to retain. Artifact hashes detect changes;
they are not signatures from a trusted reviewer.

`--allow-code` acknowledges that importing and calling a local adapter executes
Python. `--adapter-root` selects its reviewed source directory. The existing
`castia eval` and `castia optimize` commands remain the Foundry service entry
points; lifecycle callbacks connect their results to your application's metrics.

## Inspect traces and run a drift suite

```powershell
python -m castia observe features
python -m castia observe suite init --name my-agent --out suite.json
python -m castia observe suite check --suite suite.json
python -m castia observe suite run --suite suite.json --config live.json --dry-run
python -m castia observe suite run --suite suite.json --config live.json --live --out current.json
python -m castia observe suite run --suite suite.json --config live.json --baseline known-good.json --live --out current.json
```

`suite init` includes the full feature catalog. Keep the features you intend to
monitor and configure their probes. Schema checks and dry runs can succeed
without establishing any live coverage. Only an executed report measures it.
An executed suite with a blocked, failed, or uncovered selected feature exits
nonzero. Preserve missing fixtures in the report instead of removing features
to make a run green.

Live configuration uses explicitly discovered service URLs and fixture IDs.
For a suite selecting only the three model probes, a configuration looks like
this. Confirm the model URL against the installed Foundry SDK before using it.

```json
{
  "project_endpoint": "https://example.services.ai.azure.com/api/projects/my-project",
  "model_url": "https://example.services.ai.azure.com/api/projects/my-project/openai/v1/responses",
  "model": "gpt-4o",
  "limits": {
    "max_requests": 10,
    "max_seconds": 120,
    "max_output_tokens": 128,
    "max_total_output_tokens": 1024
  }
}
```

`project_read_url` must return project identity, not an agents collection.
For an ARM project URL, set `project_read_scope` to
`https://management.azure.com/.default`. Toolbox checks require an explicit
`toolbox_url`, selected `toolbox_tools`, and a safe `toolbox_prompt`.
Optimizer status requires a job ID; candidate reads also require a candidate ID.

The built-in probes cover project reads, model responses and streaming, function
calls, hosted Responses and Invocations, toolbox calls, and optimizer reads or
bounded submissions. Application-specific probes use the Python suite API.
Do not treat the catalog as a promise that every feature has a built-in probe.

```powershell
python -m castia observe traces --app-id $appId --agent my-agent --start 2026-09-15T00:00:00Z --end 2026-09-16T00:00:00Z --limit 100 --out traces.json
python -m castia observe summarize --records traces.json
```

Trace queries require an explicit time window and agent identity. Prompt and
response content are excluded unless `--include-content` is supplied. Tag a
submitted probe span with `castia.probe_tag`, then use `observe verify` with
that tag and a bounded wait to check that telemetry arrived. Verification does
not submit a probe itself.

## Spending and unattended execution

Read-only checks do not start new model or training work. Inference, evaluation,
and optimizer submissions incur service usage. Request, token, and time limits
bound the work the client requests; they do not guarantee a monetary cap on
service-side jobs.

A timed-out deployment may continue in Azure. Verify its state before retrying.
A runner must report a failed cancellation or uncertain cleanup rather than
claiming the resource was removed.

Scheduling belongs to the local automation tool. The drift CLI runs once and
returns a report. It does not install a background service or schedule. Keep the
configuration and report directory outside the checkout if they contain private
agent data. Retain the last known-good report; never replace it with a failed
run simply because that run is newer.

The GitHub optimizer smoke is manual only. It remains available for installations
that choose to configure GitHub OIDC. Local runs do not need GitHub credentials.

## Deployment handoff

`AzdDeployment` requires a project root, service name, explicit project endpoint,
and approved project ARM resource ID. It reads the selected azd environment and
rejects mismatched stored endpoints, project IDs, or subscription before any
resource command. It freezes the selected environment name and checks the
context again after reading deployment state.

Process environment overrides alone do not select the extension's target.
Prepare the named azd environment for that same project first; the adapter
does not silently rewrite its stored values.

It checks that the named service uses `host: azure.ai.agent`, then
invokes `azd deploy` for that service. It does not invoke `azd provision` or a
separate endpoint-routing command. Review the authored manifest and its hooks;
azd can execute those hooks and apply endpoint configuration during deployment.

```python
from castia.delivery import AzdDeployment

deployment = AzdDeployment(
    ".",
    service="my-agent",
    project_endpoint="https://example.services.ai.azure.com/api/projects/my-project",
    project_resource_id=(
        "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/my-group"
        "/providers/Microsoft.CognitiveServices/accounts/example/projects/my-project"
    ),
    environment="dev",
)

# Read remote state without deploying.
receipt = deployment.verify(expected_model="gpt-4o", expected_version="3")

# Explicit deployment. The manifest must already contain the intended config.
# receipt = deployment.deploy(
#     expected_model="gpt-4o",
#     expected_candidate="candidate-id",
# )
```

The receipt identifies the validated project context, deployed version, model
environment value, candidate environment value, and code content hash when
available. Follow configuration
verification with a functional probe that proves the running agent used it.
An environment variable alone cannot prove the application loaded that value.

Use deployment and verification callbacks with the lifecycle promotion workflow.
Retain the previous configuration so rollback can redeploy it. A failed promotion
must not advance the known-good record, and it must still record any uncertainty
about the actual remote state.

```powershell
python -m castia lifecycle promote --journal .\journal --expected-revision $revision --candidate $candidate --decision $decision --deploy adapters:deploy --verify adapters:verify --allow-code --execute
python -m castia lifecycle rollback --journal .\journal --expected-revision $revision --deploy adapters:deploy --verify adapters:verify --allow-code --execute
python -m castia lifecycle show --journal .\journal
```

Read the current journal revision before a handoff. Concurrent or stale revisions
are rejected. Staged files must match the evaluated snapshot by path, hash, and
size. Structured JSON, YAML, and TOML configuration also receives credential-key
and duplicate-key checks. Review the contents before capture; those checks are
not a general data-loss prevention system.

Failure or cancellation sets `pending_cleanup` and blocks both promotion and
rollback. `lifecycle reconcile` requires an explicit verification adapter,
`--expected-revision`, `--allow-code`, and `--execute`. Its async callback receives
the journal state and must return exactly this shape after checking every
possibly outstanding operation.

```python
{
    "no_outstanding_writes": True,
    "evidence": "Terminal operation IDs and status evidence from the service",
}
```

Checking only the active agent version is insufficient. A timed-out earlier
operation could still finish and overwrite it. Reconciliation does not deploy
anything or establish that the active agent is good; recovery must still be
verified. A crashed process's stale lock requires separate ownership
reconciliation before the journal can be opened for another write.

## Fine-tuning stays explicit

`castia.finetuning.sft`, `castia.finetuning.dpo`, and
`castia.finetuning.rft` build datasets and request payloads for explicit
submission commands. Their submission contracts remain provisional until real
training jobs validate them. RFT additionally builds graders.

`FineTuningClient`, exported by `castia.finetuning`, manages existing jobs.
That client has no training submission method.

```powershell
python -m castia finetune list --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT
python -m castia finetune status job-id --watch --timeout 300
python -m castia finetune events job-id --limit 20
python -m castia finetune checkpoints job-id --limit 20
python -m castia finetune results job-id
python -m castia finetune results job-id --file-id file-id --out metrics.jsonl
python -m castia finetune handoff job-id
```

Lists read one bounded page. Use `--after` to request the next page. Status can
save a reference with `--save-reference job.json`; later commands accept
`--reference job.json`. References contain only the endpoint, job ID, and schema
version. An explicit conflicting endpoint or ID is rejected.

Result downloads check that the file belongs to the specified job, enforce
`--max-bytes`, and refuse to overwrite an existing file. Downloads may contain
private training data.

`finetune handoff` requires a succeeded job with a resulting model. It returns
the model identity for external deployment and records `not_deployed`.
`finetune cancel job-id` is an explicit mutation of that existing job.
Watching a job time out does not cancel it.

No observation or drift command submits training. Training and checkpoint
deployment remain separate user decisions.

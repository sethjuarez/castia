# castia

Idiomatic, FastAPI-style Python SDK for [Microsoft Foundry](https://ai.azure.com)
hosted agents.

`castia` lets a hosted agent speak Foundry's three wire protocols — Activity
(Teams/Bot Framework), OpenAI `responses`, and `invocations` — through
protocol-named decorators, dependency injection (`Depends`), and typed builders
for messages, Adaptive Cards, entities, and invoke envelopes. You decorate a
handler, return a value, and the framework does the rest — the auth chains,
hosting, Activity routing, and telemetry stay out of your file.

## Install

```bash
uv add castia
# With baseline resolution, CLI YAML tooling, and offline tests:
uv add --prerelease=allow "castia[deploy,optimize,test]"
```

Requires Python 3.11+. For a standalone environment, use `uv venv` followed by
`uv pip install --prerelease=allow "castia[deploy,optimize,test]"`.

The [consumer agent guide](https://github.com/sethjuarez/castia/blob/main/packages/python/AGENTS.md)
has a complete Responses application,
native MCP setup, credentials, and executable offline tests.
Use [LIFECYCLE.md](https://github.com/sethjuarez/castia/blob/main/packages/python/LIFECYCLE.md)
for build, observation, evidence, and deployment
workflows. These Markdown guides are maintained in this repository.

## Package organization

Application imports stay short (`from castia import Agent, Model, Message`).
Implementation code lives in capability packages under `src/castia`.

| Package | Implementation |
| --- | --- |
| `protocols` | Activity wire models and accessors |
| `runtime` | Agent/router composition, dependency injection, turn context, dispatch |
| `hosting` | HTTP endpoints, local-run policy, credentials, agentic identity |
| `messaging` | Activity routing, Teams surfaces, replies, cards, entities, invoke helpers, streaming, connector |
| `inference` | Model calls and executable tool definitions |
| `integrations` | Foundry toolbox adapters and Graph operations under `integrations.graph` |
| `building` | Scaffolding, readiness checks, offline protocol testing |
| `evaluation` | Evaluation-suite configuration, rubric validation, azd evaluation commands |
| `observe` | Telemetry configuration and tracing, execution records, queries, drift checks |
| `optimizing` | Candidate configuration, baseline generation, optimizer jobs |
| `finetuning` | SFT/DPO/RFT preparation/submission and existing-job management |
| `lifecycle` | Snapshots, datasets, evaluation evidence, acceptance and promotion records |
| `delivery` | Manifest generation and guarded azd deployment |

Each command family keeps its CLI handlers beside its implementation.
`__main__.py` composes those commands.

The package root contains only `__init__.py`, `__main__.py`, and `py.typed`,
alongside the capability folders. Implementation imports use their canonical
paths, such as `castia.inference.model` and `castia.optimizing.jobs`.
There are no compatibility modules or import redirects.

The former flat submodule paths have been removed. Change
`from castia.model import Model` to `from castia import Model` or
`from castia.inference.model import Model`. The root public API and CLI commands
remain unchanged. Logger categories follow the canonical module names.
The [tracing guide](https://github.com/sethjuarez/castia/blob/main/packages/python/TRACING.md)
lives with the package documentation.

Shared protocol definitions and behavioral fixtures belong in
[`spec/`](https://github.com/sethjuarez/castia/tree/main/spec). These Python modules are handwritten today.
Typra now emits the experimental Rust contract layer from those TypeSpec files,
but Python remains the shipped SDK. Generated types alone do not establish
parity; each runtime must pass the same behavioral fixtures and still provide
native host adapters.

## Quickstart

```python
from castia import Agent, Depends, Model, Teams

app = Agent(name="my-agent")

def gpt4o() -> Model:
    return Model("gpt-4o")

@app.activity(Teams.direct, Teams.group, Teams.channel_mention)
async def reply(text: str, model: Model = Depends(gpt4o)) -> str:
    return await model.respond(text)

if __name__ == "__main__":
    app.run()
```

Decorate a handler with the surfaces it answers on, return a `str`, and the
framework sends it as the Teams reply. The model is built **once** for the
process and injected via `Depends` — the model choice stays visible in your file
instead of being buried in the framework. (`Model()` with no argument falls back
to `AZURE_AI_MODEL_DEPLOYMENT_NAME`; `get_model` / `use_model("gpt-4o")` are
zero-config conveniences.)

### Composing protocols with routers

Like FastAPI's `include_router`, an `Agent` composes `Router`s so each protocol
can live in its own module:

```python
from castia import Agent
from handlers import activity, responses, invocations

app = Agent(name="my-agent")
app.include(activity.router, responses.router, invocations.router)
```

### Richer replies

Handlers can take a `Message` and reach for typed builders — Adaptive Cards,
suggested actions, citations, mentions, sensitivity labels, live-typing
streamers, and reactions:

```python
from castia import Depends, Message, Model, Reaction, Router, Teams, get_model

router = Router()

@router.activity(Teams.direct)
async def reply(text: str, msg: Message, model: Model = Depends(get_model)) -> None:
    await msg.react(Reaction.eyes)
    answer = await model.respond(text)
    await msg.say(answer)
```

### Consume a toolbox

A Foundry **toolbox** is a curated set of tools the platform exposes behind one
MCP-compatible endpoint, with centralized auth, governance, and versioning.
`castia` turns it into a single Responses-API `mcp` tool spec the model service
resolves **server-side** — no local impl, no function loop. Build the spec and
hand it to the model as an `extra_specs` entry:

```python
from castia import Depends, Model, Teams, get_model, toolbox_mcp_tool, toolbox_token

@app.activity(Teams.direct)
async def reply(text: str, model: Model = Depends(get_model)) -> str:
    tool = toolbox_mcp_tool(token=await toolbox_token())   # reads TOOLBOX_* env
    return await model.respond_with_tools(
        text, tools=[], activity=None, extra_specs=[tool] if tool else []
    )
```

`toolbox_mcp_tool()` (called with no endpoint) reads the environment via
`resolve_toolbox_endpoint`, whose precedence is:

1. an explicit full URL in `TOOLBOX_ENDPOINT` / `TOOLBOX_MCP_ENDPOINT`;
2. the platform-native `TOOLBOX_<NAME>_MCP_ENDPOINT` that the `azd ai toolbox`
   extension writes, keyed off `TOOLBOX_NAME` (see `platform_endpoint_env`);
3. composed from `FOUNDRY_PROJECT_ENDPOINT` + `TOOLBOX_NAME` (+ optional
   `TOOLBOX_VERSION`). The unversioned URL resolves the promoted **default**
   version, so a version bump needs no redeploy.

It returns `None` when no toolbox is configured, so "no toolbox" just attaches no
tool. Auth is either a bearer `token` (minted from the container's managed
identity by `toolbox_token()`) or a stored-connection `project_connection_id`.
A Foundry IQ knowledge base is the same shape via `knowledge_base_mcp_tool`.

For optimizer-ready toolbox guidance, pass an override map for the selected
tools and declare the same pure spec provider with `app.tools(...)` so
`python -m castia optimize` can emit the federated tools to `tools.json`:

```python
def web_tools():
    tool = toolbox_mcp_tool(
        allowed_tools=("web",),
        descriptions={
            "web": "Search current public web information."
        },
        param_guidance={
            "web": {
                "search_query": "A concise public web search query."
            }
        },
    )
    return [tool] if tool else []

app.tools(web_tools)
```

Use names from your server's `tools/list` result. For the verified web toolbox,
the tool is `web` and its parameter is `search_query`, not `query`. Castia emits
the selected tool name to `tools.json`; it accepts `toolbox___web` only as an
older override-key spelling. Unknown or ambiguous tool names raise immediately.
If two allowed tools share the same bare name after a `___` federated prefix,
use the fully qualified toolbox tool name in `descriptions` and
`param_guidance`, for example
`contracts-kb-mcp___knowledge_base_retrieve`.
Guidance is folded into `server_description` and an optimizer sidecar. It does
not replace the upstream tool schema or turn MCP into local function execution.
Private sidecars are stripped before the Responses API call.

`app.tools(...)` declares tools for baseline generation; it does not attach
them to a model request. The handler must pass the same specs, with current
authentication, to `respond_with_tools`. Keep token acquisition out of the
registered provider so baseline checks remain offline. The
[complete application](https://github.com/sethjuarez/castia/blob/main/packages/python/AGENTS.md#write-a-complete-responses-agent)
shows both paths.

**Deploying:** the `azd ai toolbox` extension writes `TOOLBOX_<NAME>_MCP_ENDPOINT`
into the **azd** environment, but does **not** auto-inject it into a hosted
container — declare that env passthrough on your container yourself (there is no
`azure.yaml`/manifest step in `castia` for it).

> **Gotcha:** do **not** copy `rai_config.rai_policy_name: Microsoft.Default`
> from the `azd ai toolbox create --help` example — it is invalid on the project
> and 500s at tool enumeration (`tools/list`). Omit the `policies` block.

> **Validation status.** *Validated live* (Foundry Responses path, App
> Insights-traced): the end-to-end pipe (env → compose URL → attach one `mcp`
> tool → `tools/list` + `tools/call`), a raw `https://ai.azure.com` bearer minted
> in-container (**no `project_connection_id` required**), both env forms, and
> unversioned→default-version resolution. *Doc-derived / not yet live:*
> `knowledge_base_mcp_tool` (Foundry IQ), connection-backed tools (Azure AI
> Search / remote-MCP / A2A), the Activity path with a toolbox, and
> approval-gated tools (`require_approval` other than `"never"`).

The newer [issue #13 consumer proof](https://github.com/sethjuarez/castia/blob/main/packages/python/AGENTS.md#what-has-been-verified-live)
verified direct and applied-candidate web guidance from a local process against
live Foundry services. Its 16/16 calls preserve native MCP execution and the
upstream schema. Those controlled fixtures were not a new optimizer run or a
new hosted deployment.

## Protocols

`castia` publishes handlers for the protocols in `PUBLISHABLE_PROTOCOLS`:

- **Activity** — Teams / Bot Framework message and invoke turns.
- **`responses`** — the OpenAI `responses` wire shape.
- **`invocations`** — Foundry invoke envelopes (tool execution, agent-to-agent).

## Observability & evaluation

`castia` configures Foundry/Agent 365 telemetry for you when the agent starts.
By default it emits GenAI spans (the `chat {model}` spans the Foundry Traces UI
keys off) but does **not** record the prompt/response **content** onto them.

Recording content is what makes an agent's traces *evaluable* — trace-based
evaluators read the input/output text from the GenAI spans, which is only present
when content recording is enabled. Turn it on deliberately via
`configure_observability`:

```python
from castia.observe.configuration import configure_observability

# Records prompt/response text onto GenAI spans so traces can be evaluated.
configure_observability(enable_content_recording=True)
```

Resolution order for each flag is **explicit argument > environment variable >
default**:

| Flag | Argument | Environment variable | Default |
| --- | --- | --- | --- |
| Content recording | `enable_content_recording` | `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED` | off |
| GenAI tracing | `enable_genai_tracing` | `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING` | on |

Passing nothing preserves the default behavior. Telemetry setup is best-effort:
a failure is logged, never raised, so it can't break startup or a turn.

> **Security caveat:** enabling content recording writes prompt and response
> **text** to Application Insights. Only enable it where storing that content is
> acceptable for your data-handling and privacy requirements.

### Building a scored eval suite

Once your traces are evaluable, `python -m castia eval` wraps the
`azd ai agent eval` extension to synthesize and run a **scored** eval suite — a
generated JSONL dataset plus an auto-generated, weighted **rubric** (a custom
multi-dimension evaluator):

```bash
# Offline gate — validate eval.yaml + rubric files, no Azure, free in CI:
python -m castia eval check

# Synthesize a rubric + dataset from the agent instruction (billable):
python -m castia eval generate --agent my-agent --max-samples 25

# Re-upload locally edited rubric/dataset files as a new version:
python -m castia eval update --evaluator-only

# Submit a scored run against the deployed agent (billable):
python -m castia eval run
```

`check` is a pure, offline referential-integrity gate: it resolves every
evaluator/dataset `local_uri` and validates each rubric dimensions file.
`generate` and `run` submit **billable** Foundry jobs, so both accept
`--dry-run` to print the resolved `azd` command line without submitting
anything. The YAML checks need `uv pip install "castia[deploy]"`. The live wrappers also
require a separately installed and authenticated azd with `azd ai agent eval`.

The rubric dimensions file is a **bare JSON list** where each entry is keyed by
`id` (a stable slug like `correct_outcome`), with an optional
`always_applicable: true` on the catch-all dimension. That cross-SDK shape is
pinned in the monorepo at
[`spec/conformance/rubric/`](https://github.com/sethjuarez/castia/tree/main/spec/conformance/rubric).

## Optimizer-readiness

The Foundry **Agent Optimizer** searches for a better system prompt (and, when
you declare tools, better tool descriptions) by running candidates against your
eval suite. Making a castia agent *optimizer-ready* is three things: install the
runtime resolver, ship a baseline config, and source your model + instructions
from that config instead of hardcoding them — so the optimizer can swap in a
candidate with **zero handler changes**.

Bind your model dependency with `configured_model()` and thread its resolved
instructions through:

```python
from castia import Depends, Model, Router, configured_model

router = Router()
gpt = configured_model()  # resolves baseline (or the injected candidate) once

@router.responses()
async def reply(text: str, model: Model = Depends(gpt)) -> str:
    return await model.respond(text)   # instructions flow into responses.create
```

`configured_model()` calls `load_agent_config()`, which is **best-effort**: if
the optimizer package isn't installed, resolution fails, or no config is found,
it degrades to environment defaults (`AZURE_AI_MODEL_DEPLOYMENT_NAME`, no
instructions). The agent can still start, but that fallback does not preserve
the candidate's behavior. Inspect `AgentConfig.source` before reporting that a
candidate loaded.

Ship a baseline under `.agent_configs/baseline/`:

```
.agent_configs/baseline/
  metadata.yaml       # model, instruction_file, (optional) tool_file pointers
  instructions.md     # the system prompt the optimizer tunes
  tools.json          # optional: tool specs the optimizer may reword
```

If your agent declares tools with `app.tools(...)`, keep the baseline
`tools.json` in sync with the code using the build-time reconciler:

```bash
python -m castia optimize          # write/refresh .agent_configs/baseline/tools.json
python -m castia optimize --check   # CI drift gate (exits non-zero, writes nothing)
```

Then submit optimizer jobs directly through castia. This path reads the same
`eval.yaml` and baseline files, inlines local JSONL datasets for the request,
and preserves `tools.json` so toolbox/federated tool descriptions are visible to
the optimizer:

```bash
python -m castia optimize run --dry-run   # FREE: print the exact payload
python -m castia optimize run             # billable: submit to Foundry
python -m castia optimize status --watch  # poll the latest castia-submitted job
python -m castia optimize apply           # write the best candidate locally
python -m castia optimize cancel          # cancel the latest job
```

`optimize run` uses `FOUNDRY_PROJECT_ENDPOINT` (or `--project-endpoint`) and
the model/evaluator/dataset declarations in `eval.yaml`. `optimize apply`
materializes the candidate under `.agent_configs/<candidate-id>/` with
`metadata.yaml`, `instructions.md`, `tools.json`, and `skills/` as returned by
Foundry. Deployment is still an `azd` handoff: set
`OPTIMIZATION_LOCAL_DIR=.agent_configs` and
`OPTIMIZATION_CANDIDATE_ID=<candidate-id>`, then deploy the hosted agent with
your existing `azd` workflow.

For local functional checks, use `python -m castia observe drift` with an
explicit project configuration and your local Azure credentials. See
[the lifecycle guide](https://github.com/sethjuarez/castia/blob/main/packages/python/LIFECYCLE.md)
for coverage, limits, and report handling.
The CLI does not install a schedule or submit fine-tuning jobs.

The optional `.github/workflows/foundry-optimizer-live.yml` workflow runs on
manual dispatch only. It submits one billable optimizer candidate against a pre-deployed
Foundry smoke agent, waits for completion, and applies the best candidate into a
throwaway runner directory. To use this optional workflow, configure the
`foundry-live` GitHub environment with
OIDC Azure login secrets (`AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`) and these variables:

| Variable | Purpose |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Target Foundry project endpoint for local/process checks; do not put this under hosted `azure.yaml` env. |
| `FOUNDRY_OPTIMIZER_AGENT_NAME` | Pre-deployed smoke agent name. |
| `FOUNDRY_OPTIMIZER_AGENT_VERSION` | Optional pinned hosted-agent version. |
| `FOUNDRY_EVAL_MODEL` | Optional evaluator model, defaults to `gpt-4o`. |
| `FOUNDRY_OPTIMIZE_MODEL` | Optional optimizer model, defaults to `gpt-5`. |

**Config resolution order** is first-wins: `OPTIMIZATION_CONFIG` (inline JSON) →
resolver API (`OPTIMIZATION_CANDIDATE_ID` + `OPTIMIZATION_RESOLVE_ENDPOINT`) →
local `.agent_configs/` → environment defaults. An explicit `config_dir`
argument (or `OPTIMIZATION_LOCAL_DIR`) affects **only** the local source — pass
it anchored to your app root so the baseline resolves the same under
`python app.py` and `python -m castia`. That contract is pinned for every SDK in
[`spec/conformance/optimization/`](https://github.com/sethjuarez/castia/tree/main/spec/conformance/optimization).

### Switching to a fine-tuned model

The optimizer's model search can land on a **reasoning** model — an o-series or
GPT-5 deployment, or one you mint yourself with fine-tuning. Reasoning models
take a `reasoning.effort` control that plain chat models don't. `Model` exposes
it as `reasoning_effort` (`minimal|low|medium|high`):

```python
from castia import use_model

o4 = use_model("o4-mini-rft-2025", reasoning_effort="high")
```

An unset effort omits the field entirely, so chat models are called exactly as
before; a bad level raises at construction rather than as a `400` mid-turn. An
operator can also switch a **deployed** agent onto a reasoning model with zero
code by setting `MODEL_REASONING_EFFORT` — an explicit argument still wins.
Because `configured_model()` builds its `Model` through the same path, that env
override flows through to the resolved candidate automatically.

> **Responses-only constraint:** the optimizer accepts only single-protocol
> `responses` agents — submitting a multi-protocol agent (one that also speaks
> activity/invocations) is rejected with a `400` at submission. Project a
> responses-only sibling from the *same* handler code with
> `app.responses_only()`, deploy that as its own service, optimize it, then apply
> the winning `.agent_configs` candidate back to your live agent.

The runtime resolver and reconciler need the optimizer extra,
`uv pip install --prerelease=allow "castia[optimize]"`. Fine-tuning remains a
separate build-time operation: train, deploy the resulting model, evaluate the
deployment, then include that deployment in optimizer model search if desired.

## Fine-tuning: SFT, DPO, and RFT

Fine-tuning is an optional, separately authorized training operation. A
resulting model still needs a separate deployment before an application or
optimizer search can use it. Castia supplies builders, offline validators, and
an explicit billable submit command. Passing an offline check does not authorize
training or checkpoint deployment.

```bash
# Offline gates — validate datasets, no Azure, free in CI:
python -m castia finetune check --type sft --dataset train.jsonl --validation val.jsonl
python -m castia finetune check --type dpo --dataset train.jsonl
python -m castia finetune check --type rft --dataset train.jsonl --validation val.jsonl --grader grader.json

# Bridge an eval rubric into a score_model grader (offline):
python -m castia finetune grader --rubric rubric.json --model gpt-4o --out grader.json

# Preview payloads without uploads or submission:
# Removing --dry-run starts billable training and needs separate approval.
python -m castia finetune submit --type sft --model gpt-4.1-mini \
  --dataset train.jsonl --validation val.jsonl --n-epochs 2 --dry-run
python -m castia finetune submit --type dpo --model gpt-4.1 \
  --dataset train.jsonl --beta 0.1 --dry-run
python -m castia finetune submit --model o4-mini --dataset train.jsonl \
  --validation val.jsonl --grader grader.json --reasoning-effort high --dry-run
```

SFT datasets are JSONL chat `messages[]` rows with at least one `user` and one
`assistant` turn, and the **final message role must be `assistant`**. SFT accepts
optional `n_epochs`, `batch_size`, and `learning_rate_multiplier`
hyperparameters.

DPO datasets are JSONL preference rows with `input.messages`,
`preferred_output`, and `non_preferred_output`. The output arrays may contain
`assistant` or `tool` messages and must include at least one `assistant`
message. DPO accepts the SFT hyperparameters plus `beta` and `l2_multiplier`.

RFT trains a reasoning model against a grader. Its datasets are JSONL chat
`messages[]` rows whose **final message role must be `user`**, with extra
top-level keys as the `item.*` ground truth; **both** train and validation
splits are required. A grader is one of `string_check`, `text_similarity`,
`score_model`, `python`, `multi`, or `endpoint` (preview); templates reference
two namespaces only — `{{ sample.output_text }}` and `{{ item.<field> }}`.
`rubric_to_score_model()` bridges an eval rubric straight into a `score_model`
grader — the natural tie between the *evaluate* and *switch-models* steps.

> ⚠️ **Provisional / doc-derived.** The grader JSON schema, the RFT
> hyperparameter names, and the SFT/DPO/RFT `fine_tuning.jobs.create` payload
> shapes are doc-derived and **have not been confirmed against live Castia
> training jobs**. The builders and validators are fully offline-tested; treat
> submitted wire shapes as provisional until real submissions validate them. The
> RFT language-neutral contract is pinned in `spec/conformance/graders/`.

## Design

`castia` is deliberately import-cheap: `import castia` never pulls in the
instrumented Azure/OpenAI/httpx stacks, so telemetry can be configured before
those libraries load. The heavy imports are deferred into the methods that need
them.

## License

MIT © 2026 Seth Juarez

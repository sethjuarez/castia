---
name: castia-lifecycle
description: "Build, evaluate, optimize, and RFT-fine-tune a Microsoft Foundry agent with the castia OSS Python SDK (PyPI: castia). Use whenever a task involves the castia package or the agent lifecycle it covers: writing a FastAPI-style Foundry agent, generating/validating an eval rubric, running the Foundry Agent Optimizer, or preparing a reinforcement fine-tuning (RFT) job to switch to a reasoning model. Triggers include: 'use castia', 'pip install castia', 'build a Foundry agent', 'castia eval', 'generate a rubric', 'castia optimize', 'agent optimizer', 'castia finetune', 'RFT', 'reinforcement fine-tuning', 'reasoning_effort', 'switch models', 'score_model grader'. Covers the four lifecycle steps, the free-vs-billable command split, and the provisional status of the RFT wire shape. This is a package-usage playbook, not a Foundry tutorial."
---

# castia-lifecycle — build → evaluate → optimize → switch models

`castia` (PyPI, MIT) is an idiomatic, FastAPI-style Python SDK for **Microsoft
Foundry agents**. It carries one through-line — the **agent lifecycle** — end to
end, so an agent session can take a Foundry agent from first reply to a
fine-tuned reasoning model without leaving the package:

```
build a simple agent → evaluate it → optimize it → switch models via RFT
```

Install: `pip install castia` (or `uv pip install castia`). Import as `castia`;
the build-time CLI is `python -m castia`.

## The one mental model: two audiences, one config

Every step past "build" reads the **same** `eval.yaml` superset and the
`.agent_configs/` baseline. Evaluate scores against it; optimize improves against
it; RFT mints a new model you point the agent back at. Nothing is bespoke per
step — you grow one artifact set.

CLI verbs split cleanly into **free/offline gates** and **billable Foundry jobs**:

| Verb | Free (offline, no Azure, no spend) | Billable (submits a Foundry job) |
|------|-----------------------------------|----------------------------------|
| `eval` | `eval check` | `eval generate`, `eval run` (`--dry-run` to preview) |
| `optimize` | `optimize --check`, `optimize run --dry-run` | `optimize run` |
| `finetune` | `finetune check`, `finetune grader` | `finetune submit` (`--dry-run` to preview) |

Rule of thumb: run the `check`/`--check`/`--dry-run` gate first — it's free and
catches drift before you spend.

## Step 1 — build a simple agent

`castia` wraps the Foundry model + Responses API so a handler never touches the
client. The runtime primitives are `Agent`/`Router` (composition), `Model` +
`Depends(get_model)` / `use_model(...)` (the model dependency), and the reply
surfaces (`Teams`, `Message`, streaming, cards). Minimal shape:

```python
from castia import Agent, Depends, Model, Teams, get_model

app = Agent(name="my-agent")

@app.activity(Teams.direct)
async def reply(text: str, model: Model = Depends(get_model)) -> str:
    return await model.respond(text)
```

`get_model` reads `AZURE_AI_MODEL_DEPLOYMENT_NAME` + `FOUNDRY_PROJECT_ENDPOINT`.
See the castia README for the full handler/tool/decoration API — this skill owns
the *lifecycle*, the README owns the *runtime surface*.

## Step 2 — evaluate it (+ generate a rubric)

`eval.yaml` lists evaluators + datasets. A **rubric** is a generated custom
evaluator: a bare list of `{id, description, weight}` dimensions (keyed by `id`,
not `name`; an optional `always_applicable: true` catch-all). Commands:

```
python -m castia eval check                 # FREE offline gate: eval.yaml + rubric files well-formed
python -m castia eval generate ...          # billable: synthesize a rubric + dataset (--dry-run previews)
python -m castia eval update ...            # re-upload locally edited rubric/dataset files
python -m castia eval run ...               # billable: score the agent against the suite
```

Start with `eval check` in CI — it needs no Azure and proves referential
integrity before any spend.

## Step 3 — optimize it (Foundry Agent Optimizer)

The optimizer improves the **prompt / tool descriptions / model selection**
against the same `eval.yaml`. The on-disk contract is `.agent_configs/baseline/`
(instructions, tools.json, metadata). Castia owns request construction,
submission, status, cancel, and local candidate apply; `azd` remains the hosted
agent deployment rail. Two facts matter:

- **Responses-only.** The optimizer submits against the Responses protocol; an
  agent it optimizes must expose it (`Agent.responses_only()`).
- **Consume the result as config, zero code.** `configured_model()` /
  `load_agent_config()` resolve the optimizer-selected model + instructions from
  the baseline — so applying an optimization is a config change, not an edit.

```
python -m castia optimize --check           # FREE: report .agent_configs baseline drift, write nothing
python -m castia optimize                   # FREE: write/refresh baseline tools.json + metadata pointers
python -m castia optimize run --dry-run     # FREE: print the exact Foundry optimizer payload
python -m castia optimize run               # billable: submit + wait for a Foundry optimizer job
python -m castia optimize status --watch    # inspect/poll the latest castia-submitted job
python -m castia optimize apply             # fetch best candidate and write .agent_configs/<candidate>
python -m castia optimize cancel            # cancel the latest castia-submitted job
```

`optimize run` uses `FOUNDRY_PROJECT_ENDPOINT` (or `--project-endpoint`) and
the model/evaluator/dataset declarations in `eval.yaml`. The live wire shape is
validated against the preview service through the package tests and the daily
GitHub Actions smoke: `api-version=v1`, `Foundry-Features:
AgentsOptimization=V2Preview`, and a `{"inputs": ...}` submit envelope.

For toolbox/federated MCP tools, use the post-facto guidance overrides on
`toolbox_mcp_tool(...)` / `knowledge_base_mcp_tool(...)`: selected tool names,
descriptions, parameter guidance, and `server_description` are emitted into
optimizer-visible `tools.json`, while private `x-castia-*` sidecar metadata is
stripped before Responses calls. Live service-visible toolbox names are the MCP
tool names (for example `web`), not `server_label___tool`; the legacy spelling
is accepted only as an alias when applying optimized tools.

After `optimize apply`, deploy a chosen candidate through `azd` by setting:

```
OPTIMIZATION_LOCAL_DIR=.agent_configs
OPTIMIZATION_CANDIDATE_ID=<candidate-id>
```

Then run the normal hosted-agent deploy workflow.

## Step 4 — switch models via RFT (the last step)

**Reinforcement fine-tuning** trains a *reasoning* model against a **grader** (a
reward function) to produce a stronger model deployment. It **composes** with the
optimizer: RFT mints a new reasoning-model deployment; add it to the optimizer's
`model_search_space` (needs ≥2 deployed models) and re-run optimize to select it.
So *"switch models via RFT"* = train → deploy checkpoint → point
`configured_model` at it → re-optimize.

### 4a. The one runtime enabler — `reasoning_effort` (zero-code)

RFT targets reasoning models (o4-mini, gpt-5), which take a reasoning-effort
control. `castia.Model` forwards it, so consuming an RFT'd model is config only:

```python
from castia import use_model
o4 = use_model("o4-mini-rft-2025", reasoning_effort="high")   # minimal|low|medium|high
# or zero-code on the default model dependency:
#   MODEL_REASONING_EFFORT=high   (alongside AZURE_AI_MODEL_DEPLOYMENT_NAME)
```

A plain chat model is called unchanged (the field is omitted); a bad level fails
fast at construction, not as a 400 mid-turn.

### 4b. Prepare / submit the RFT job — `castia finetune`

```
# FREE offline gates:
python -m castia finetune grader --rubric rubric.yaml --model gpt-4o --out grader.json
python -m castia finetune check  --dataset train.jsonl --validation val.jsonl --grader grader.json

# billable (preview with --dry-run, which uploads/submits nothing):
python -m castia finetune submit --model o4-mini --dataset train.jsonl \
       --validation val.jsonl --grader grader.json --reasoning-effort high --dry-run
```

- `finetune grader` bridges an eval **rubric → a `score_model` grader** (each
  weighted dimension becomes a line in the judge prompt) — the natural tie-in
  between the *evaluate* and *switch-models* steps.
- `finetune check` validates the dataset offline: JSONL `messages`, **final
  message role must be `user`**, **both** train+validation splits required, and
  every `{{ item.<field> }}` the grader reads is present on each row.
- Grader types: `string_check`, `text_similarity`, `score_model`, `python`,
  `multi`, `endpoint` (preview). Templates use `{{ sample.output_text }}` /
  `{{ item.* }}`. RFT hyperparameters: `eval_interval`, `eval_samples`,
  `compute_multiplier`, `reasoning_effort`.
- Submission is out-of-band (OpenAI `fine_tuning.jobs.create`), **billable**
  (auto-pauses at the $5,000 training+grading cap), and needs a **Foundry Owner**
  to deploy the resulting checkpoint.

> **PROVISIONAL — RFT wire shape.** The grader JSON schema, the RFT hyperparameter
> names, and that `fine_tuning.jobs.create` accepts the built payload are
> **doc-derived** (Foundry RFT how-to) and **not yet confirmed against a live RFT
> job**. The *offline* surface (builders + validators + CLI gates) is tested; the
> wire acceptance is not. `castia.finetune`'s docstrings and
> `spec/conformance/graders/` carry `status: "provisional"`. Treat
> `build_rft_job`/`submit_rft_job` output as provisional until a real submission
> validates it, then flip the fixture to `"validated"` and drop the caveats.

## Conformance / spec

`spec/conformance/` is the language-neutral source of truth pinned by the SDK's
own tests (rubric shape, optimization candidate precedence, grader schema). If you
extend a shape, update the fixture — the spec tests will fail otherwise. That's
the guard that keeps the SDK and the documented contract from drifting.

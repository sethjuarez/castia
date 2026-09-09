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
pip install castia
# or, with uv:
uv add castia
```

Requires Python 3.11+.

## Quickstart

```python
from castia import Agent, Depends, Model, Teams

app = Agent(name="my-agent")

def gpt4o() -> Model:
    return Model("gpt-4o")

@app.message(Teams.direct, Teams.group, Teams.channel_mention)
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
from castia import Depends, Message, Model, Reaction, Router, Teams

router = Router()

@router.activity(Teams.direct)
async def reply(text: str, msg: Message, model: Model = Depends(get_model)) -> None:
    await msg.react(Reaction.eyes)
    answer = await model.respond(text)
    await msg.say(answer)
```

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
from castia.observability import configure_observability

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
anything. The `azd` wrappers need the build-time extra: `pip install
'castia[deploy]'`.

The rubric dimensions file is a **bare JSON list** where each entry is keyed by
`id` (a stable slug like `correct_outcome`), with an optional
`always_applicable: true` on the catch-all dimension. That cross-SDK shape is
pinned in the monorepo at [`spec/conformance/rubric/`](../../spec/conformance/rubric).

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
instructions) — the agent runs identically with or without the optimizer.

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

**Config resolution order** is first-wins: `OPTIMIZATION_CONFIG` (inline JSON) →
resolver API (`OPTIMIZATION_CANDIDATE_ID` + `OPTIMIZATION_RESOLVE_ENDPOINT`) →
local `.agent_configs/` → environment defaults. An explicit `config_dir`
argument (or `OPTIMIZATION_LOCAL_DIR`) affects **only** the local source — pass
it anchored to your app root so the baseline resolves the same under
`python app.py` and `python -m castia`. That contract is pinned for every SDK in
[`spec/conformance/optimization/`](../../spec/conformance/optimization).

> **Responses-only constraint:** the optimizer accepts only single-protocol
> `responses` agents — submitting a multi-protocol agent (one that also speaks
> activity/invocations) is rejected with a `400` at submission. Project a
> responses-only sibling from the *same* handler code with
> `app.responses_only()`, deploy that as its own service, optimize it, then apply
> the winning `.agent_configs` candidate back to your live agent.

The runtime resolver and the reconciler need the optimizer extra: `pip install
'castia[optimize]'`.

## Design

`castia` is deliberately import-cheap: `import castia` never pulls in the
instrumented Azure/OpenAI/httpx stacks, so telemetry can be configured before
those libraries load. The heavy imports are deferred into the methods that need
them.

## License

MIT © 2026 Seth Juarez

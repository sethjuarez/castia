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

## Design

`castia` is deliberately import-cheap: `import castia` never pulls in the
instrumented Azure/OpenAI/httpx stacks, so telemetry can be configured before
those libraries load. The heavy imports are deferred into the methods that need
them.

## License

MIT © 2026 Seth Juarez

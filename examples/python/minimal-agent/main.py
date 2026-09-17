"""Responses, Invocations and Teams share one model-backed handler."""

from pathlib import Path

from castia import Agent, Depends, Teams

app = Agent(name="minimal-agent")


def model_provider():
    # Resolve candidates only on first use, never during module registration.
    from castia import configured_model, load_agent_config

    config = load_agent_config(Path(__file__).parent / ".agent_configs")
    return configured_model(config)()


ModelDependency = Depends(model_provider)


@app.activity(Teams.direct)
@app.responses()
@app.invocations()
async def reply(text: str, model=ModelDependency) -> str:
    return await model.respond(text)


if hasattr(app, "responses_stream"):

    @app.responses_stream()
    async def reply_stream(text: str, model=ModelDependency):
        async for delta in model.stream(text):
            yield delta


if __name__ == "__main__":
    app.run()

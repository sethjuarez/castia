"""Protocol playground agent for exercising Responses, Activity, and Invocations."""

import os
import re
from pathlib import Path

from castia import Agent, Depends, Message, Teams

app = Agent(name="protocol-playground-agent")
AGENT_ROOT = Path(__file__).parent
PROJECT_ENDPOINT = re.compile(r"^https://[^/\s]+/api/projects/[^/\s]+$")


def load_local_env() -> None:
    env_path = AGENT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return
    load_dotenv(env_path, override=False)


def validate_startup() -> None:
    load_local_env()
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    if not endpoint or endpoint.startswith("<"):
        raise RuntimeError("Fill .env from .env.example before starting the protocol playground agent.")
    if not PROJECT_ENDPOINT.match(endpoint):
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT must look like https://<account>.services.ai.azure.com/api/projects/<project>.")
    os.environ["FOUNDRY_PROJECT_ENDPOINT"] = endpoint


app.startup_check(validate_startup)


class ProtocolScenario:
    """Small deterministic service so protocol behavior is testable offline."""

    async def answer(self, text: str) -> str:
        return f"Protocol playground received: {text}"


def scenario_provider() -> ProtocolScenario:
    return ProtocolScenario()


ScenarioDependency = Depends(scenario_provider)


@app.responses()
async def responses_reply(text: str, scenario: ProtocolScenario = ScenarioDependency) -> str:
    return await scenario.answer(text)


@app.invocations()
async def invocation_reply(text: str, scenario: ProtocolScenario = ScenarioDependency) -> str:
    return await scenario.answer(f"invocation {text}")


@app.activity(Teams.direct)
async def activity_reply(msg: Message, scenario: ProtocolScenario = ScenarioDependency) -> str | None:
    await msg.react("eyes")
    await msg.typing()
    first = await msg.say(await scenario.answer(msg.text), ai_generated=True)
    if first:
        await msg.update(first, f"Updated after review: {msg.text}")
    disposable = await msg.say("This temporary status will be deleted.")
    if disposable:
        await msg.delete(disposable)
    return "Final Activity Protocol reply."


if __name__ == "__main__":
    app.run()

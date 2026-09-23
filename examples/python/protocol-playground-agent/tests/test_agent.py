import asyncio
import sys
from pathlib import Path

from castia.building import AgentTestHarness

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app, scenario_provider


class OfflineScenario:
    async def answer(self, text: str) -> str:
        return f"Offline: {text}"


def activity(text="hello"):
    return {
        "type": "message",
        "id": "turn-1",
        "channelId": "msteams",
        "serviceUrl": "https://connector.invalid",
        "text": text,
        "conversation": {"id": "chat-1", "conversationType": "personal"},
        "from": {"id": "user"},
        "recipient": {"id": "bot"},
    }


def test_protocols(monkeypatch):
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/demo",
    )

    async def check():
        assert app.registered_protocols() == ["activity", "responses", "invocations"]
        async with AgentTestHarness(
            app,
            dependency_overrides={scenario_provider: OfflineScenario},
        ) as test:
            ready = await test.client.get("/readiness")
            assert ready.status_code == 200

            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "Offline: hello"

            response = await test.client.post("/invocations", json={"message": "hello"})
            assert response.json()["output"] == "Offline: invocation hello"

            response = await test.client.post("/activity/messages", json=activity())
            assert response.status_code == 200
            assert [event.method for event in test.egress] == [
                "PUT",
                "POST",
                "POST",
                "PUT",
                "POST",
                "DELETE",
                "DELETE",
                "POST",
            ]
            assert test.egress[0].url.endswith("/activities/turn-1/reactions/eyes")
            assert test.egress[2].body["text"] == "Offline: hello"
            assert test.egress[3].body["text"] == "Updated after review: hello"
            assert test.egress[-1].body["text"] == "Final Activity Protocol reply."

    asyncio.run(check())

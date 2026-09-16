"""Offline protocol contract; no model, identity, or Azure calls."""

import asyncio

from castia.building import AgentTestHarness
from main import app, model_provider


class EchoModel:
    async def respond(self, text):
        return f"Echo: {text}"

    async def stream(self, text):
        yield "Echo: "
        yield text


def test_protocols():
    async def check():
        async with AgentTestHarness(
            app, dependency_overrides={model_provider: EchoModel}
        ) as test:
            ready = await test.client.get("/readiness")
            assert ready.status_code == 200
            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "Echo: hello"
            response = await test.client.post("/responses", json={
                "input": "hello", "stream": True,
            })
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            assert "event: response.output_text.delta" in response.text
            assert '"delta":"Echo: "' in response.text
            assert '"delta":"hello"' in response.text
            assert "event: response.completed" in response.text
            response = await test.client.post("/invocations", json={"message": "hello"})
            assert response.json()["output"] == "Echo: hello"
            response = await test.client.post("/activity/messages", json={
                "type": "message", "id": "turn-1", "channelId": "msteams",
                "serviceUrl": "https://connector.invalid", "text": "hello",
                "conversation": {"id": "chat-1", "conversationType": "personal"},
                "from": {"id": "user"}, "recipient": {"id": "bot"},
            })
            assert response.status_code == 200
            assert test.egress[-1].body["text"] == "Echo: hello"
    asyncio.run(check())

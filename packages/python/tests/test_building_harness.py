"""The harness runs real routes and restores all process-wide test seams."""

import asyncio
import socket
import subprocess

import httpx
import pytest

from castia import Agent, Depends, Message, Teams
from castia.building import AgentTestHarness
from castia.building._offline import OfflineOperationError


def activity(**changes):
    return {
        "type": "message",
        "id": "turn",
        "channelId": "msteams",
        "text": "hi",
        "serviceUrl": "https://connector.invalid",
        "conversation": {"id": "chat", "conversationType": "personal"},
        "from": {"id": "user"},
        "recipient": {"id": "bot"},
        **changes,
    }


def test_real_wire_endpoints_and_strict_isolated_overrides():
    from castia.runtime import dependencies

    def production():
        pytest.fail("Real dependency must not run")

    async def fake():
        return "fake"

    app = Agent()

    @app.responses()
    @app.invocations()
    @app.chat()
    async def reply(text: str, value=Depends(production)):
        return f"{value}:{text}"

    previous = dict(dependencies._CACHE)
    dependencies._CACHE[production] = "production cached"

    async def run():
        harness = AgentTestHarness(app, dependency_overrides={production: fake})
        with pytest.raises(RuntimeError, match="async with"):
            _ = harness.client
        async with harness as test:
            assert (await test.client.get("/readiness")).text == "Agent running!"
            r = await test.client.post("/responses", json={"input": [
                {"role": "user", "content": [{"type": "input_text", "text": "real"}]}
            ]})
            assert r.json()["output"][0]["content"][0]["text"] == "fake:real"
            r = await test.client.post("/chat/completions", json={
                "messages": [{"role": "user", "content": "chat"}]
            })
            assert r.json()["choices"][0]["message"]["content"] == "fake:chat"
            r = await test.client.post("/invocations", content="plain")
            assert r.json() == {"output": "fake:plain"}
            assert not test.egress
            client = test.client
        assert client.is_closed
        with pytest.raises(RuntimeError, match="async with"):
            _ = harness.client
        async with AgentTestHarness(app) as test:
            with pytest.raises(RuntimeError, match="Missing dependency override"):
                await test.client.post("/responses", json={"input": "hello"})

    try:
        asyncio.run(run())
        assert dependencies._CACHE[production] == "production cached"
    finally:
        dependencies._CACHE.clear()
        dependencies._CACHE.update(previous)


def test_connector_capture_all_verbs_and_streaming_without_auth(monkeypatch):
    from castia.messaging import connector

    async def forbidden(*args, **kwargs):
        pytest.fail("Connector authentication must not run")

    monkeypatch.setattr(connector, "bot_connector_token", forbidden)
    app = Agent()

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        await msg.typing()
        assert await msg.react()
        assert await msg.unreact()
        sent = await msg.say("card", ai_generated=True)
        assert sent == "captured-4"
        assert await msg.update(sent, "edited")
        assert await msg.delete(sent)
        stream = msg.stream(min_interval=0)
        await stream.append("delta")
        await stream.finish()
        return "final"

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post("/activity/messages", json=activity())
            assert response.status_code == 200
            events = test.egress
            assert [e.method for e in events[:6]] == [
                "POST", "PUT", "DELETE", "POST", "PUT", "DELETE"
            ]
            assert events[1].url.endswith("/activities/turn/reactions/like")
            assert events[3].body["replyToId"] == "turn"
            assert events[3].body["from"]["id"] == "bot"
            assert events[3].body["entities"]
            assert events[-1].body["text"] == "final"
            assert all("headers" not in e.to_dict() for e in events)

    asyncio.run(run())


def test_invoke_and_unhandled_and_malformed_activity_use_production_behavior():
    app = Agent()

    @app.invoke("custom")
    async def invoke(value):
        return {"received": value}

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post("/activity/messages", json=activity(
                type="invoke", name="custom", value={"action": "test"}
            ))
            assert response.json() == {"received": {"action": "test"}}
            response = await test.client.post("/activity/messages", json=activity(
                type="invoke", name="unknown"
            ))
            assert response.json() == {}
            assert (await test.client.post("/activity/messages", content="{")).status_code == 200
            assert not test.egress
            assert (await test.client.post("/responses", json={})).status_code == 404

    asyncio.run(run())


def test_failing_connector_is_captured_not_reported_as_success():
    app = Agent()

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        assert not await msg.react()
        assert await msg.say("failed") is None

    async def run():
        async with AgentTestHarness(app, connector_status=403) as test:
            await test.client.post("/activity/messages", json=activity())
            assert len(test.egress) == 2
            assert all(e.status_code == 403 and e.activity_id is None for e in test.egress)

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
def test_restoration_and_generator_cleanup_even_on_failure(failure):
    from opentelemetry import trace

    from castia.messaging import connector
    from castia.observe import configuration as observability
    from castia.runtime import dispatch

    original = (
        connector._send, connector.authorization, dispatch.resolve,
        dispatch.flush_telemetry, observability.configure_observability,
        socket.socket.connect, trace.get_tracer,
    )
    cleaned = []

    def dependency():
        raise AssertionError("production")

    async def fake():
        try:
            yield "test"
        finally:
            await asyncio.sleep(0)
            cleaned.append(True)

    app = Agent()

    @app.responses()
    async def reply(text: str, value=Depends(dependency)):
        raise failure("test")

    async def run():
        with pytest.raises(failure):
            async with AgentTestHarness(app, dependency_overrides={dependency: fake}) as test:
                client = test.client
                await test.client.post("/responses", json={"input": "hello"})
        assert client.is_closed
        async with AgentTestHarness(Agent()):
            pass

    asyncio.run(run())
    assert cleaned == [True]
    assert original == (
        connector._send, connector.authorization, dispatch.resolve,
        dispatch.flush_telemetry, observability.configure_observability,
        socket.socket.connect, trace.get_tracer,
    )


def test_entry_failure_cleanup_and_overlap_rejected():
    app = Agent()

    @app.responses()
    async def incompatible(msg: Message):
        return ""

    async def run():
        with pytest.raises(TypeError, match="Activity Protocol"):
            async with AgentTestHarness(app):
                pass
        harness = AgentTestHarness(Agent())
        async with harness:
            with pytest.raises(RuntimeError, match="already active"):
                async with AgentTestHarness(Agent()):
                    pass
            with pytest.raises(RuntimeError, match="already active"):
                async with harness:
                    pass
        async with harness:
            assert (await harness.client.get("/readiness")).status_code == 200

    asyncio.run(run())


def test_network_and_subprocess_disabled_and_telemetry_not_flushed(monkeypatch):
    from castia.runtime import dispatch

    def forbidden():
        pytest.fail("telemetry flush attempted")

    monkeypatch.setattr(dispatch, "flush_telemetry", forbidden)
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            assert (await test.client.post("/responses", json={"input": "x"})).status_code == 200
            with pytest.raises(OfflineOperationError):
                socket.create_connection(("example.com", 443))
            with pytest.raises(OfflineOperationError):
                socket.getaddrinfo("example.com", 443)
            with pytest.raises(OfflineOperationError):
                subprocess.run(["this-must-not-start"], check=False)  # noqa: ASYNC221 - blocked before execution
            async with httpx.AsyncClient() as client:
                with pytest.raises(OfflineOperationError):
                    await client.get("https://example.com")
        assert dispatch.flush_telemetry is forbidden

    asyncio.run(run())


def test_sync_fixture_and_async_override_teardown_errors_restore_patches():
    from castia.runtime import dispatch

    original = dispatch.resolve
    events = []

    def real():
        pytest.fail("real")

    def fake():
        events.append("open")
        try:
            yield "ok"
        finally:
            events.append("close")
            raise ValueError("cleanup")

    app = Agent()

    @app.responses()
    async def handler(text: str, value=Depends(real)):
        return value

    async def run():
        with pytest.raises(ValueError, match="cleanup"):
            async with AgentTestHarness(app, dependency_overrides={real: fake}) as test:
                client = test.client
                for _ in range(2):
                    assert (await client.post("/responses", json={})).json()["output_text"] == "ok"
        assert client.is_closed

    asyncio.run(run())
    assert events == ["open", "close"]
    assert dispatch.resolve is original

"""The harness runs real routes and restores all process-wide test seams."""

import asyncio
import json
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


def sse_events(text: str):
    events = []
    event_type = None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("event: "):
            event_type = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif not line and data_lines:
            data = "\n".join(data_lines)
            events.append((event_type, data if data == "[DONE]" else json.loads(data)))
            event_type = None
            data_lines = []
    return events


def assert_responses_sse_contract(response, *, deltas: list[str], output_text: str):
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = sse_events(response.text)
    assert [event for event, _ in events] == [
        "response.created",
        "response.in_progress",
        "response.output_item.added",
        "response.content_part.added",
        *["response.output_text.delta" for _ in deltas],
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
        None,
    ]

    payloads = [payload for _, payload in events]
    response_id = payloads[0]["response"]["id"]
    output_item_id = payloads[2]["item"]["id"]
    assert payloads[1]["response"]["id"] == response_id
    assert payloads[3]["item_id"] == output_item_id

    delta_payloads = payloads[4: 4 + len(deltas)]
    assert [payload["delta"] for payload in delta_payloads] == deltas
    assert all(payload["type"] == "response.output_text.delta" for payload in delta_payloads)

    done_offset = 4 + len(deltas)
    assert payloads[done_offset]["type"] == "response.output_text.done"
    assert payloads[done_offset]["item_id"] == output_item_id
    assert payloads[done_offset]["text"] == output_text
    assert payloads[done_offset + 1]["type"] == "response.content_part.done"
    assert payloads[done_offset + 1]["item_id"] == output_item_id
    assert payloads[done_offset + 1]["part"]["text"] == output_text
    assert payloads[done_offset + 2]["type"] == "response.output_item.done"
    assert payloads[done_offset + 2]["item"]["id"] == output_item_id
    assert payloads[done_offset + 2]["item"]["status"] == "completed"
    assert payloads[done_offset + 2]["item"]["content"][0]["text"] == output_text
    assert payloads[done_offset + 3]["type"] == "response.completed"
    assert payloads[done_offset + 3]["response"]["id"] == response_id
    assert payloads[done_offset + 3]["response"]["status"] == "completed"
    assert payloads[done_offset + 3]["response"]["output_text"] == output_text
    assert payloads[done_offset + 3]["response"]["output"][0]["id"] == output_item_id
    assert payloads[done_offset + 4] == "[DONE]"


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
            readiness = (await test.client.get("/readiness")).json()
            assert readiness == {"status": "ok"}
            readiness = (await test.client.get(
                "/readiness",
                headers={"host": "127.0.0.1"},
            )).json()
            assert readiness["status"] == "ok"
            assert readiness["protocols"] == ["responses", "invocations", "chat"]
            assert readiness["routes"]["responses"] == "/responses"
            assert readiness["agent"]["name"] is None
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


def test_agent_run_uses_env_port_and_startup_checks(monkeypatch):
    calls = []
    captured = {}
    app = Agent(name="env-agent")
    app.startup_check(lambda: calls.append("startup"))

    monkeypatch.setenv("PORT", "9012")
    monkeypatch.setattr(
        "castia.observe.configuration.configure_observability",
        lambda: calls.append("observability"),
    )

    def fake_serve(routes, wire, invokes, *, host, port, agent_name, required_env):
        captured.update(
            host=host,
            port=port,
            agent_name=agent_name,
            required_env=required_env,
        )

    monkeypatch.setattr("castia.hosting.server.serve", fake_serve)
    app.run()

    assert calls == ["observability", "startup"]
    assert captured == {
        "host": "0.0.0.0",
        "port": 9012,
        "agent_name": "env-agent",
        "required_env": (),
    }


def test_agent_run_explicit_port_wins_and_invalid_port_fails(monkeypatch):
    captured = {}
    app = Agent()

    monkeypatch.setenv("PORT", "not-a-port")
    with pytest.raises(ValueError, match="PORT must be an integer"):
        app.run()
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValueError, match="PORT must be between"):
        app.run()

    monkeypatch.setattr(
        "castia.observe.configuration.configure_observability",
        lambda: None,
    )

    def fake_explicit_serve(routes, wire, invokes, *, host, port, agent_name, required_env):
        captured.update(host=host, port=port)

    monkeypatch.setattr(
        "castia.hosting.server.serve",
        fake_explicit_serve,
    )
    app.run(host="0.0.0.0", port=9123)
    assert captured == {"host": "0.0.0.0", "port": 9123}


def test_startup_checks_and_required_env_fail_before_serving(monkeypatch):
    app = Agent()
    app.require_env("REQUIRED_FOR_TEST")
    monkeypatch.delenv("REQUIRED_FOR_TEST", raising=False)
    monkeypatch.setattr(
        "castia.observe.configuration.configure_observability",
        lambda: None,
    )
    monkeypatch.setattr(
        "castia.hosting.server.serve",
        lambda *args, **kwargs: pytest.fail("server must not start"),
    )
    with pytest.raises(RuntimeError, match="Missing required environment"):
        app.run()

    app = Agent()

    def fail_startup():
        raise ValueError("bad config")

    app.startup_check(fail_startup)
    with pytest.raises(RuntimeError, match="Startup validation failed in fail_startup"):
        app.run()


def test_readiness_reports_missing_required_env_without_failing_health(monkeypatch):
    monkeypatch.delenv("REQUIRED_FOR_READINESS", raising=False)
    app = Agent(name="ready-agent")
    app.require_env("REQUIRED_FOR_READINESS")

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.get("/readiness")
            assert response.status_code == 200
            body = response.json()
            assert body == {"status": "configuration_missing"}

            response = await test.client.get("/readiness", headers={"host": "127.0.0.1"})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "configuration_missing"
            assert body["agent"]["name"] == "ready-agent"
            assert body["configuration"]["missing_required"] == [
                "REQUIRED_FOR_READINESS"
            ]
            assert body["configuration"]["environment"]["REQUIRED_FOR_READINESS"] == {
                "present": False,
                "required": True,
            }

    asyncio.run(run())


def test_readiness_diagnostics_are_loopback_only():
    from castia.hosting import server

    app = Agent(name="public-agent")

    @app.responses()
    async def reply(text: str):
        return text

    asgi = server.build_app(
        app._routes,
        app._wire,
        app._invokes,
        agent_name=app.name,
        required_env=app._required_env,
    )

    async def run():
        remote_transport = httpx.ASGITransport(
            app=asgi,
            client=("203.0.113.10", 12345),
        )
        async with httpx.AsyncClient(
            transport=remote_transport,
            base_url="http://castia.test",
        ) as client:
            response = await client.get("/readiness?diagnostics=1")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

        local_transport = httpx.ASGITransport(
            app=asgi,
            client=("127.0.0.1", 12345),
        )
        async with httpx.AsyncClient(
            transport=local_transport,
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.get("/readiness")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            assert body["agent"]["name"] == "public-agent"
            assert body["protocols"] == ["responses"]

    asyncio.run(run())


def test_last_turn_diagnostics_are_opt_in_and_loopback_only(monkeypatch):
    from types import SimpleNamespace

    from castia.hosting import server
    from castia.inference.model import Model

    class Tool:
        name = "lookup_policy"

        def spec(self):
            return {"type": "function", "function": {"name": self.name}}

        async def run(self, activity, **kwargs):
            return {"ok": True, "policy": kwargs["policy"]}

    class Responses:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call-1",
                            name="lookup_policy",
                            arguments='{"policy":"travel"}',
                        )
                    ],
                    output_text="",
                )
            return SimpleNamespace(output=[], output_text="done")

    def model_provider():
        responses = Responses()
        model = Model.__new__(Model)
        model._deployment = "dep"
        model._instructions = None
        model._reasoning = {}
        model._tool_definitions = ()
        model._client = SimpleNamespace(
            get_openai_client=lambda: SimpleNamespace(responses=responses)
        )
        return model

    app = Agent(name="diagnostic-agent")

    @app.responses()
    async def reply(text: str, model=Depends(model_provider)):
        return await model.respond_with_tools(text, tools=[Tool()], activity=None)

    asgi = server.build_app(
        app._routes,
        app._wire,
        app._invokes,
        agent_name=app.name,
        required_env=app._required_env,
    )

    async def run():
        monkeypatch.delenv("CASTIA_DEV_DIAGNOSTICS", raising=False)
        local_transport = httpx.ASGITransport(
            app=asgi,
            client=("127.0.0.1", 12345),
        )
        async with httpx.AsyncClient(
            transport=local_transport,
            base_url="http://127.0.0.1",
        ) as client:
            disabled = await client.get("/diagnostics/last-turn")
            assert disabled.json() == {
                "enabled": False,
                "enable_with": "CASTIA_DEV_DIAGNOSTICS",
                "last_turn": None,
            }

        monkeypatch.setenv("CASTIA_DEV_DIAGNOSTICS", "1")
        async with AgentTestHarness(
            app,
            dependency_overrides={model_provider: model_provider},
        ) as test:
            response = await test.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "done"
            diagnostics = await test.client.get(
                "/diagnostics/last-turn",
                headers={"host": "127.0.0.1"},
            )
            body = diagnostics.json()
            turn = body["last_turn"]
            assert body["enabled"] is True
            assert turn["protocol"] == "responses"
            assert turn["input"] == "hello"
            assert turn["final_output"] == "done"
            assert turn["tool_calls"] == [
                {
                    "kind": "function",
                    "name": "lookup_policy",
                    "status": "ok",
                    "arguments": {"policy": "travel"},
                    "summary": "{'ok': True, 'policy': 'travel'}",
                }
            ]

        remote_transport = httpx.ASGITransport(
            app=asgi,
            client=("203.0.113.10", 12345),
        )
        async with httpx.AsyncClient(
            transport=remote_transport,
            base_url="http://castia.test",
        ) as client:
            remote = await client.get("/diagnostics/last-turn")
            assert remote.status_code == 404

    asyncio.run(run())


def test_port_probe_reports_occupied_port():
    from castia.hosting.server import _ensure_port_available

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(SystemExit, match="port .* is already in use"):
            _ensure_port_available("127.0.0.1", port)


def test_responses_stream_uses_stream_handler_for_sse(monkeypatch):
    from castia.hosting import server

    app = Agent()
    stream_attributes = {}

    @app.responses()
    async def reply(text: str):
        return f"single:{text}"

    @app.responses_stream()
    async def reply_stream(text: str):
        yield "stream:"
        yield text

    def record_stream_attributes(**attributes):
        stream_attributes.update(attributes)

    monkeypatch.setattr(server, "_record_stream_attributes", record_stream_attributes)

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses", json={"input": "hello", "stream": True}
            )
            assert_responses_sse_contract(
                response,
                deltas=["stream:", "hello"],
                output_text="stream:hello",
            )
            assert stream_attributes["chunk_count"] == 11
            assert stream_attributes["bytes_sent"] == len(response.text.encode("utf-8"))
            assert stream_attributes["first_chunk_at"] >= stream_attributes["started"]
            assert (
                stream_attributes["last_chunk_at"]
                >= stream_attributes["first_chunk_at"]
            )

            response = await test.client.post(
                "/responses", json={"input": "hello", "stream": False}
            )
            assert "application/json" in response.headers["content-type"]
            assert response.json()["output_text"] == "single:hello"
            assert response.json()["output"][0] == {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "single:hello"}],
            }

    asyncio.run(run())


def test_responses_stream_falls_back_to_responses_handler_for_sse():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return f"single:{text}"

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses", json={"input": "hello", "stream": True}
            )
            assert_responses_sse_contract(
                response,
                deltas=["single:hello"],
                output_text="single:hello",
            )

            response = await test.client.post(
                "/responses", json={"input": "hello"}
            )
            assert "application/json" in response.headers["content-type"]
            assert response.json()["output_text"] == "single:hello"
            assert response.json()["output"][0] == {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "single:hello"}],
            }

    asyncio.run(run())


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

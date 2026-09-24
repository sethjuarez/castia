"""The harness runs real routes and restores all process-wide test seams."""

import asyncio
import json
import socket
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from openai.types.responses.response import Response as OpenAIResponse
from openai.types.responses.response_stream_event import ResponseStreamEvent
from pydantic import TypeAdapter

from castia import Agent, Depends, Message, Teams, current_request_context
from castia.building import AgentTestHarness
from castia.building._offline import OfflineOperationError

FIXTURES = Path(__file__).with_name("fixtures")
OPENAI_STREAM_EVENT = TypeAdapter(ResponseStreamEvent)


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


def normalize_sse_payload(value, *, key: str | None = None):
    if isinstance(value, str):
        if key in {"id", "response_id"} and value.startswith("resp_"):
            return "resp_fixture"
        if key in {"id", "item_id"} and value.startswith("msg_"):
            return "msg_fixture"
        return value
    if isinstance(value, dict):
        return {
            child_key: 0
            if child_key == "created_at" and isinstance(item, int)
            else normalize_sse_payload(item, key=child_key)
            for child_key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_sse_payload(item, key=key) for item in value]
    return value


def normalized_sse_events(text: str):
    return [
        {"event": event, "data": normalize_sse_payload(payload)}
        for event, payload in sse_events(text)
    ]


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_responses_object_defaults(body: dict, *, output_text: str) -> None:
    assert body["object"] == "response"
    assert isinstance(body["created_at"], int)
    assert body["error"] is None
    assert body["incomplete_details"] is None
    assert body["instructions"] is None
    assert body["metadata"] == {}
    assert isinstance(body["model"], str)
    assert body["model"]
    assert body["output_text"] == output_text
    assert body["parallel_tool_calls"] is True
    assert body["tool_choice"] == "auto"
    assert body["tools"] == []
    if body["status"] == "completed":
        assert body["usage"] == {
            "input_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 0,
        }
    else:
        assert body["usage"] is None


def assert_sdk_parseable_response(body: dict) -> None:
    OpenAIResponse.model_validate(body)


def assert_sdk_parseable_stream_events(events) -> None:
    for event_type, payload in events:
        if event_type is not None:
            OPENAI_STREAM_EVENT.validate_python(payload)


def assert_response_message_output(body: dict, *, text: str) -> None:
    message = body["output"][0]
    assert message["id"].startswith("msg_")
    assert message["type"] == "message"
    assert message["role"] == "assistant"
    assert message["status"] == body["status"]
    assert message["content"] == [
        {
            "type": "output_text",
            "text": text,
            "annotations": [],
            "logprobs": [],
        }
    ]


def assert_empty_activity_error(
    response,
    *,
    status_code: int,
    activity_id: str | None = None,
    session_id: str | None = None,
) -> None:
    assert response.status_code == status_code
    assert response.content == b""
    assert response.headers["x-agent-activity-id"]
    assert response.headers["x-agent-session-id"]
    for header_name in ("x-agent-activity-id", "x-agent-session-id"):
        header = response.headers[header_name]
        assert "\r" not in header
        assert "\n" not in header
        assert len(header) <= 256
    if activity_id is not None:
        assert response.headers["x-agent-activity-id"] == activity_id
    if session_id is not None:
        assert response.headers["x-agent-session-id"] == session_id


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
    assert_sdk_parseable_stream_events(events)

    payloads = [payload for _, payload in events]
    assert [
        payload["sequence_number"]
        for payload in payloads[:-1]
    ] == list(range(len(payloads) - 1))
    response_id = payloads[0]["response"]["id"]
    output_item_id = payloads[2]["item"]["id"]
    assert payloads[1]["response"]["id"] == response_id
    assert_responses_object_defaults(payloads[0]["response"], output_text="")
    assert_responses_object_defaults(payloads[1]["response"], output_text="")
    assert payloads[1]["response"]["created_at"] == payloads[0]["response"]["created_at"]
    assert payloads[3]["item_id"] == output_item_id
    assert payloads[3]["part"]["logprobs"] == []

    delta_payloads = payloads[4: 4 + len(deltas)]
    assert [payload["delta"] for payload in delta_payloads] == deltas
    assert all(payload["type"] == "response.output_text.delta" for payload in delta_payloads)
    assert all(payload["item_id"] == output_item_id for payload in delta_payloads)
    assert all(payload["output_index"] == 0 for payload in delta_payloads)
    assert all(payload["content_index"] == 0 for payload in delta_payloads)
    assert all(payload["logprobs"] == [] for payload in delta_payloads)

    done_offset = 4 + len(deltas)
    assert payloads[done_offset]["type"] == "response.output_text.done"
    assert payloads[done_offset]["item_id"] == output_item_id
    assert payloads[done_offset]["output_index"] == 0
    assert payloads[done_offset]["content_index"] == 0
    assert payloads[done_offset]["logprobs"] == []
    assert payloads[done_offset]["text"] == output_text
    assert payloads[done_offset + 1]["type"] == "response.content_part.done"
    assert payloads[done_offset + 1]["item_id"] == output_item_id
    assert payloads[done_offset + 1]["part"]["text"] == output_text
    assert payloads[done_offset + 1]["part"]["logprobs"] == []
    assert payloads[done_offset + 2]["type"] == "response.output_item.done"
    assert payloads[done_offset + 2]["item"]["id"] == output_item_id
    assert payloads[done_offset + 2]["item"]["status"] == "completed"
    assert payloads[done_offset + 2]["item"]["content"][0]["text"] == output_text
    assert payloads[done_offset + 2]["item"]["content"][0]["logprobs"] == []
    assert payloads[done_offset + 3]["type"] == "response.completed"
    assert payloads[done_offset + 3]["response"]["id"] == response_id
    assert payloads[done_offset + 3]["response"]["status"] == "completed"
    assert_sdk_parseable_response(payloads[done_offset + 3]["response"])
    assert_responses_object_defaults(
        payloads[done_offset + 3]["response"], output_text=output_text
    )
    assert (
        payloads[done_offset + 3]["response"]["created_at"]
        == payloads[0]["response"]["created_at"]
    )
    assert payloads[done_offset + 3]["response"]["output"][0]["id"] == output_item_id
    assert (
        payloads[done_offset + 3]["response"]["output"][0]["content"][0]["logprobs"]
        == []
    )
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


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("previous_response_id", ["resp_previous", "", 123])
def test_responses_rejects_previous_response_id(stream, previous_response_id):
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={
                    "input": "hello",
                    "stream": stream,
                    "previous_response_id": previous_response_id,
                },
            )
            assert response.status_code == 400
            body = response.json()
            assert body["error"]["code"] == "unsupported_parameter"
            assert body["error"]["param"] == "previous_response_id"

            allowed = await test.client.post(
                "/responses",
                json={
                    "input": "hello",
                    "stream": stream,
                    "previous_response_id": None,
                },
            )
            assert allowed.status_code == 200
            if stream:
                events = sse_events(allowed.text)
                assert events[-2][0] == "response.completed"
                assert events[-2][1]["response"]["output_text"] == "hello"
            else:
                assert allowed.json()["output_text"] == "hello"

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("conversation", [None, "conv_previous", {}, {"id": "conv"}])
def test_responses_accepts_hosted_conversation_context(stream, conversation):
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "hello", "stream": stream, "conversation": conversation},
            )
            assert response.status_code == 200
            if stream:
                events = sse_events(response.text)
                assert events[-2][0] == "response.completed"
                assert events[-2][1]["response"]["output_text"] == "hello"
            else:
                assert response.json()["output_text"] == "hello"

    asyncio.run(run())


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
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

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
            assert normalized_sse_events(response.text) == load_fixture(
                "responses_sse_stream_golden.json"
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
            body = response.json()
            assert_responses_object_defaults(body, output_text="single:hello")
            assert_response_message_output(body, text="single:hello")
            assert_sdk_parseable_response(body)

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
            body = response.json()
            assert_responses_object_defaults(body, output_text="single:hello")
            assert_response_message_output(body, text="single:hello")
            assert_sdk_parseable_response(body)

    asyncio.run(run())


def test_responses_lifecycle_stores_completed_json_response():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return f"answer:{text}"

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post("/responses", json={"input": "hello"})
            assert created.status_code == 200
            body = created.json()
            response_id = body["id"]
            assert_responses_object_defaults(body, output_text="answer:hello")
            assert_response_message_output(body, text="answer:hello")
            assert_sdk_parseable_response(body)

            fetched = await test.client.get(f"/responses/{response_id}")
            assert fetched.status_code == 200
            stored = fetched.json()
            assert stored == body
            assert_sdk_parseable_response(stored)

            input_items = await test.client.get(f"/responses/{response_id}/input_items")
            assert input_items.status_code == 200
            page = input_items.json()
            assert page["object"] == "list"
            assert page["has_more"] is False
            assert page["first_id"] == f"{response_id}_input_0"
            assert page["last_id"] == f"{response_id}_input_0"
            assert page["data"] == [
                {
                    "id": f"{response_id}_input_0",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ]

            cancel = await test.client.post(f"/responses/{response_id}/cancel")
            assert cancel.status_code == 400
            assert cancel.json()["error"] == {
                "message": "Cannot cancel a completed response.",
                "type": "invalid_request_error",
                "param": "response_id",
                "code": "unsupported_parameter",
            }

            deleted = await test.client.delete(f"/responses/{response_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {
                "id": response_id,
                "object": "response",
                "deleted": True,
            }
            missing = await test.client.get(f"/responses/{response_id}")
            assert missing.status_code == 404

            cancel_missing = await test.client.post(f"/responses/{response_id}/cancel")
            assert cancel_missing.status_code == 404
            assert cancel_missing.json()["error"]["param"] == "response_id"

    asyncio.run(run())


def test_responses_lifecycle_honors_store_false_and_rejects_background():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post(
                "/responses", json={"input": "private", "store": False}
            )
            assert created.status_code == 200
            response_id = created.json()["id"]
            assert (await test.client.get(f"/responses/{response_id}")).status_code == 404
            assert (
                await test.client.get(f"/responses/{response_id}/input_items")
            ).status_code == 404

            for background in (False, None):
                created = await test.client.post(
                    "/responses",
                    json={
                        "input": f"background-{background}",
                        "background": background,
                        "store": False,
                    },
                )
                assert created.status_code == 200
                assert created.json()["output_text"] == f"background-{background}"

            background = await test.client.post(
                "/responses", json={"input": "later", "background": True}
            )
            assert background.status_code == 400
            assert background.json()["error"] == {
                "message": "background=true is not supported by this Castia runtime.",
                "type": "invalid_request_error",
                "param": "background",
                "code": "unsupported_parameter",
            }

            bad_body = await test.client.post("/responses", json=["not", "object"])
            assert bad_body.status_code == 400
            assert bad_body.json()["error"]["param"] == "body"

    asyncio.run(run())


def test_responses_lifecycle_evicts_oldest_completed_records(monkeypatch):
    from castia.hosting import server

    monkeypatch.setattr(server, "_MAX_STORED_RESPONSES", 2)
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return f"answer:{text}"

    async def run():
        async with AgentTestHarness(app) as test:
            created = []
            for value in ("one", "two", "three"):
                response = await test.client.post("/responses", json={"input": value})
                assert response.status_code == 200
                created.append(response.json())

            oldest, middle, newest = created
            assert (await test.client.get(f"/responses/{oldest['id']}")).status_code == 404
            assert (
                await test.client.get(f"/responses/{oldest['id']}/input_items")
            ).status_code == 404
            assert (
                await test.client.delete(f"/responses/{oldest['id']}")
            ).status_code == 404

            middle_get = await test.client.get(f"/responses/{middle['id']}")
            assert middle_get.status_code == 200
            assert middle_get.json() == middle

            newest_get = await test.client.get(f"/responses/{newest['id']}")
            assert newest_get.status_code == 200
            assert newest_get.json() == newest

    asyncio.run(run())


def test_responses_lifecycle_stores_concurrent_completed_records():
    app = Agent()
    gate = asyncio.Barrier(3)

    @app.responses()
    async def reply(text: str):
        await gate.wait()
        return f"answer:{text}"

    async def run():
        async with AgentTestHarness(app) as test:
            values = ("one", "two", "three")
            responses = await asyncio.gather(
                *[
                    test.client.post("/responses", json={"input": value})
                    for value in values
                ]
            )
            bodies = [response.json() for response in responses]
            assert [body["output_text"] for body in bodies] == [
                f"answer:{value}" for value in values
            ]
            ids = [body["id"] for body in bodies]
            assert len(set(ids)) == len(ids)

            fetched = await asyncio.gather(
                *[test.client.get(f"/responses/{response_id}") for response_id in ids]
            )
            assert [response.status_code for response in fetched] == [200, 200, 200]
            assert [response.json() for response in fetched] == bodies
            for body in bodies:
                assert_sdk_parseable_response(body)

    asyncio.run(run())


def test_responses_body_echoes_safe_request_shape_fields(monkeypatch):
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    request_body = {
        "input": "hello",
        "model": "request-model",
        "instructions": "Be concise.",
        "metadata": {"trace": "abc"},
        "tools": [{"type": "function", "name": "lookup"}],
        "tool_choice": "none",
        "parallel_tool_calls": False,
    }

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post("/responses", json=request_body)
            assert created.status_code == 200
            body = created.json()
            assert body["model"] == "request-model"
            assert body["instructions"] == "Be concise."
            assert body["metadata"] == {"trace": "abc"}
            assert body["tools"] == [{"type": "function", "name": "lookup"}]
            assert body["tool_choice"] == "none"
            assert body["parallel_tool_calls"] is False
            assert body["usage"]["total_tokens"] == 0

            fetched = await test.client.get(f"/responses/{body['id']}")
            assert fetched.status_code == 200
            stored = fetched.json()
            assert stored["model"] == body["model"]
            assert stored["instructions"] == body["instructions"]
            assert stored["metadata"] == body["metadata"]
            assert stored["tools"] == body["tools"]
            assert stored["tool_choice"] == body["tool_choice"]
            assert stored["parallel_tool_calls"] == body["parallel_tool_calls"]
            assert stored["created_at"] == body["created_at"]

    asyncio.run(run())


def test_responses_stream_echoes_safe_request_shape_fields(monkeypatch):
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    @app.responses_stream()
    async def reply_stream(text: str):
        yield text

    valid_request_body = {
        "input": "hello",
        "stream": True,
        "model": "stream-model",
        "instructions": ["Follow the wire shape."],
        "metadata": {"trace": "stream"},
        "tools": [{"type": "function", "name": "lookup"}],
        "tool_choice": {"type": "function", "name": "lookup"},
        "parallel_tool_calls": False,
    }
    unsafe_request_body = {
        "input": "unsafe",
        "stream": True,
        "model": ["not", "a", "string"],
        "instructions": {"not": "safe"},
        "metadata": ["not", "an", "object"],
        "tools": {"not": "a list"},
        "tool_choice": {"not": "safe"},
        "parallel_tool_calls": "nope",
    }

    def assert_echoed(body, *, expected_model, expected_instructions, expected_metadata,
                      expected_tools, expected_tool_choice, expected_parallel):
        assert body["model"] == expected_model
        assert body["instructions"] == expected_instructions
        assert body["metadata"] == expected_metadata
        assert body["tools"] == expected_tools
        assert body["tool_choice"] == expected_tool_choice
        assert body["parallel_tool_calls"] is expected_parallel

    async def run():
        async with AgentTestHarness(app) as test:
            streamed = await test.client.post("/responses", json=valid_request_body)
            assert streamed.status_code == 200
            events = sse_events(streamed.text)
            event_payloads = dict(events[:-1])
            assert events[-1] == (None, "[DONE]")
            completed = event_payloads["response.completed"]["response"]
            for response_body in (
                event_payloads["response.created"]["response"],
                event_payloads["response.in_progress"]["response"],
                completed,
            ):
                assert_echoed(
                    response_body,
                    expected_model="stream-model",
                    expected_instructions=["Follow the wire shape."],
                    expected_metadata={"trace": "stream"},
                    expected_tools=[{"type": "function", "name": "lookup"}],
                    expected_tool_choice={"type": "function", "name": "lookup"},
                    expected_parallel=False,
                )

            stored = await test.client.get(f"/responses/{completed['id']}")
            assert stored.status_code == 200
            assert_echoed(
                stored.json(),
                expected_model="stream-model",
                expected_instructions=["Follow the wire shape."],
                expected_metadata={"trace": "stream"},
                expected_tools=[{"type": "function", "name": "lookup"}],
                expected_tool_choice={"type": "function", "name": "lookup"},
                expected_parallel=False,
            )

            streamed = await test.client.post("/responses", json=unsafe_request_body)
            assert streamed.status_code == 200
            events = sse_events(streamed.text)
            assert events[-1] == (None, "[DONE]")
            unsafe_completed = dict(events[:-1])["response.completed"]["response"]
            assert_echoed(
                unsafe_completed,
                expected_model="castia-agent",
                expected_instructions=None,
                expected_metadata={},
                expected_tools=[],
                expected_tool_choice="auto",
                expected_parallel=True,
            )
            stored = await test.client.get(f"/responses/{unsafe_completed['id']}")
            assert stored.status_code == 200
            assert_echoed(
                stored.json(),
                expected_model="castia-agent",
                expected_instructions=None,
                expected_metadata={},
                expected_tools=[],
                expected_tool_choice="auto",
                expected_parallel=True,
            )

    asyncio.run(run())


def test_responses_stream_failure_emits_terminal_failed_event(monkeypatch):
    from castia.hosting import server

    app = Agent()
    stream_attributes = {}

    @app.responses()
    async def reply(text: str):
        return text

    @app.responses_stream()
    async def reply_stream(text: str):
        yield "partial"
        raise RuntimeError("boom")

    def record_stream_attributes(**attributes):
        stream_attributes.update(attributes)

    monkeypatch.setattr(server, "_record_stream_attributes", record_stream_attributes)

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses", json={"input": "hello", "stream": True}
            )
            assert response.status_code == 200
            events = sse_events(response.text)
            assert_sdk_parseable_stream_events(events)
            assert [event for event, _ in events] == [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.failed",
                None,
            ]
            payloads = [payload for _, payload in events]
            assert [
                payload["sequence_number"]
                for payload in payloads[:-1]
            ] == list(range(len(payloads) - 1))
            failed = payloads[-2]
            assert failed["type"] == "response.failed"
            assert failed["response"]["status"] == "failed"
            assert failed["response"]["output_text"] == "partial"
            assert failed["response"]["error"] == {
                "code": "server_error",
                "message": "An internal server error occurred.",
            }
            assert failed["response"]["output"][0]["status"] == "incomplete"
            assert_sdk_parseable_response(failed["response"])
            assert payloads[-1] == "[DONE]"

            fetched = await test.client.get(f"/responses/{failed['response']['id']}")
            assert fetched.status_code == 200
            assert fetched.json() == failed["response"]

            input_items = await test.client.get(
                f"/responses/{failed['response']['id']}/input_items"
            )
            assert input_items.status_code == 200
            assert input_items.json()["data"][0]["content"][0]["text"] == "hello"
            cancel = await test.client.post(
                f"/responses/{failed['response']['id']}/cancel"
            )
            assert cancel.status_code == 400
            assert cancel.json()["error"] == {
                "message": "Cannot cancel a failed response.",
                "type": "invalid_request_error",
                "param": "response_id",
                "code": "unsupported_parameter",
            }
            assert stream_attributes["terminal_status"] == "failed"
            assert stream_attributes["chunk_count"] == 7

    asyncio.run(run())


def test_responses_stream_failure_store_false_leaves_no_record():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    @app.responses_stream()
    async def reply_stream(text: str):
        raise RuntimeError("boom")

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "hello", "stream": True, "store": False},
            )
            assert response.status_code == 200
            events = sse_events(response.text)
            assert_sdk_parseable_stream_events(events)
            assert [event for event, _ in events] == [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.content_part.added",
                "response.failed",
                None,
            ]
            failed_response = events[-2][1]["response"]
            assert failed_response["status"] == "failed"
            assert failed_response["output_text"] == ""
            assert failed_response["output"][0]["status"] == "incomplete"
            assert_sdk_parseable_response(failed_response)

            fetched = await test.client.get(f"/responses/{failed_response['id']}")
            assert fetched.status_code == 404

    asyncio.run(run())


def test_responses_lifecycle_stores_completed_stream_response():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return f"single:{text}"

    @app.responses_stream()
    async def reply_stream(text: str):
        yield "stream:"
        yield text

    async def run():
        async with AgentTestHarness(app) as test:
            streamed = await test.client.post(
                "/responses", json={"input": "hello", "stream": True}
            )
            assert streamed.status_code == 200
            events = sse_events(streamed.text)
            completed = next(
                payload
                for event, payload in events
                if event == "response.completed"
            )
            response_id = completed["response"]["id"]

            fetched = await test.client.get(f"/responses/{response_id}")
            assert fetched.status_code == 200
            assert_responses_object_defaults(
                fetched.json(), output_text="stream:hello"
            )

    asyncio.run(run())


def test_responses_input_items_pagination_and_order():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    input_value = [
        {"role": "user", "content": "first", "id": "client-id-ignored"},
        {"role": "user", "content": "second"},
        "third",
    ]

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post("/responses", json={"input": input_value})
            response_id = created.json()["id"]

            desc = await test.client.get(
                f"/responses/{response_id}/input_items?limit=1"
            )
            assert desc.status_code == 200
            desc_page = desc.json()
            assert desc_page["has_more"] is True
            assert [item["id"] for item in desc_page["data"]] == [
                f"{response_id}_input_2"
            ]

            asc = await test.client.get(
                f"/responses/{response_id}/input_items?order=asc&limit=2"
            )
            assert asc.status_code == 200
            asc_page = asc.json()
            assert asc_page["has_more"] is True
            assert [item["id"] for item in asc_page["data"]] == [
                f"{response_id}_input_0",
                f"{response_id}_input_1",
            ]

            after = await test.client.get(
                f"/responses/{response_id}/input_items"
                f"?order=asc&after={response_id}_input_0"
            )
            assert [item["id"] for item in after.json()["data"]] == [
                f"{response_id}_input_1",
                f"{response_id}_input_2",
            ]

            bad_limit = await test.client.get(
                f"/responses/{response_id}/input_items?limit=0"
            )
            assert bad_limit.status_code == 400
            bad_order = await test.client.get(
                f"/responses/{response_id}/input_items?order=random"
            )
            assert bad_order.status_code == 400

    asyncio.run(run())


@pytest.mark.parametrize("bad_body", [["bad"], "bad", 7])
def test_responses_rejects_non_object_json_bodies(bad_body):
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post("/responses", json=bad_body)
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "body"

    asyncio.run(run())


@pytest.mark.parametrize("raw_body", [b"", b"null", b'{"input":', b"not json"])
def test_responses_rejects_unparseable_or_non_object_raw_bodies(raw_body):
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                content=raw_body,
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "body"

    asyncio.run(run())


@pytest.mark.parametrize("bad_body", [["bad"], "bad", 7])
def test_chat_rejects_non_object_json_bodies(bad_body):
    app = Agent()

    @app.chat()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post("/chat/completions", json=bad_body)
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "body"

    asyncio.run(run())


@pytest.mark.parametrize("raw_body", [b"", b"null", b'{"messages":', b"not json"])
def test_chat_rejects_unparseable_or_non_object_raw_bodies(raw_body):
    app = Agent()

    @app.chat()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/chat/completions",
                content=raw_body,
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "body"

    asyncio.run(run())


def test_responses_input_items_rejects_malformed_query_params():
    app = Agent()

    @app.responses()
    async def reply(text: str):
        return text

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post("/responses", json={"input": "hello"})
            response_id = created.json()["id"]
            cases = [
                ("limit=not-an-int", "limit"),
                ("limit=-1", "limit"),
                ("limit=101", "limit"),
                ("order=random", "order"),
            ]
            for query, param in cases:
                response = await test.client.get(
                    f"/responses/{response_id}/input_items?{query}"
                )
                assert response.status_code == 400
                assert response.json()["error"]["param"] == param

    asyncio.run(run())


def test_connector_capture_all_verbs_and_streaming_without_auth(monkeypatch):
    from castia.messaging import connector

    async def forbidden(*args, **kwargs):
        pytest.fail("Connector authentication must not run")

    monkeypatch.setattr(connector, "bot_connector_token", forbidden)
    app = Agent()

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        assert current_request_context().session_id == "session-header"
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
            response = await test.client.post(
                "/activity/messages",
                json=activity(),
                headers={"x-agent-session-id": "session-header"},
            )
            assert response.status_code == 200
            assert response.headers["x-agent-activity-id"] == "turn"
            assert response.headers["x-agent-session-id"] == "session-header"
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
        if value.get("action") == "query-session":
            assert current_request_context().session_id == "session-query"
        return {"received": value}

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post("/activity/messages", json=activity(
                type="invoke", name="custom", value={"action": "test"}
            ))
            assert response.json() == {"received": {"action": "test"}}
            assert response.headers["x-agent-activity-id"] == "turn"
            response = await test.client.post("/activity/messages", json=activity(
                type="invoke", name="unknown", id="bad id\n"
            ))
            assert response.json() == {}
            fallback_activity_id = response.headers["x-agent-activity-id"]
            assert fallback_activity_id != "bad id\n"
            assert len(fallback_activity_id) == 36
            response = await test.client.post(
                "/activity/messages?agent_session_id=session-query",
                json=activity(
                    type="invoke",
                    name="custom",
                    value={"action": "query-session"},
                    id="activity-query-session",
                ),
                headers={"x-agent-session-id": "session-header"},
            )
            assert response.headers["x-agent-session-id"] == "session-query"
            response = await test.client.post(
                "/activity/messages?agent_session_id=bad%0d%0aX-Evil:%201",
                json=activity(id=5),
                headers={"x-agent-session-id": "session-header"},
            )
            assert_empty_activity_error(response, status_code=400)
            assert "\n" not in response.headers["x-agent-session-id"]
            assert response.headers["x-agent-activity-id"] != "5"
            malformed = await test.client.post("/activity/messages", content="{")
            assert_empty_activity_error(malformed, status_code=400)
            non_object = await test.client.post("/activity/messages", json=["bad"])
            assert_empty_activity_error(non_object, status_code=400)
            assert not test.egress
            assert (await test.client.post("/responses", json={})).status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize(
    ("activity_id", "expected_status"),
    [
        ("", 200),
        ("x" * 257, 200),
        ("bad id", 200),
        ("bad\nid", 200),
        (5, 400),
    ],
)
def test_activity_malformed_ids_fall_back_to_safe_header(
    activity_id, expected_status
):
    app = Agent()

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        return None

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/activity/messages",
                json=activity(id=activity_id),
                headers={"x-agent-session-id": "session-header"},
            )
            assert response.status_code == expected_status
            if expected_status >= 400:
                assert_empty_activity_error(response, status_code=expected_status)
            header = response.headers["x-agent-activity-id"]
            assert header != str(activity_id)
            uuid.UUID(header)
            assert response.headers["x-agent-session-id"] == "session-header"

    asyncio.run(run())


def test_activity_session_id_uses_valid_query_then_header_then_uuid():
    app = Agent()

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        return None

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/activity/messages?agent_session_id=query-session",
                json=activity(),
                headers={"x-agent-session-id": "header-session"},
            )
            assert response.headers["x-agent-session-id"] == "query-session"

            response = await test.client.post(
                "/activity/messages?agent_session_id=bad%0d%0aInjected:%201",
                json=activity(id="bad-query-session"),
                headers={"x-agent-session-id": "header-session"},
            )
            assert response.headers["x-agent-session-id"] == "header-session"

            response = await test.client.post(
                "/activity/messages",
                json=activity(id="bad-header-session"),
                headers={"x-agent-session-id": "bad session"},
            )
            uuid.UUID(response.headers["x-agent-session-id"])

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


def test_activity_failures_preserve_protocol_headers():
    app = Agent()
    calls = []

    @app.activity(Teams.direct)
    async def reply(msg: Message):
        calls.append("message")
        raise RuntimeError("boom")

    @app.invoke("fail")
    async def invoke(value):
        calls.append("invoke")
        raise RuntimeError("invoke boom")

    @app.invoke("non-json")
    async def non_json(value):
        calls.append("non-json")
        return {"bad": {object()}}

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/activity/messages?agent_session_id=session-error",
                json=activity(id="activity-error"),
            )
            assert_empty_activity_error(
                response,
                status_code=500,
                activity_id="activity-error",
                session_id="session-error",
            )
            assert calls[-1] == "message"

            response = await test.client.post(
                "/activity/messages",
                json=activity(
                    type="invoke",
                    name="fail",
                    id="invoke-error",
                ),
                headers={"x-agent-session-id": "session-invoke-error"},
            )
            assert_empty_activity_error(
                response,
                status_code=500,
                activity_id="invoke-error",
                session_id="session-invoke-error",
            )
            assert calls[-1] == "invoke"

            response = await test.client.post(
                "/activity/messages",
                json=activity(
                    type="invoke",
                    name="non-json",
                    id="invoke-serialization-error",
                ),
                headers={"x-agent-session-id": "session-serialization-error"},
            )
            assert_empty_activity_error(
                response,
                status_code=500,
                activity_id="invoke-serialization-error",
                session_id="session-serialization-error",
            )
            assert calls[-1] == "non-json"

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

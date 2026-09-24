from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from openai.types.responses.response import Response as OpenAIResponse

from castia import Agent, current_request_context, toolbox_mcp_tool
from castia.building import AgentTestHarness
from castia.inference.model import Model, _public_tool_spec
from castia.runtime.request_context import (
    FOUNDRY_CALL_ID_HEADER,
    FOUNDRY_SESSION_ID_HEADER,
    FOUNDRY_USER_ID_HEADER,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from castia.runtime.request_context import (
    current_request_context as runtime_request_context,
)


def test_current_request_context_is_empty_outside_request() -> None:
    context = current_request_context()
    assert context.call_id is None
    assert context.user_id is None
    assert context.session_id is None
    assert context.platform_headers() == {}


def test_wire_handlers_receive_foundry_request_context() -> None:
    app = Agent()

    @app.responses()
    @app.invocations()
    @app.chat()
    async def reply(text: str) -> str:
        context = current_request_context()
        assert context.user_id == "user-1"
        assert context.call_id == "call-1"
        assert context.session_id == "session-1"
        assert context.platform_headers() == {FOUNDRY_CALL_ID_HEADER: "call-1"}
        return f"{text}:{context.call_id}"

    headers = {
        FOUNDRY_USER_ID_HEADER: "user-1",
        FOUNDRY_CALL_ID_HEADER: "call-1",
        FOUNDRY_SESSION_ID_HEADER: "session-1",
    }

    async def run() -> None:
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses", json={"input": "hello"}, headers=headers
            )
            assert response.json()["output_text"] == "hello:call-1"

            response = await test.client.post(
                "/chat/completions",
                json={"messages": [{"role": "user", "content": "chat"}]},
                headers=headers,
            )
            assert response.json()["choices"][0]["message"]["content"] == "chat:call-1"

            response = await test.client.post(
                "/invocations", json={"input": "invoke"}, headers=headers
            )
            assert response.json() == {"output": "invoke:call-1"}

        assert current_request_context().call_id is None

    asyncio.run(run())


def test_streaming_responses_keep_request_context_for_entire_generator() -> None:
    app = Agent()

    @app.responses()
    async def fallback(text: str) -> str:
        return f"fallback:{text}"

    @app.responses_stream()
    async def stream(text: str):
        yield f"{text}:"
        yield current_request_context().call_id or "missing"

    async def run() -> None:
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "stream", "stream": True},
                headers={FOUNDRY_CALL_ID_HEADER: "call-stream"},
            )
            assert '"delta":"stream:"' in response.text
            assert '"delta":"call-stream"' in response.text

        assert current_request_context().call_id is None

    asyncio.run(run())


def test_toolbox_mcp_tool_forwards_only_platform_call_id() -> None:
    token = set_request_context(
        RequestContext(call_id="call-2", user_id="user-2", session_id="session-2")
    )
    try:
        spec = toolbox_mcp_tool("https://x/mcp", token="secret")
        public = _public_tool_spec(spec)
    finally:
        reset_request_context(token)

    assert spec is not None
    assert spec["headers"]["Authorization"] == "Bearer secret"
    assert FOUNDRY_CALL_ID_HEADER not in spec["headers"]
    assert public["headers"]["Authorization"] == "Bearer secret"
    assert public["headers"][FOUNDRY_CALL_ID_HEADER] == "call-2"
    assert FOUNDRY_USER_ID_HEADER not in public["headers"]
    assert FOUNDRY_SESSION_ID_HEADER not in public["headers"]
    assert runtime_request_context().platform_headers() == {}


def test_public_tool_spec_replaces_stale_cached_platform_call_id() -> None:
    spec = {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
        "headers": {FOUNDRY_CALL_ID_HEADER: "stale", "x-custom": "1"},
    }
    token = set_request_context(
        RequestContext(call_id="fresh", user_id="user-2", session_id="session-2")
    )
    try:
        public = _public_tool_spec(spec)
    finally:
        reset_request_context(token)

    assert public["headers"] == {"x-custom": "1", FOUNDRY_CALL_ID_HEADER: "fresh"}


def test_public_tool_spec_removes_stale_cached_platform_call_id_without_context() -> None:
    spec = {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
        "headers": {FOUNDRY_CALL_ID_HEADER: "stale"},
    }

    assert _public_tool_spec(spec) == {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
    }


def test_blank_platform_call_id_is_not_forwarded() -> None:
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        spec = toolbox_mcp_tool("https://x/mcp")
        assert spec is not None
        return str(_public_tool_spec(spec).get("headers", {}))

    async def run() -> None:
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "hello"},
                headers={FOUNDRY_CALL_ID_HEADER: ""},
            )
            assert response.json()["output_text"] == "{}"

    asyncio.run(run())


def test_model_normalizes_cached_toolbox_spec_per_request_call_id() -> None:
    cached_spec = toolbox_mcp_tool("https://x/mcp")
    assert cached_spec is not None
    seen: list[dict] = []

    async def reply(text: str) -> str:
        model = Model.__new__(Model)
        model._deployment = "gpt-4o"
        model._instructions = None
        model._tool_definitions = ()
        model._reasoning = {}

        async def create(**kwargs):
            seen.append(kwargs["tools"][0]["headers"])
            return SimpleNamespace(output=[], output_text="ok")

        model._client = SimpleNamespace(
            get_openai_client=lambda: SimpleNamespace(
                responses=SimpleNamespace(create=create)
            )
        )
        return await model.respond_with_tools(
            text,
            tools=[],
            activity=None,
            extra_specs=[cached_spec],
        )

    app = Agent()
    app.responses()(reply)

    async def run() -> None:
        async with AgentTestHarness(app) as test:
            for call_id in ("call-a", "call-b"):
                response = await test.client.post(
                    "/responses",
                    json={"input": "hello"},
                    headers={FOUNDRY_CALL_ID_HEADER: call_id},
                )
                assert response.json()["output_text"] == "ok"

    asyncio.run(run())

    assert seen == [
        {FOUNDRY_CALL_ID_HEADER: "call-a"},
        {FOUNDRY_CALL_ID_HEADER: "call-b"},
    ]


def test_explicit_headers_do_not_override_fresh_platform_call_id() -> None:
    token = set_request_context(RequestContext(call_id="platform-call"))
    try:
        spec = toolbox_mcp_tool(
            "https://x/mcp",
            headers={FOUNDRY_CALL_ID_HEADER: "explicit-call"},
        )
        public = _public_tool_spec(spec)
    finally:
        reset_request_context(token)

    assert spec is not None
    assert spec["headers"] == {FOUNDRY_CALL_ID_HEADER: "explicit-call"}
    assert public["headers"] == {FOUNDRY_CALL_ID_HEADER: "platform-call"}


def test_responses_tool_loop_preserves_context_specs_and_response_shape() -> None:
    cached_spec = toolbox_mcp_tool("https://x/mcp", token="secret")
    assert cached_spec is not None
    seen: list[dict] = []
    tool_runs: list[dict] = []

    class Tool:
        name = "lookup_policy"

        def spec(self):
            return {"type": "function", "function": {"name": self.name}}

        async def run(self, activity, **kwargs):
            tool_runs.append({"activity": activity, "args": kwargs})
            return {"policy": kwargs["policy"], "allowed": True}

    class Responses:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            seen.append(kwargs)
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
            return SimpleNamespace(
                output=[SimpleNamespace(type="message")],
                output_text="policy:travel",
            )

    async def reply(text: str) -> str:
        model = Model.__new__(Model)
        model._deployment = "gpt-4o"
        model._instructions = None
        model._tool_definitions = ()
        model._reasoning = {}
        responses = Responses()
        model._client = SimpleNamespace(
            get_openai_client=lambda: SimpleNamespace(responses=responses)
        )
        return await model.respond_with_tools(
            text,
            tools=[Tool()],
            activity=None,
            extra_specs=[cached_spec],
        )

    app = Agent()
    app.responses()(reply)

    async def run() -> None:
        async with AgentTestHarness(app) as test:
            created = await test.client.post(
                "/responses",
                json={"input": "Can I travel?"},
                headers={FOUNDRY_CALL_ID_HEADER: "call-tool-loop"},
            )
            assert created.status_code == 200
            body = created.json()
            assert body["output_text"] == "policy:travel"
            OpenAIResponse.model_validate(body)

            stored = await test.client.get(f"/responses/{body['id']}")
            assert stored.status_code == 200
            assert stored.json() == body

    asyncio.run(run())

    assert tool_runs == [{"activity": None, "args": {"policy": "travel"}}]
    assert len(seen) == 2
    first_tools = seen[0]["tools"]
    assert first_tools[0] == {"type": "function", "function": {"name": "lookup_policy"}}
    assert first_tools[1]["type"] == "mcp"
    assert "Authorization" in first_tools[1]["headers"]
    assert first_tools[1]["headers"][FOUNDRY_CALL_ID_HEADER] == "call-tool-loop"
    assert seen[1]["input"][-2] == {
        "type": "function_call",
        "call_id": "call-1",
        "name": "lookup_policy",
        "arguments": '{"policy":"travel"}',
    }
    assert seen[1]["input"][-1]["type"] == "function_call_output"
    assert seen[1]["input"][-1]["call_id"] == "call-1"
    assert json.loads(seen[1]["input"][-1]["output"]) == {
        "policy": "travel",
        "allowed": True,
    }

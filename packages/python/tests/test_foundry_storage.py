"""Foundry response storage and platform response-id contract.

Wire shapes are pinned to ``azure-ai-agentserver-responses`` 2.4.0
(``store/_foundry_provider.py``, ``_foundry_serializer.py``,
``hosting/_request_parsing.py``, ``_id_generator.py``): a hosted agent with no
explicit store writes every response to ``{project}/storage`` so the platform's
``GET /openai/v1/responses/{id}`` can read it.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from castia import Agent, response_history
from castia.building import AgentTestHarness
from castia.hosting import foundry_storage, server
from castia.hosting.agent_identity import (
    InvalidAgentReferenceError,
    resolve_agent_reference,
    resolve_agent_session_id,
)
from castia.hosting.foundry_storage import (
    FoundryResponseAlreadyExistsError,
    FoundryResponseStore,
    FoundryStorageError,
)
from castia.hosting.response_ids import (
    InvalidResponseIdError,
    item_id,
    platform_id,
    resolve_response_id,
    validate_id,
)
from castia.runtime.request_context import (
    RequestContext,
    reset_request_context,
    set_request_context,
)

ENDPOINT = "https://acct.services.ai.azure.com/api/projects/proj"
STORAGE = f"{ENDPOINT}/storage/"
NEW_ID = "caresp_" + "a" * 16 + "00" + "b" * 32
LEGACY_ID = "resp_" + "c" * 32 + "d" * 16
CALL = {"x-agent-foundry-call-id": "call-123", "x-agent-user-id": "user-a"}
FOUNDRY_ID = re.compile(r"caresp_[0-9a-f]{16}00[0-9a-f]{32}")
REF = {"type": "agent_reference", "name": "contract-policy-analyst", "version": "11"}
STOCK = json.loads(
    (Path(__file__).parent / "fixtures" / "stock_foundry_storage_2_4_0.json").read_text(
        encoding="utf-8"
    )
)


class FakeCredential:
    def __init__(self):
        self.scopes = []

    async def get_token(self, *scopes):
        self.scopes.append(scopes)
        return SimpleNamespace(token="tok", expires_on=4_102_444_800)


class FakeStorage:
    """An in-memory stand-in for ``{project}/storage`` that records every call."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.input_items: dict[str, list] = {}
        self.items: dict[str, dict] = {}
        self.history: dict[str, list] = {}
        self.calls: list[tuple[str, str, dict, object]] = []
        self.fail: dict[tuple[str, str], int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.split("/storage/", 1)[1]
        self.calls.append((request.method, str(request.url), dict(request.headers), body))
        status = self.fail.get((request.method, path)) or self.fail.get((request.method, "*"))
        if status:
            return httpx.Response(status, json={"error": {"code": "boom", "message": "down"}})
        if request.method == "POST" and path == "responses":
            response = body["response"]
            if response["id"] in self.responses:
                return httpx.Response(
                    409, json={"error": {"code": "conflict", "message": "already exists"}}
                )
            self.responses[response["id"]] = response
            self.input_items[response["id"]] = body["input_items"]
            self.history[response["id"]] = body["history_item_ids"]
            self._index(response, body["input_items"])
            return httpx.Response(200, json=response)
        if path == "items/batch/retrieve":
            return httpx.Response(200, json=[self.items.get(i) for i in body["item_ids"]])
        if path == "history/item_ids":
            return self._history(request.url.params)
        parts = path.split("/")
        response_id = parts[1]
        if response_id not in self.responses:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})
        if len(parts) == 3:
            items = self.input_items[response_id]
            after = request.url.params.get("after")
            start = 0 if after is None else [i["id"] for i in items].index(after) + 1
            limit = int(request.url.params["limit"])
            page = items[start : start + limit]
            return httpx.Response(
                200, json={"data": page, "has_more": start + limit < len(items)}
            )
        if request.method == "GET":
            return httpx.Response(200, json=self.responses[response_id])
        if request.method == "DELETE":
            del self.responses[response_id]
            return httpx.Response(200, json={"deleted": True})
        self.responses[response_id] = body
        self._index(body, [])
        return httpx.Response(200, json=body)

    def _index(self, response, input_items):
        for item in [*input_items, *response.get("output", [])]:
            if isinstance(item, dict) and item.get("id"):
                self.items[item["id"]] = item

    def _item_ids(self, response_id):
        response = self.responses[response_id]
        return [
            *self.history[response_id],
            *(i["id"] for i in self.input_items[response_id] if i),
            *(o["id"] for o in response.get("output", [])),
        ]

    def _history(self, params):
        """Stock ``get_history_item_ids``: prior ids + inputs + outputs, per response."""
        previous = params.get("previous_response_id")
        conversation = params.get("conversation_id")
        ids = []
        if previous is not None:
            if previous not in self.responses:
                return httpx.Response(
                    404, json={"error": {"code": "not_found", "message": "nope"}}
                )
            ids += self._item_ids(previous)
        if conversation is not None:
            for response_id, response in self.responses.items():
                if (response.get("conversation") or {}).get("id") == conversation:
                    ids += self._item_ids(response_id)
        return httpx.Response(200, json=list(dict.fromkeys(ids)))


def _store(storage: FakeStorage, **kwargs) -> FoundryResponseStore:
    return FoundryResponseStore(
        ENDPOINT,
        credential=FakeCredential(),
        transport=httpx.MockTransport(storage.handler),
        retry_backoff=0,
        **kwargs,
    )


def _record(response_id=NEW_ID, input_items=None, **response):
    return {
        "response": {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "agent_reference": REF,
            **response,
        },
        "input_items": input_items or [],
        "background": False,
        "user_id": "ignored",
    }


# --- ids: parity with agentserver IdGenerator / request parsing -------------


def test_platform_id_reuses_partition_like_the_agentserver_id_generator():
    assert platform_id("msg", NEW_ID)[4:22] == "a" * 16 + "00"
    assert platform_id("msg", LEGACY_ID)[4:22] == "d" * 16 + "00"
    for like in (None, "resp_short", "nounderscore"):
        minted = platform_id("caresp", like)
        assert FOUNDRY_ID.fullmatch(minted) and minted[23:25] == "00"
    with pytest.raises(ValueError):
        platform_id("")


def test_validate_id_matches_agentserver_is_valid():
    assert validate_id(NEW_ID) is None
    assert validate_id(LEGACY_ID) is None
    assert validate_id("x_" + "z" * 50) is None  # no charset/prefix check upstream
    for bad in ("", None, "no-delimiter", "_" + "a" * 50, "resp_abc"):
        assert validate_id(bad) is not None


def test_response_id_precedence_is_header_then_body_then_minted():
    other = "caresp_" + "e" * 16 + "00" + "f" * 32
    assert resolve_response_id(NEW_ID, other, prefix="caresp") == NEW_ID
    assert resolve_response_id("  ", other, prefix="caresp") == other
    assert resolve_response_id(None, f" {other} ", prefix="caresp") == other
    assert resolve_response_id(None, 42, prefix="caresp").startswith("caresp_")
    minted = resolve_response_id(None, None, prefix="resp", like=NEW_ID)
    assert minted.startswith("resp_" + "a" * 16 + "00")
    with pytest.raises(InvalidResponseIdError, match="Malformed identifier"):
        resolve_response_id("resp_bad", None, prefix="caresp")


def test_item_ids_use_agentserver_type_prefixes():
    assert item_id({"type": "function_call_output"}, NEW_ID).startswith(
        "fco_" + "a" * 16 + "00"
    )
    assert item_id({"role": "user", "content": "hi"}, NEW_ID).startswith("msg_")
    assert item_id({"type": "item_reference", "id": "x"}, NEW_ID) is None
    assert item_id({"type": "brand_new_type"}, NEW_ID) is None


# --- FoundryResponseStore wire contract -------------------------------------


def test_create_posts_the_agentserver_envelope_with_call_id_and_scope():
    storage = FakeStorage()
    storage.items["msg_ref"] = {"id": "msg_ref", "type": "message", "role": "user"}
    storage.responses[LEGACY_ID] = {"id": LEGACY_ID, "output": [{"id": "msg_prev_2"}]}
    storage.input_items[LEGACY_ID] = [{"id": "msg_prev_1"}]
    storage.history[LEGACY_ID] = []
    store = _store(storage)
    record = _record(
        previous_response_id=LEGACY_ID,
        input_items=[
            {"role": "user", "content": "hello", "status": "in_progress"},
            {"type": "item_reference", "id": "msg_ref"},
            {"type": "function_call_output", "call_id": "c1", "output": "42"},
            {"type": "unsupported_kind"},
        ],
    )

    async def run():
        token = set_request_context(RequestContext(call_id="call-123", user_id="user-a"))
        try:
            await store.create(record)
        finally:
            reset_request_context(token)
        await store.aclose()

    asyncio.run(run())
    batch, history, create = storage.calls
    assert batch[:2] == ("POST", f"{STORAGE}items/batch/retrieve?api-version=v1")
    assert batch[3] == {"item_ids": ["msg_ref"]}
    assert history[1] == (
        f"{STORAGE}history/item_ids?api-version=v1&limit=-1"
        f"&previous_response_id={LEGACY_ID}"
    )
    method, url, headers, body = create
    assert (method, url) == ("POST", f"{STORAGE}responses?api-version=v1")
    assert headers["authorization"] == "Bearer tok"
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["x-agent-foundry-call-id"] == "call-123"
    assert "x-agent-user-id" not in headers
    assert store._credential.scopes == [("https://ai.azure.com/.default",)]
    assert body["response"] == {
        **record["response"],
        "response_id": NEW_ID,
        "background": False,
        "completed_at": body["response"]["completed_at"],
        "output": [],
    }
    assert body["history_item_ids"] == ["msg_prev_1", "msg_prev_2"]
    message, reference, tool_output = body["input_items"]
    assert message["id"].startswith("msg_" + "a" * 16 + "00")
    assert message["status"] == "completed"
    assert message["content"] == [{"type": "input_text", "text": "hello"}]
    assert reference == storage.items["msg_ref"]
    assert tool_output["id"].startswith("fco_") and tool_output["status"] == "completed"


def test_missing_item_reference_fails_explicitly():
    storage = FakeStorage()
    store = _store(storage)
    record = _record(input_items=[{"type": "item_reference", "id": "msg_gone"}])
    with pytest.raises(FoundryStorageError, match="msg_gone"):
        asyncio.run(store.create(record))
    assert [call[1].split("?")[0] for call in storage.calls] == [
        f"{STORAGE}items/batch/retrieve"
    ]


def test_get_reads_response_and_pages_input_items():
    storage = FakeStorage()
    store = _store(storage)
    items = [{"type": "message", "role": "user", "content": str(i)} for i in range(1001)]

    async def run():
        await store.create(_record(input_items=items))
        record = await store.get(NEW_ID)
        missing = await store.get("caresp_" + "0" * 50)
        return record, missing

    record, missing = asyncio.run(run())
    assert missing is None
    assert record["response"]["id"] == NEW_ID
    assert record["user_id"] is None and record["background"] is False
    assert len(record["input_items"]) == 1001
    pages = [call[1] for call in storage.calls if "/input_items" in call[1]]
    assert pages[0].endswith("input_items?api-version=v1&limit=100&order=asc")
    assert "&after=" in pages[1]


def test_update_replace_and_delete_use_the_response_path():
    storage = FakeStorage()
    store = _store(storage)

    async def run():
        await store.create(_record(status="in_progress"))
        stale = await store.update(_record(status="cancelled"), expected_status="queued")
        swapped = await store.update(_record(status="completed"), expected_status="in_progress")
        deleted = await store.delete(NEW_ID)
        again = await store.delete(NEW_ID)
        return stale, swapped, deleted, again

    assert asyncio.run(run()) == (False, True, True, False)
    writes = [(m, u) for m, u, _, _ in storage.calls if m in {"POST", "DELETE"}][1:]
    assert writes == [
        ("POST", f"{STORAGE}responses/{NEW_ID}?api-version=v1"),
        ("DELETE", f"{STORAGE}responses/{NEW_ID}?api-version=v1"),
        ("DELETE", f"{STORAGE}responses/{NEW_ID}?api-version=v1"),
    ]


def test_errors_are_mapped_and_transient_failures_retried():
    storage = FakeStorage()
    store = _store(storage)
    asyncio.run(store.create(_record()))
    with pytest.raises(FoundryResponseAlreadyExistsError):
        asyncio.run(store.create(_record()))

    storage.fail[("GET", "*")] = 503
    storage.calls.clear()
    with pytest.raises(FoundryStorageError) as error:
        asyncio.run(store.get(NEW_ID))
    assert error.value.status_code == 503 and error.value.message == "down"
    assert len(storage.calls) == 3

    storage.fail[("GET", "*")] = 403
    storage.calls.clear()
    with pytest.raises(FoundryStorageError):
        asyncio.run(store.get(NEW_ID))
    assert len(storage.calls) == 1


def test_from_env_requires_an_http_project_endpoint(monkeypatch):
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        FoundryResponseStore.from_env()
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "not-a-url")
    with pytest.raises(ValueError, match="absolute http"):
        FoundryResponseStore.from_env()
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT + "/")
    assert FoundryResponseStore.from_env().storage_base_url == STORAGE


# --- hosted server integration ---------------------------------------------


def _hosted(monkeypatch, storage, *, seen=None):
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", REF["name"])
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", REF["version"])
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(
        server.FoundryResponseStore, "from_env", classmethod(lambda cls: _store(storage))
    )
    app = Agent()
    calls = seen if seen is not None else []

    @app.responses()
    async def reply(text: str) -> str:
        calls.append(text)
        return f"echo:{text}"

    return app


def _text(response):
    return response["output"][0]["content"][0]["text"]


def _post(app, payload, headers=CALL):
    async def run():
        async with AgentTestHarness(app) as test:
            return await test.client.post("/responses", json=payload, headers=headers)

    return asyncio.run(run())


def test_hosted_foreground_persists_under_the_gateway_response_id(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    reply = _post(app, {"input": "hi"}, headers={**CALL, "x-agent-response-id": NEW_ID})
    assert reply.status_code == 200
    assert reply.json()["id"] == NEW_ID
    stored = storage.responses[NEW_ID]
    assert stored["status"] == "completed" and _text(stored) == "echo:hi"
    assert stored["agent_reference"] == REF
    assert storage.input_items[NEW_ID][0]["id"].startswith("msg_" + "a" * 16 + "00")
    assert all(c[2]["x-agent-foundry-call-id"] == "call-123" for c in storage.calls)


def test_hosted_foreground_mints_caresp_and_reports_storage_failure(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    reply = _post(app, {"input": "hi"})
    assert FOUNDRY_ID.fullmatch(reply.json()["id"])

    storage.fail[("POST", "responses")] = 500
    failed = _post(app, {"input": "hi"})
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "storage_error"


def test_hosted_store_false_is_not_persisted(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    assert _post(app, {"input": "hi", "store": False}).status_code == 200
    assert storage.calls == []


def test_invalid_response_id_is_rejected_before_dispatch(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted(monkeypatch, storage, seen=seen)
    reply = _post(app, {"input": "hi", "response_id": "resp_bad"})
    assert reply.status_code == 400
    assert reply.json()["error"]["param"] == "response_id"
    assert seen == [] and storage.calls == []


def _events(text):
    return [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: {")
    ]


def test_hosted_stream_registers_before_created_and_updates_at_completion(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    reply = _post(app, {"input": "hi", "stream": True, "response_id": NEW_ID})
    events = _events(reply.text)
    assert events[0]["type"] == "response.created"
    assert events[-1]["type"] == "response.completed"
    create, update = storage.calls
    assert create[3]["response"]["status"] == "in_progress"
    assert update[:2] == ("POST", f"{STORAGE}responses/{NEW_ID}?api-version=v1")
    assert _text(storage.responses[NEW_ID]) == "echo:hi"


def test_hosted_stream_fails_explicitly_when_registration_fails(monkeypatch):
    storage = FakeStorage()
    storage.fail[("POST", "responses")] = 400
    seen = []
    app = _hosted(monkeypatch, storage, seen=seen)
    events = _events(_post(app, {"input": "hi", "stream": True}).text)
    assert [e["type"] for e in events] == ["response.created", "response.failed"]
    assert events[1]["response"]["error"]["code"] == "storage_error"
    assert seen == []


def test_hosted_stream_fails_explicitly_when_final_update_fails(monkeypatch):
    storage = FakeStorage()
    storage.fail[("POST", f"responses/{NEW_ID}")] = 500
    app = _hosted(monkeypatch, storage)
    events = _events(_post(app, {"input": "hi", "stream": True, "response_id": NEW_ID}).text)
    assert events[-1]["type"] == "response.failed"
    assert events[-1]["response"]["error"]["code"] == "storage_error"
    assert "response.completed" not in [e["type"] for e in events]


@pytest.mark.parametrize("update_fails", [False, True])
def test_hosted_stream_handler_error_persists_failure_with_call_id(monkeypatch, update_fails):
    storage = FakeStorage()
    if update_fails:
        storage.fail[("POST", f"responses/{NEW_ID}")] = 500
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", REF["name"])
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", REF["version"])
    monkeypatch.setattr(
        server.FoundryResponseStore, "from_env", classmethod(lambda cls: _store(storage))
    )
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        raise RuntimeError("handler broke")

    events = _events(_post(app, {"input": "hi", "stream": True, "response_id": NEW_ID}).text)
    failed = events[-1]
    assert failed["type"] == "response.failed"
    update = [c for c in storage.calls if c[1].startswith(f"{STORAGE}responses/{NEW_ID}")]
    assert update and update[-1][2]["x-agent-foundry-call-id"] == "call-123"
    assert update[-1][3]["status"] == "failed"
    expected = "storage_error" if update_fails else "server_error"
    assert failed["response"]["error"]["code"] == expected


def test_hosted_reads_go_to_foundry_storage_with_the_call_id(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)

    async def run():
        async with AgentTestHarness(app) as test:
            created = await test.client.post(
                "/responses", json={"input": "hi"}, headers=CALL
            )
            response_id = created.json()["id"]
            storage.calls.clear()
            other_user = {**CALL, "x-agent-user-id": "someone-else"}
            fetched = await test.client.get(f"/responses/{response_id}", headers=other_user)
            items = await test.client.get(
                f"/responses/{response_id}/input_items", headers=CALL
            )
            return response_id, fetched, items

    response_id, fetched, items = asyncio.run(run())
    assert fetched.status_code == 200 and fetched.json()["id"] == response_id
    assert items.json()["data"][0]["content"] == [{"type": "input_text", "text": "hi"}]
    assert all(c[2]["x-agent-foundry-call-id"] == "call-123" for c in storage.calls)


def test_hosted_default_store_runs_background_like_agentserver(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted(monkeypatch, storage, seen=seen)

    async def run():
        async with AgentTestHarness(app) as test:
            queued = await test.client.post(
                "/responses", json={"input": "hi", "background": True}, headers=CALL
            )
            response_id = queued.json()["id"]
            for _ in range(100):
                polled = await test.client.get(f"/responses/{response_id}", headers=CALL)
                if polled.json()["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            return queued, polled

    queued, polled = asyncio.run(run())
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued" and queued.json()["background"] is True
    assert queued.json()["id"].startswith("caresp_")
    assert polled.json()["status"] == "completed" and seen == ["hi"]
    stored = storage.responses[polled.json()["id"]]
    assert stored["background"] is True and stored["agent_reference"]["name"] == REF["name"]
    assert all(c[2]["x-agent-foundry-call-id"] == "call-123" for c in storage.calls)


def test_hosted_background_handler_failure_is_marked_failed(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", REF["name"])
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", REF["version"])
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(
        server.FoundryResponseStore, "from_env", classmethod(lambda cls: _store(storage))
    )
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        raise RuntimeError("boom")

    async def run():
        async with AgentTestHarness(app) as test:
            queued = await test.client.post(
                "/responses", json={"input": "hi", "background": True}, headers=CALL
            )
            for _ in range(100):
                polled = await test.client.get(
                    f"/responses/{queued.json()['id']}", headers=CALL
                )
                if polled.json()["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            return polled

    polled = asyncio.run(run())
    assert polled.json()["status"] == "failed"
    assert polled.json()["error"]["code"] == "server_error"


def test_hosted_default_store_keeps_background_stream_off(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    streamed = _post(app, {"input": "hi", "background": True, "stream": True})
    assert streamed.status_code == 400 and streamed.json()["error"]["param"] == "stream"
    assert storage.calls == []


def _hosted_with_history(monkeypatch, storage, seen):
    _hosted(monkeypatch, storage)
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        seen.append(
            [(m["role"], m["content"][0]["text"]) for m in response_history()]
        )
        return f"echo:{text}"

    return app


def _turns(app, *payloads):
    async def run():
        results = []
        async with AgentTestHarness(app) as test:
            for payload in payloads:
                payload = payload(results) if callable(payload) else payload
                results.append(
                    await test.client.post("/responses", json=payload, headers=CALL)
                )
        return results

    return asyncio.run(run())


def test_hosted_previous_response_id_loads_history_from_storage(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted_with_history(monkeypatch, storage, seen)
    first, second = _turns(
        app,
        {"input": "one"},
        lambda done: {"input": "two", "previous_response_id": done[0].json()["id"]},
    )
    assert first.status_code == second.status_code == 200
    assert seen == [[], [("user", "one"), ("assistant", "echo:one")]]
    first_id, second_id = first.json()["id"], second.json()["id"]
    lookups = [c[1] for c in storage.calls if "history/item_ids" in c[1]]
    # Prefetch for the handler, then again for the create (stock does both).
    assert lookups == [
        (
            f"{STORAGE}history/item_ids?api-version=v1&limit=-1"
            f"&previous_response_id={first_id}"
        )
    ] * 2
    assert storage.history[second_id] == storage._item_ids(first_id)
    assert storage.responses[second_id]["previous_response_id"] == first_id
    assert all(c[2]["x-agent-foundry-call-id"] == "call-123" for c in storage.calls)


def test_hosted_conversation_loads_history_from_storage(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted_with_history(monkeypatch, storage, seen)
    first, second = _turns(
        app,
        {"input": "one", "conversation": "conv_1"},
        {"input": "two", "conversation": {"id": "conv_1"}},
    )
    assert first.status_code == second.status_code == 200
    assert seen == [[], [("user", "one"), ("assistant", "echo:one")]]
    stored = storage.responses[second.json()["id"]]
    assert stored["conversation"] == {"id": "conv_1"}
    # Stock creates conversation turns without history ids.
    assert storage.history[second.json()["id"]] == []


def test_hosted_foundry_history_is_not_recapped(monkeypatch):
    # Storage already applied limit=-1; castia's consumer-store cap must not
    # drop the single Foundry history group when inline input pushes past it.
    monkeypatch.setattr("castia.hosting.server._HISTORY_MAX_ITEMS", 2)
    storage = FakeStorage()
    seen = []
    app = _hosted_with_history(monkeypatch, storage, seen)
    _turns(
        app,
        {"input": "one", "conversation": "conv_1"},
        {"input": "two", "conversation": "conv_1"},
    )
    assert seen[1] == [("user", "one"), ("assistant", "echo:one")]


def test_hosted_unknown_previous_response_is_404_before_the_handler(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted_with_history(monkeypatch, storage, seen)
    missing = _post(app, {"input": "hi", "previous_response_id": NEW_ID})
    assert missing.status_code == 404 and seen == []
    storage.fail[("GET", "history/item_ids")] = 503
    down = _post(app, {"input": "hi", "conversation": "conv_1"})
    assert down.status_code >= 500 and seen == []


def test_hosted_without_project_endpoint_fails_fast(monkeypatch):
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        return text

    async def run():
        async with AgentTestHarness(app):
            pass

    with pytest.raises(ValueError, match="FOUNDRY_PROJECT_ENDPOINT"):
        asyncio.run(run())


def test_explicit_store_keeps_resp_prefix_and_honors_response_id(monkeypatch):
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    records = {}

    class Store:
        async def create(self, record):
            records[record["response"]["id"]] = record

        async def get(self, response_id):
            return records.get(response_id)

        async def update(self, record, *, expected_status):
            return False

        async def delete(self, response_id):
            return records.pop(response_id, None) is not None

    app = Agent(response_store=Store())

    @app.responses()
    async def reply(text: str) -> str:
        return text

    minted = _post(app, {"input": "hi"}).json()["id"]
    assert re.fullmatch(r"resp_[0-9a-f]{16}00[0-9a-f]{32}", minted)
    assert records[minted]["input_items"][0]["id"] == f"{minted}_input_0"
    assert _post(app, {"input": "hi", "response_id": NEW_ID}).json()["id"] == NEW_ID
    assert foundry_storage.foundry_storage_enabled()

    # A same-owner retry may overwrite; another caller cannot take the id.
    assert _post(app, {"input": "again", "response_id": NEW_ID}).status_code == 200
    other = {**CALL, "x-agent-user-id": "user-b"}
    stolen = _post(app, {"input": "mine", "response_id": NEW_ID}, headers=other)
    assert stolen.status_code == 409
    assert stolen.json()["error"]["code"] == "conflict"
    assert records[NEW_ID]["user_id"] == "user-a"
    assert records[NEW_ID]["response"]["output_text"] == "again"


# --- stock agentserver 2.4.0 storage payload contract -----------------------
# Golden writes recorded from the real stock SDK by
# fixtures/capture_stock_foundry_storage.py.

# Valid Response fields castia also writes; stock leaves them unset (stripped).
CASTIA_EXTRAS = {"metadata", "tools", "tool_choice", "usage"}
VOLATILE = {"id", "response_id", "created_at", "completed_at", "agent_session_id"}


def _shape(value):
    """Replace run-specific values (ids, timestamps) with their kind."""
    if isinstance(value, dict):
        return {
            key: (type(item).__name__ if key in VOLATILE else _shape(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return value


def _castia_writes(monkeypatch, request):
    storage = FakeStorage()
    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "production")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    monkeypatch.delenv("FOUNDRY_AGENT_NAME", raising=False)
    monkeypatch.delenv("FOUNDRY_AGENT_VERSION", raising=False)
    monkeypatch.setattr(
        server.FoundryResponseStore, "from_env", classmethod(lambda cls: _store(storage))
    )
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        return "Hi there"

    assert _post(app, request).status_code == 200
    return [call[3] for call in storage.calls]


def _assert_stock_response(castia, stock):
    castia = dict(castia)
    assert castia.pop("parallel_tool_calls") is True  # castia's default; stock: False
    stock = {k: v for k, v in stock.items() if k != "parallel_tool_calls"}
    assert set(castia) - set(stock) <= CASTIA_EXTRAS | {"background"}
    castia = {k: v for k, v in castia.items() if k not in CASTIA_EXTRAS}
    stock.setdefault("background", False)
    assert _shape(castia) == _shape(stock)
    assert castia["response_id"] == castia["id"]
    for item in castia["output"]:
        assert item["response_id"] == castia["id"]
    assert re.fullmatch(r"[0-9a-f]{63}", castia["agent_session_id"])


def test_stream_storage_writes_match_stock_agentserver(monkeypatch):
    case = STOCK["cases"]["stream"]
    create, update = _castia_writes(monkeypatch, case["request"])
    stock_create, stock_update = (write["body"] for write in case["writes"])
    assert set(create) == set(stock_create) == {"response", "input_items", "history_item_ids"}
    assert _shape(create["input_items"]) == _shape(stock_create["input_items"])
    assert create["history_item_ids"] == stock_create["history_item_ids"]
    _assert_stock_response(create["response"], stock_create["response"])
    _assert_stock_response(update, stock_update)
    assert update["agent_session_id"] == create["response"]["agent_session_id"]


def test_sync_storage_write_matches_stock_final_snapshot(monkeypatch):
    # Stock writes create(in_progress) then update(completed); castia writes the
    # completed snapshot in one create. Both must leave the same stored response.
    case = STOCK["cases"]["sync"]
    (create,) = _castia_writes(monkeypatch, case["request"])
    stock_create, stock_update = (write["body"] for write in case["writes"])
    assert set(create) == set(stock_create)
    assert _shape(create["input_items"]) == _shape(stock_create["input_items"])
    _assert_stock_response(create["response"], stock_update)


def test_hosted_without_agent_reference_is_rejected_before_the_handler(monkeypatch):
    storage = FakeStorage()
    seen = []
    app = _hosted(monkeypatch, storage, seen=seen)
    monkeypatch.delenv("FOUNDRY_AGENT_VERSION")
    for payload in (
        {"input": "hi"},
        {"input": "hi", "agent_reference": {"type": "agent_reference", "name": "other"}},
    ):
        reply = _post(app, payload)
        assert reply.status_code == 400
        assert reply.json()["error"]["param"] == "agent_reference"
    assert seen == [] and storage.calls == []
    assert _post(app, {"input": "hi", "store": False}).status_code == 200


@pytest.mark.parametrize(
    "reference",
    ["contract-policy-analyst", {"type": "agent", "name": "a"}, {"name": "  "}],
)
def test_malformed_agent_reference_is_rejected(monkeypatch, reference):
    storage = FakeStorage()
    seen = []
    app = _hosted(monkeypatch, storage, seen=seen)
    reply = _post(app, {"input": "hi", "agent_reference": reference})
    assert reply.status_code == 400
    assert reply.json()["error"]["param"].startswith("agent_reference")
    assert seen == [] and storage.calls == []


def test_request_agent_reference_wins_over_the_hosted_identity(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    other = {"type": "agent_reference", "name": "other", "version": "3"}
    reply = _post(app, {"input": "hi", "agent_reference": other, "response_id": NEW_ID})
    assert reply.json()["agent_reference"] == other
    assert storage.responses[NEW_ID]["agent_reference"] == other


def test_agent_reference_resolution_mirrors_stock_with_env_fallback():
    env = {"FOUNDRY_AGENT_NAME": "agent", "FOUNDRY_AGENT_VERSION": "7"}
    assert resolve_agent_reference(None, env={}) is None
    assert resolve_agent_reference(None, env=env) == {
        "type": "agent_reference", "name": "agent", "version": "7"
    }
    assert resolve_agent_reference({"name": " agent "}, env=env)["version"] == "7"
    assert resolve_agent_reference({"name": "other"}, env=env) == {
        "type": "agent_reference", "name": "other"
    }
    with pytest.raises(InvalidAgentReferenceError) as error:
        resolve_agent_reference({"type": "agent_reference"}, env=env)
    assert error.value.param == "agent_reference.name"


def test_agent_session_id_matches_stock_derivation():
    # Expected values printed by stock 2.4.0 hosting._request_parsing.derive_session_id.
    conv = "conv_" + "a" * 16 + "00" + "b" * 32
    prev = "resp_" + "c" * 32 + "d" * 16
    resolve = resolve_agent_session_id
    assert resolve({}, agent_reference=REF, previous_response_id=None,
                   conversation_id=conv, env={}) == (
        "45d276feb7d7af0a6cdc13bddab2271577e3a63c70a29fe8fe175c30508c47a"
    )
    assert resolve({}, agent_reference=REF, previous_response_id=prev,
                   conversation_id=None, env={}) == (
        "0fe108d12f05877b94580adaae22283d187a4c44e3a787a80a43672be38805a"
    )
    assert resolve({}, agent_reference=None, previous_response_id=None,
                   conversation_id="conv-plain", env={}) == (
        "0c0762f4f87291b9de94f66f7b2d27d03d10a3eaa2651a234abf94398d43ff6"
    )
    assert resolve({"agent_session_id": "s1"}, agent_reference=REF,
                   previous_response_id=None, conversation_id=None,
                   env={"FOUNDRY_AGENT_SESSION_ID": "s2"}) == "s1"
    assert resolve({}, agent_reference=REF, previous_response_id=None,
                   conversation_id=None, env={"FOUNDRY_AGENT_SESSION_ID": "s2"}) == "s2"


def test_storage_snapshot_refuses_incomplete_identity_before_any_request():
    storage = FakeStorage()
    store = _store(storage)
    record = _record()
    record["response"]["agent_reference"] = {"type": "agent_reference", "name": "a"}
    with pytest.raises(FoundryStorageError, match="agent_reference") as error:
        asyncio.run(store.create(record))
    assert error.value.code == "invalid_request"
    with pytest.raises(FoundryStorageError):
        asyncio.run(store.replace(record["response"]))
    assert storage.calls == []


def test_storage_snapshot_strips_nulls_and_stamps_output_items():
    snapshot = foundry_storage.storage_snapshot(
        {
            "id": NEW_ID,
            "status": "completed",
            "agent_reference": REF,
            "error": None,
            "output_text": "hi",
            "output": [{"type": "message", "id": "msg_1", "response_id": None}],
        }
    )
    assert "error" not in snapshot and "output_text" not in snapshot
    assert snapshot["output"][0] == {
        "type": "message", "id": "msg_1", "response_id": NEW_ID, "agent_reference": REF
    }
    assert snapshot["response_id"] == NEW_ID and snapshot["background"] is False
    assert isinstance(snapshot["completed_at"], int)


def test_stream_output_item_events_carry_identity_like_stock(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    events = _events(_post(app, {"input": "hi", "stream": True, "response_id": NEW_ID}).text)
    items = [e["item"] for e in events if e["type"].startswith("response.output_item.")]
    assert len(items) == 2
    for item in items:
        assert item["response_id"] == NEW_ID and item["agent_reference"] == REF


def test_agent_session_id_is_resolved_without_persistence(monkeypatch):
    storage = FakeStorage()
    app = _hosted(monkeypatch, storage)
    payload = {"input": "hi", "store": False, "agent_session_id": "requested-session"}
    reply = _post(app, payload)
    assert reply.json()["agent_session_id"] == "requested-session"
    assert reply.json()["agent_reference"] == REF
    assert storage.calls == []


def _call(app, method, path, **kwargs):
    async def run():
        async with AgentTestHarness(app) as test:
            return await test.client.request(method, path, headers=CALL, **kwargs)

    return asyncio.run(run())


SESSION_HEADER = "x-agent-session-id"


def test_post_reports_resolved_session_header_like_stock(monkeypatch):
    app = _hosted(monkeypatch, FakeStorage())
    reply = _post(app, {"input": "hi", "conversation": "conv-plain"})
    assert reply.headers[SESSION_HEADER] == reply.json()["agent_session_id"]
    stream = _post(app, {"input": "hi", "stream": True, "agent_session_id": "s-1"})
    assert stream.headers[SESSION_HEADER] == "s-1"


def test_session_header_prefers_payload_then_platform_env(monkeypatch):
    app = _hosted(monkeypatch, FakeStorage())
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "platform-session")
    assert _post(app, {"input": "hi"}).headers[SESSION_HEADER] == "platform-session"
    reply = _post(app, {"input": "hi", "agent_session_id": "requested"})
    assert reply.headers[SESSION_HEADER] == "requested"


def test_non_post_and_error_responses_report_platform_session(monkeypatch):
    app = _hosted(monkeypatch, FakeStorage())
    assert SESSION_HEADER not in _call(app, "GET", f"/responses/{NEW_ID}").headers
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "platform-session")
    assert _call(app, "GET", f"/responses/{NEW_ID}").headers[SESSION_HEADER] == (
        "platform-session"
    )
    error = _call(app, "POST", "/responses", content=b"not json")
    assert error.status_code == 400
    assert error.headers[SESSION_HEADER] == "platform-session"
    assert SESSION_HEADER not in _call(app, "GET", "/readiness").headers


def test_unencodable_session_is_not_emitted_as_a_header(monkeypatch):
    app = _hosted(monkeypatch, FakeStorage())
    reply = _post(app, {"input": "hi", "store": False, "agent_session_id": "sess-\u2603"})
    assert reply.status_code == 200
    assert reply.json()["agent_session_id"] == "sess-\u2603"
    assert SESSION_HEADER not in reply.headers


def test_local_post_reports_generated_session_header_only(monkeypatch):
    monkeypatch.delenv("FOUNDRY_AGENT_SESSION_ID", raising=False)
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        return text

    reply_ = _post(app, {"input": "hi"})
    assert re.fullmatch(r"[0-9a-f]{63}", reply_.headers[SESSION_HEADER])
    assert "agent_session_id" not in reply_.json()


def test_invalid_response_id_reports_platform_not_requested_session(monkeypatch):
    app = _hosted(monkeypatch, FakeStorage())
    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "platform")
    error = _post(app, {"input": "hi", "agent_session_id": "requested", "response_id": "bad"})
    assert error.status_code == 400
    assert error.headers[SESSION_HEADER] == "platform"


def test_session_header_matches_route_path_under_root_path(monkeypatch):
    from castia.hosting.agent_identity import SessionHeaderMiddleware

    monkeypatch.setenv("FOUNDRY_AGENT_SESSION_ID", "platform")

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    def headers_for(path, root_path):
        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "path": path, "root_path": root_path}
        asyncio.run(SessionHeaderMiddleware(inner)(scope, None, send))
        return dict(sent[0]["headers"])

    assert headers_for("/api/responses/x", "/api")[b"x-agent-session-id"] == b"platform"
    assert b"x-agent-session-id" not in headers_for("/api/readiness", "/api")

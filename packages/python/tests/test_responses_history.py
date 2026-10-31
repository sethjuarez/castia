"""Responses conversation history through a consumer-owned store."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from castia import Agent, Depends, response_history
from castia.building import AgentTestHarness
from castia.hosting import server
from castia.inference.model import _conversation_input
from castia.runtime import dependencies
from castia.runtime.history import model_input_items

USER = {"x-agent-user-id": "user-a"}
OTHER = {"x-agent-user-id": "user-b"}


class RecordStore:
    """TEST ONLY - not for production: process-local and unbounded."""

    def __init__(self):
        self.records = {}
        self.fail_get = False

    async def create(self, record):
        self.records[record["response"]["id"]] = deepcopy(record)

    async def get(self, response_id):
        if self.fail_get:
            raise ConnectionError("store down")
        record = self.records.get(response_id)
        return deepcopy(record) if record is not None else None

    async def update(self, record, *, expected_status):
        current = self.records.get(record["response"]["id"])
        if current is None or current["response"]["status"] != expected_status:
            return False
        self.records[record["response"]["id"]] = deepcopy(record)
        return True

    async def delete(self, response_id):
        return self.records.pop(response_id, None) is not None


class ConversationStore(RecordStore):
    """TEST ONLY: indexes records by ``response["conversation"]["id"]``."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def get_history(self, *, previous_response_id, conversation_id, user_id, limit):
        self.calls.append((previous_response_id, conversation_id, user_id, limit))
        groups = []
        for record in self.records.values():
            response = record["response"]
            if record.get("user_id") != user_id:
                continue
            if conversation_id is not None and (
                (response.get("conversation") or {}).get("id") != conversation_id
            ):
                continue
            if previous_response_id is not None and response["id"] != previous_response_id:
                continue
            groups.append(record["input_items"] + response["output"])
        return groups


def _texts(history):
    return [(item["role"], item["content"][0]["text"]) for item in history]


def _echo_app(store, seen):
    app = Agent(response_store=store)

    @app.responses()
    async def reply(text: str, history: list = Depends(response_history)):
        seen.append(history)
        return f"echo:{text}"

    return app


def test_previous_response_id_chains_history_through_the_store():
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            first = (
                await test.client.post("/responses", json={"input": "one"}, headers=USER)
            ).json()
            assert first["previous_response_id"] is None
            second = (
                await test.client.post(
                    "/responses",
                    json={"input": "two", "previous_response_id": first["id"]},
                    headers=USER,
                )
            ).json()
            assert second["previous_response_id"] == first["id"]
            await test.client.post(
                "/responses",
                json={"input": "three", "previous_response_id": second["id"]},
                headers=USER,
            )

    asyncio.run(run())
    assert seen[0] == []
    assert _texts(seen[1]) == [("user", "one"), ("assistant", "echo:one")]
    assert _texts(seen[2]) == [
        ("user", "one"),
        ("assistant", "echo:one"),
        ("user", "two"),
        ("assistant", "echo:two"),
    ]
    assert all("id" not in item and "status" not in item for item in seen[2])


@pytest.mark.parametrize("headers", [OTHER, {}])
def test_previous_response_id_of_another_user_is_not_found(headers):
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            first = (
                await test.client.post("/responses", json={"input": "secret"}, headers=USER)
            ).json()
            for background in (False, True):
                response = await test.client.post(
                    "/responses",
                    json={
                        "input": "peek",
                        "previous_response_id": first["id"],
                        "background": background,
                    },
                    headers=headers,
                )
                assert response.status_code == 404
                assert response.json()["error"]["param"] == "response_id"

    asyncio.run(run())
    assert len(seen) == 1


def test_anonymous_history_is_only_visible_to_anonymous_callers():
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            anonymous = (await test.client.post("/responses", json={"input": "a"})).json()
            chained = await test.client.post(
                "/responses", json={"input": "b", "previous_response_id": anonymous["id"]}
            )
            assert chained.status_code == 200
            foreign = await test.client.post(
                "/responses",
                json={"input": "c", "previous_response_id": anonymous["id"]},
                headers=USER,
            )
            assert foreign.status_code == 404

    asyncio.run(run())
    assert _texts(seen[1]) == [("user", "a"), ("assistant", "echo:a")]


def test_chain_walk_stops_at_deleted_links_cycles_and_foreign_records():
    store = RecordStore()

    def record(response_id, previous, text, user="user-a"):
        store.records[response_id] = {
            "response": {
                "id": response_id,
                "status": "completed",
                "previous_response_id": previous,
                "output": [
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": f"re:{text}"}
                    ]}
                ],
            },
            "input_items": [{"type": "message", "role": "user", "content": text}],
            "background": False,
            "user_id": user,
        }

    record("resp_a", "resp_missing", "a")
    record("resp_b", "resp_a", "b")
    record("resp_x", "resp_y", "x")
    record("resp_y", "resp_x", "y")
    record("resp_f", None, "f", user="user-b")
    record("resp_g", "resp_f", "g")

    async def walk(anchor):
        groups, truncated = await server._stored_history(
            store, previous_response_id=anchor, conversation_id=None, owner="user-a"
        )
        return [item for group in groups for item in group], truncated

    items, truncated = asyncio.run(walk("resp_b"))
    assert truncated is True
    assert [item["content"] for item in items][::2] == ["a", "b"]
    items, truncated = asyncio.run(walk("resp_x"))
    assert truncated is True
    assert len(items) == 4
    items, truncated = asyncio.run(walk("resp_g"))
    assert truncated is True
    assert [item["content"] for item in items][::2] == ["g"]


def test_history_cap_drops_the_oldest_whole_responses(monkeypatch):
    monkeypatch.setattr(server, "_HISTORY_MAX_ITEMS", 5)
    groups = [
        [{"role": "user", "content": f"q{n}"}, {"role": "assistant", "content": f"a{n}"}]
        for n in range(3)
    ]
    items, truncated = server._capped_history([*groups, []])
    assert truncated is True
    assert [item["content"][0]["text"] for item in items] == ["q1", "a1", "q2", "a2"]


def test_tool_call_pairs_survive_across_stored_responses():
    groups = [
        [
            {"role": "user", "content": "weather?"},
            {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
        ],
        [
            {"type": "function_call_output", "call_id": "c1", "output": "sunny"},
            {"role": "assistant", "content": "sunny"},
        ],
    ]
    items, truncated = server._capped_history(groups)
    assert truncated is False
    assert [item.get("type", "message") for item in items] == [
        "message",
        "function_call",
        "function_call_output",
        "message",
    ]


def test_tool_outputs_must_follow_their_calls():
    items = model_input_items(
        [
            {"type": "function_call_output", "call_id": "early", "output": "x"},
            {"type": "function_call", "call_id": "early", "name": "f"},
            {"type": "function_call", "call_id": "dup", "name": "f", "arguments": "1"},
            {"type": "function_call", "call_id": "dup", "name": "f", "arguments": "2"},
            {"type": "function_call_output", "call_id": "dup", "output": "y"},
            {"type": "function_call_output", "call_id": "dup", "output": "z"},
        ]
    )
    assert items == [
        {"type": "function_call", "call_id": "dup", "name": "f", "arguments": "2"},
        {"type": "function_call_output", "call_id": "dup", "output": "y"},
    ]


@pytest.mark.parametrize(
    ("value", "text", "history_len"),
    [
        ([{"role": "user", "content": "old"}, "new"], "new", 1),
        ([{"role": "user", "content": "q"}, {"role": "user", "content": ""}], "q", 0),
        ([{"role": "assistant", "content": "hi"}, {"content": "roleless"}], "roleless", 1),
        ([{"role": "assistant", "content": "only"}], "only", 0),
    ],
)
def test_current_message_and_inline_history_agree(value, text, history_len):
    assert server._responses_input(value) == text
    assert len(server._inline_history(value)) == history_len


def test_history_store_must_return_per_response_groups():
    class FlatStore(RecordStore):
        async def get_history(self, **_):
            return [{"role": "user", "content": "flat"}]

    with pytest.raises(TypeError, match="per-response"):
        asyncio.run(
            server._stored_history(
                FlatStore(), previous_response_id=None, conversation_id="c", owner=None
            )
        )


def test_inline_input_turns_become_history():
    seen = []
    app = Agent()

    @app.responses()
    async def reply(text: str, history: list = Depends(response_history)):
        seen.append((text, history))
        return "ok"

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={
                    "input": [
                        {"role": "system", "content": "be brief"},
                        {"role": "user", "content": "first"},
                        {"role": "assistant", "content": [
                            {"type": "output_text", "text": "reply", "annotations": []}
                        ]},
                        {"type": "function_call", "call_id": "c1", "name": "f",
                         "arguments": "{}"},
                        {"type": "function_call_output", "call_id": "c1", "output": "42"},
                        {"type": "function_call", "call_id": "orphan", "name": "f"},
                        {"role": "user", "content": [{"type": "input_text", "text": "now"}]},
                    ]
                },
            )
            assert response.status_code == 200
            # A second turn with no history must not see the first turn's items.
            await test.client.post("/responses", json={"input": "alone"})

    asyncio.run(run())
    text, history = seen[0]
    assert text == "now"
    assert history == [
        {"role": "system", "content": [{"type": "input_text", "text": "be brief"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "first"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "reply"}]},
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "42"},
    ]
    assert seen[1] == ("alone", [])


def test_streaming_and_background_turns_see_history():
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            first = (
                await test.client.post("/responses", json={"input": "one"}, headers=USER)
            ).json()
            streamed = await test.client.post(
                "/responses",
                json={"input": "two", "stream": True, "previous_response_id": first["id"]},
                headers=USER,
            )
            assert streamed.status_code == 200
            assert f'"previous_response_id":"{first["id"]}"' in streamed.text
            queued = (
                await test.client.post(
                    "/responses",
                    json={
                        "input": "three",
                        "background": True,
                        "previous_response_id": first["id"],
                    },
                    headers=USER,
                )
            ).json()
            assert queued["previous_response_id"] == first["id"]
            for _ in range(200):
                body = (
                    await test.client.get(f"/responses/{queued['id']}", headers=USER)
                ).json()
                if body["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert body["status"] == "completed"
            assert body["previous_response_id"] == first["id"]

    asyncio.run(run())
    expected = [("user", "one"), ("assistant", "echo:one")]
    assert _texts(seen[1]) == expected
    assert _texts(seen[2]) == expected


def test_store_false_reads_history_without_writing():
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            first = (
                await test.client.post("/responses", json={"input": "one"}, headers=USER)
            ).json()
            count = len(store.records)
            response = await test.client.post(
                "/responses",
                json={"input": "two", "store": False, "previous_response_id": first["id"]},
                headers=USER,
            )
            assert response.status_code == 200
            assert len(store.records) == count

    asyncio.run(run())
    assert _texts(seen[1]) == [("user", "one"), ("assistant", "echo:one")]


def test_unfinished_previous_response_and_store_errors_are_explicit():
    store = RecordStore()
    store.records["resp_q"] = {
        "response": {"id": "resp_q", "status": "queued", "output": []},
        "input_items": [],
        "background": True,
        "user_id": "user-a",
    }
    app = _echo_app(store, [])

    async def run():
        async with AgentTestHarness(app) as test:
            queued = await test.client.post(
                "/responses",
                json={"input": "x", "previous_response_id": "resp_q"},
                headers=USER,
            )
            assert queued.status_code == 400
            assert "still queued" in queued.json()["error"]["message"]
            store.fail_get = True
            down = await test.client.post(
                "/responses",
                json={"input": "x", "previous_response_id": "resp_q"},
                headers=USER,
            )
            assert down.status_code == 503

    asyncio.run(run())


def test_both_previous_response_id_and_conversation_is_rejected():
    app = _echo_app(RecordStore(), [])

    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "x", "previous_response_id": "resp_a", "conversation": "c"},
            )
            assert response.status_code == 400
            assert response.json()["error"]["param"] == "conversation"

    asyncio.run(run())


def test_conversation_history_uses_the_store_capability():
    store = ConversationStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            for text in ("one", "two"):
                body = (
                    await test.client.post(
                        "/responses",
                        json={"input": text, "conversation": {"id": "conv_1"}},
                        headers=USER,
                    )
                ).json()
                assert body["conversation"] == {"id": "conv_1"}
            other = await test.client.post(
                "/responses",
                json={"input": "x", "conversation": "conv_1"},
                headers=OTHER,
            )
            assert other.status_code == 200

    asyncio.run(run())
    assert seen[0] == []
    assert _texts(seen[1]) == [("user", "one"), ("assistant", "echo:one")]
    assert seen[2] == []
    assert store.calls[0] == (None, "conv_1", "user-a", server._HISTORY_MAX_ITEMS)


def test_conversation_without_the_capability_is_ignored():
    store = RecordStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            for text in ("one", "two"):
                response = await test.client.post(
                    "/responses", json={"input": text, "conversation": "conv_1"}
                )
                assert response.status_code == 200

    asyncio.run(run())
    assert seen == [[], []]


def test_previous_response_id_prefers_the_store_capability():
    store = ConversationStore()
    seen = []
    app = _echo_app(store, seen)

    async def run():
        async with AgentTestHarness(app) as test:
            first = (
                await test.client.post("/responses", json={"input": "one"}, headers=USER)
            ).json()
            await test.client.post(
                "/responses",
                json={"input": "two", "previous_response_id": first["id"]},
                headers=USER,
            )

    asyncio.run(run())
    assert len(store.calls) == 1
    assert store.calls[0][0].startswith("resp_")
    assert store.calls[0][1:] == (None, "user-a", server._HISTORY_MAX_ITEMS)
    assert _texts(seen[1]) == [("user", "one"), ("assistant", "echo:one")]


def test_use_cache_false_and_per_turn_dependencies_are_not_cached():
    calls = []

    def provider():
        calls.append(1)
        return len(calls)

    async def run():
        previous = dict(dependencies._CACHE)
        try:
            first = await dependencies.resolve(provider, None, use_cache=False)
            second = await dependencies.resolve(provider, None, use_cache=False)
            assert (first, second) == (1, 2)
            assert provider not in dependencies._CACHE
            assert await dependencies.resolve(response_history, None) == []
            assert response_history not in dependencies._CACHE
        finally:
            dependencies._CACHE.clear()
            dependencies._CACHE.update(previous)

    asyncio.run(run())


def test_response_history_returns_a_copy():
    from castia.runtime.history import bind_response_history

    with bind_response_history([{"role": "user", "content": "a"}]):
        copy = response_history()
        copy[0]["content"] = "changed"
        assert response_history() == [{"role": "user", "content": "a"}]
    assert response_history() == []


def test_model_input_puts_history_before_the_user_message():
    history = model_input_items([{"role": "user", "content": "a"}])
    assert _conversation_input("b", history) == [
        {"role": "user", "content": [{"type": "input_text", "text": "a"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "b"}]},
    ]
    assert _conversation_input("b", None) == [
        {"role": "user", "content": [{"type": "input_text", "text": "b"}]}
    ]

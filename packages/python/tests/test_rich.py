"""Unit tests for the rich Activity toolset.

Every connector verb (typing, reactions, message update/delete, streamed reply)
and the payload builders are exercised against the connector's single HTTP choke
point ``connector._send``, monkeypatched to a recorder -- so we assert the exact
method, URL and body that would hit the Bot Framework without any network, auth
chain, or extra test dependency. Async code is driven with ``asyncio.run`` since
the project deliberately ships no ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from castia import action_chips, adaptive_card, suggested_actions
from castia.activity import Activity, ChannelAccount, ConversationAccount
from castia.cards import ADAPTIVE_CARD_CONTENT_TYPE, Reaction

SERVICE_URL = "https://smba.trafficmanager.net/teams"
CONVERSATION_ID = "19:meeting_abc@thread.v2"
ACTIVITY_ID = "1700000000000"


def _activity() -> Activity:
    return Activity(
        type="message",
        id=ACTIVITY_ID,
        text="hello",
        service_url=SERVICE_URL,
        conversation=ConversationAccount(id=CONVERSATION_ID),
        from_property=ChannelAccount(id="29:user", name="User"),
        recipient=ChannelAccount(id="28:agent", name="HAL"),
    )


class _FakeResponse:
    def __init__(self, status_code: int = 201, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"id": "created-id"}
        self.text = ""

    def json(self) -> dict:
        return self._body


@pytest.fixture
def calls(monkeypatch):
    """Capture every connector request and return a canned created id."""
    from castia import connector

    recorded: list[dict] = []

    async def fake_send(activity, method, url, *, json=None, headers=None):
        recorded.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return _FakeResponse()

    monkeypatch.setattr(connector, "_send", fake_send)
    return recorded


# -- connector verbs --------------------------------------------------------


def test_send_reply_shape(calls):
    from castia import connector

    created = asyncio.run(connector.send_reply(_activity(), "hi there"))

    assert created == "created-id"
    (call,) = calls
    assert call["method"] == "POST"
    assert call["url"] == (
        f"{SERVICE_URL}/v3/conversations/{CONVERSATION_ID}/activities"
    )
    body = call["json"]
    assert body["type"] == "message"
    assert body["text"] == "hi there"
    # Threaded under the inbound turn, with from/recipient swapped.
    assert body["replyToId"] == ACTIVITY_ID
    assert body["from"]["id"] == "28:agent"
    assert body["recipient"]["id"] == "29:user"
    assert body["conversation"]["id"] == CONVERSATION_ID


def test_send_reply_empty_is_noop(calls):
    from castia import connector

    assert asyncio.run(connector.send_reply(_activity(), "")) is None
    assert calls == []


def test_send_typing_shape(calls):
    from castia import connector

    asyncio.run(connector.send_typing(_activity()))

    (call,) = calls
    assert call["method"] == "POST"
    assert call["url"].endswith(f"/conversations/{CONVERSATION_ID}/activities")
    assert call["json"]["type"] == "typing"
    assert "text" not in call["json"]


def test_add_reaction_is_put_with_no_body(calls):
    from castia import connector

    ok = asyncio.run(connector.add_reaction(_activity(), Reaction.like))

    assert ok is True
    (call,) = calls
    assert call["method"] == "PUT"
    assert call["url"] == (
        f"{SERVICE_URL}/v3/conversations/{CONVERSATION_ID}"
        f"/activities/{ACTIVITY_ID}/reactions/like"
    )
    assert call["json"] is None


def test_remove_reaction_is_delete(calls):
    from castia import connector

    ok = asyncio.run(connector.remove_reaction(_activity(), Reaction.heart))

    assert ok is True
    (call,) = calls
    assert call["method"] == "DELETE"
    assert call["url"].endswith(f"/activities/{ACTIVITY_ID}/reactions/heart")


def test_reaction_type_is_url_encoded(calls):
    from castia import connector

    asyncio.run(connector.add_reaction(_activity(), Reaction.check))

    (call,) = calls
    # '2705_whiteheavycheckmark' has no unsafe chars, but the id is passed
    # through quote(); an id with a space would be percent-encoded.
    assert call["url"].endswith("/reactions/2705_whiteheavycheckmark")


def test_update_activity_is_put_to_activity_id(calls):
    from castia import connector

    ok = asyncio.run(
        connector.update_activity(_activity(), "edit-me", {"type": "message", "text": "v2"})
    )

    assert ok is True
    (call,) = calls
    assert call["method"] == "PUT"
    assert call["url"].endswith("/activities/edit-me")
    assert call["json"]["id"] == "edit-me"
    assert call["json"]["text"] == "v2"


def test_delete_activity_is_delete(calls):
    from castia import connector

    ok = asyncio.run(connector.delete_activity(_activity(), "drop-me"))

    assert ok is True
    (call,) = calls
    assert call["method"] == "DELETE"
    assert call["url"].endswith("/activities/drop-me")
    assert call["json"] is None


def test_no_service_url_skips_call(calls):
    from castia import connector

    bare = Activity(type="message", id="x", conversation=ConversationAccount(id="c"))
    # No service_url -> nothing to call.
    assert asyncio.run(connector.send_typing(bare)) is None
    assert calls == []


# -- streaming --------------------------------------------------------------


@pytest.fixture
def stream_calls(monkeypatch, calls):
    """As ``calls``, but also stub the once-minted auth header the streamer takes."""
    from castia import connector

    async def fake_auth(activity):
        return {"Authorization": "test"}

    monkeypatch.setattr(connector, "authorization", fake_auth)
    return calls


def test_streamer_lifecycle(stream_calls):
    from castia.streaming import Streamer

    async def run():
        s = Streamer(_activity(), min_interval=0)
        await s.update("Thinking...")
        await s.append("Hello")
        await s.append(" world")
        return await s.finish(
            suggestions=suggested_actions("Thanks!", "More"),
        )

    final_id = asyncio.run(run())

    # informative + 2 streaming + final.
    assert len(stream_calls) == 4
    kinds = [c["json"]["channelData"]["streamType"] for c in stream_calls]
    assert kinds == ["informative", "streaming", "streaming", "final"]

    # streamSequence is 1-based and monotonic across the interim (typing) chunks,
    # and is OMITTED from the final message -- per the Teams streaming contract,
    # a final message that carries a sequence never resolves (the bubble stays
    # stuck rendering as "streaming").
    interim = stream_calls[:-1]
    seqs = [c["json"]["channelData"]["streamSequence"] for c in interim]
    assert seqs == [1, 2, 3]
    final = stream_calls[-1]["json"]
    assert "streamSequence" not in final["channelData"]
    assert "streamSequence" not in final["entities"][0]

    # Every chunk carries a streamInfo entity; the auth header is reused.
    for call in stream_calls:
        (entity,) = call["json"]["entities"]
        assert entity["type"] == "streamInfo"
        assert call["headers"] == {"Authorization": "test"}

    # First chunk has no streamId; later chunks reuse the captured id.
    assert "streamId" not in stream_calls[0]["json"]["channelData"]
    assert stream_calls[1]["json"]["channelData"]["streamId"] == "created-id"

    # Interim chunks are typing; the final is a real message carrying the
    # accumulated text, the streamId (via streamInfo, not an activity id) and
    # the suggested actions.
    assert stream_calls[0]["json"]["type"] == "typing"
    assert final["type"] == "message"
    assert "id" not in final
    assert final["entities"][0]["streamId"] == "created-id"
    assert final["text"] == "Hello world"
    assert final["channelData"]["streamType"] == "final"
    assert final["suggestedActions"]["actions"][0]["title"] == "Thanks!"
    assert final_id == "created-id"


def test_streamer_update_ignored_after_text(stream_calls):
    from castia.streaming import Streamer

    async def run():
        s = Streamer(_activity(), min_interval=0)
        await s.append("body")
        await s.update("too late")  # no-op once text exists
        await s.finish()

    asyncio.run(run())
    kinds = [c["json"]["channelData"]["streamType"] for c in stream_calls]
    assert kinds == ["streaming", "final"]


# -- payload builders -------------------------------------------------------


def test_adaptive_card_shape():
    card = adaptive_card(
        [{"type": "TextBlock", "text": "hi"}],
        actions=[{"type": "Action.OpenUrl", "url": "https://x"}],
    )
    assert card["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
    content = card["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.5"
    assert content["body"][0]["text"] == "hi"
    assert content["actions"][0]["type"] == "Action.OpenUrl"


def test_suggested_actions_from_strings_and_dicts():
    block = suggested_actions("Yes", {"title": "Show diff", "value": "diff"})
    yes, diff = block["actions"]
    assert yes == {"type": "imBack", "title": "Yes", "value": "Yes"}
    assert diff == {"type": "imBack", "title": "Show diff", "value": "diff"}
    assert block["to"] == []


def test_action_chips_is_adaptive_card_with_imback_submits():
    card = action_chips("Thanks!", {"title": "Show diff", "value": "diff"}, prompt="Next?")
    assert card["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
    content = card["content"]
    assert content["type"] == "AdaptiveCard"
    # The prompt renders as the card's only body element.
    assert content["body"][0]["text"] == "Next?"

    thanks, diff = content["actions"]
    assert thanks["type"] == "Action.Submit"
    assert thanks["title"] == "Thanks!"
    # A bare string chip posts itself back as the user's next message. imBack
    # (not messageBack) delivers a single visible message -- messageBack
    # double-fires in Teams 1:1 chats.
    assert thanks["data"]["msteams"] == {"type": "imBack", "value": "Thanks!"}
    assert thanks["data"]["choice"] == "Thanks!"
    # A dict chip keeps title/value distinct: button label vs. text posted back.
    assert diff["title"] == "Show diff"
    assert diff["data"]["msteams"] == {"type": "imBack", "value": "diff"}


def test_action_chips_without_prompt_has_empty_body():
    card = action_chips("Only button")
    assert card["content"]["body"] == []
    assert card["content"]["actions"][0]["type"] == "Action.Submit"


# -- reaction tool ----------------------------------------------------------


def test_react_tool_adds_reaction(monkeypatch):
    from castia import connector
    from castia.tools import _react_to_message_impl

    seen = {}

    async def fake_add(activity, reaction_type="like"):
        seen["add"] = reaction_type
        return True

    monkeypatch.setattr(connector, "add_reaction", fake_add)

    result = asyncio.run(_react_to_message_impl(_activity(), reaction="heart"))
    assert result == {"ok": True, "reaction": "heart", "removed": False}
    assert seen["add"] == "heart"


def test_react_tool_removes_reaction(monkeypatch):
    from castia import connector
    from castia.tools import _react_to_message_impl

    async def fake_remove(activity, reaction_type="like"):
        return True

    monkeypatch.setattr(connector, "remove_reaction", fake_remove)

    result = asyncio.run(
        _react_to_message_impl(_activity(), reaction="like", remove=True)
    )
    assert result == {"ok": True, "reaction": "like", "removed": True}


def test_react_tool_is_registered():
    from castia.tools import agent_tools

    names = {t.name for t in agent_tools()}
    assert "react_to_message" in names


# -- model streaming --------------------------------------------------------


def test_model_stream_yields_text_deltas():
    from castia.model import Model

    class _Event:
        def __init__(self, type_, delta=""):
            self.type = type_
            self.delta = delta

    async def _events():
        yield _Event("response.created")
        yield _Event("response.output_text.delta", "Hel")
        yield _Event("response.output_text.delta", "lo")
        yield _Event("response.output_text.delta", "")  # empty deltas skipped
        yield _Event("response.completed")

    class _Responses:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return _events()

    class _OpenAI:
        responses = _Responses()

    class _Client:
        def get_openai_client(self):
            return _OpenAI()

    model = Model.__new__(Model)
    model._client = _Client()
    model._deployment = "gpt-4o"
    model._instructions = None
    model._reasoning = {}

    async def run():
        return [d async for d in model.stream("hi")]

    assert asyncio.run(run()) == ["Hel", "lo"]


# -- import cheapness -------------------------------------------------------


def test_importing_castia_stays_cheap():
    # `import castia` must not drag in the instrumented httpx/azure stacks,
    # which have to load only after telemetry is configured.
    code = (
        "import sys, castia; "
        "leaked = [m for m in ('httpx', 'azure', 'openai') "
        "if any(k == m or k.startswith(m + '.') for k in sys.modules)]; "
        "print(','.join(leaked))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "", f"import leaked heavy modules: {out.stdout!r}"


# -- reply decorations: entity builders -------------------------------------


def test_message_entity_folds_all_facets_into_one_root():
    from castia.entities import citation, message_entity, sensitivity_label

    entity = message_entity(
        ai_generated=True,
        citations=[citation(1, "Doc")],
        sensitivity=sensitivity_label("Confidential", description="Internal only"),
    )

    assert entity is not None
    # A single root schema.org/Message entity carries every facet (Teams rejects
    # more than one root message entity).
    assert entity["type"] == "https://schema.org/Message"
    assert entity["additionalType"] == ["AIGeneratedContent"]
    assert entity["citation"][0]["@type"] == "Claim"
    assert entity["usageInfo"]["name"] == "Confidential"


def test_message_entity_is_none_when_nothing_requested():
    from castia.entities import message_entity

    assert message_entity() is None


def test_citation_builder_shape_and_keyword_cap():
    from castia.entities import citation

    claim = citation(
        2,
        "Quarterly Report",
        url="https://example/report",
        abstract="Q3 numbers",
        keywords=["a", "b", "c", "d", "e"],
        icon="PDF",
    )

    assert claim == {
        "@type": "Claim",
        "position": 2,
        "appearance": {
            "@type": "DigitalDocument",
            "name": "Quarterly Report",
            "url": "https://example/report",
            "abstract": "Q3 numbers",
            "keywords": ["a", "b", "c"],  # capped at 3
            "image": {"@type": "ImageObject", "name": "PDF"},
        },
    }


def test_feedback_channel_data_shape():
    from castia.entities import feedback_channel_data

    assert feedback_channel_data() == {"feedbackLoop": {"type": "default"}}
    assert feedback_channel_data("custom") == {"feedbackLoop": {"type": "custom"}}


def test_mention_entity_pairs_entity_and_tag():
    from castia.entities import mention_entity

    m = mention_entity("8:orgid:aad-123", "Ada")
    assert m == {
        "type": "mention",
        "mentioned": {"id": "8:orgid:aad-123", "name": "Ada"},
        "text": "<at>Ada</at>",
    }


# -- reply decorations: turn context ----------------------------------------


def test_turn_cite_auto_increments_position():
    from castia.context import Turn

    turn = Turn(activity=_activity())
    assert turn.cite("First") == 1
    assert turn.cite("Second", url="https://x") == 2
    assert [c["position"] for c in turn.citations] == [1, 2]


def test_decorate_message_merges_turn_and_overrides():
    from castia.context import Turn, decorate_message

    turn = Turn(activity=_activity())
    turn.ai_generated = True
    turn.cite("From tool")

    payload: dict = {"type": "message", "text": "hi"}
    decorate_message(
        payload,
        turn,
        feedback="default",
        importance="high",
    )

    (root,) = [e for e in payload["entities"] if e["type"].endswith("Message")]
    assert root["additionalType"] == ["AIGeneratedContent"]
    assert len(root["citation"]) == 1  # the turn-accumulated citation
    assert payload["channelData"] == {"feedbackLoop": {"type": "default"}}
    assert payload["importance"] == "high"


def test_decorate_message_no_turn_uses_only_overrides():
    from castia.context import decorate_message

    payload: dict = {"type": "message"}
    decorate_message(payload, None, ai_generated=True)

    (root,) = payload["entities"]
    assert root["additionalType"] == ["AIGeneratedContent"]
    assert "channelData" not in payload  # nothing else requested


# -- reply decorations: wired through the send paths ------------------------


def test_send_reply_folds_turn_decorations(calls):
    from castia import connector
    from castia.context import turn_scope

    activity = _activity()

    async def run():
        with turn_scope(activity) as turn:
            turn.ai_generated = True
            turn.cite("Handbook", url="https://x")
            return await connector.send_reply(activity, "answer [1]")

    asyncio.run(run())

    (call,) = calls
    body = call["json"]
    assert body["text"] == "answer [1]"
    (root,) = body["entities"]
    assert root["additionalType"] == ["AIGeneratedContent"]
    assert root["citation"][0]["appearance"]["name"] == "Handbook"


def test_say_ai_generated_and_feedback(calls):
    from castia.context import turn_scope
    from castia.messages import Message

    activity = _activity()

    async def run():
        with turn_scope(activity):
            msg = Message(activity)
            return await msg.say("hi", ai_generated=True, feedback="default")

    asyncio.run(run())

    (call,) = calls
    body = call["json"]
    assert body["text"] == "hi"
    (root,) = body["entities"]
    assert root["additionalType"] == ["AIGeneratedContent"]
    assert body["channelData"] == {"feedbackLoop": {"type": "default"}}


def test_say_mention_passthrough_coexists_with_root_entity(calls):
    from castia.context import turn_scope
    from castia.entities import mention_entity
    from castia.messages import Message

    activity = _activity()

    async def run():
        with turn_scope(activity):
            msg = Message(activity)
            return await msg.say(
                "Thanks <at>Ada</at>",
                entities=[mention_entity("8:orgid:x", "Ada")],
                ai_generated=True,
            )

    asyncio.run(run())

    (call,) = calls
    ents = call["json"]["entities"]
    # Both the mention entity and the single root message entity are present.
    assert any(e["type"] == "mention" for e in ents)
    assert any(e["type"].endswith("Message") for e in ents)


# -- reply decorations: the cite_source tool --------------------------------


def test_cite_source_tool_accumulates_on_turn():
    from castia.context import turn_scope
    from castia.tools import _cite_source_impl

    activity = _activity()

    async def run():
        with turn_scope(activity) as turn:
            first = await _cite_source_impl(activity, name="A", url="https://a")
            second = await _cite_source_impl(activity, name="B")
            return first, second, turn

    first, second, turn = asyncio.run(run())
    assert first == {"ok": True, "position": 1, "marker": "[1]"}
    assert second == {"ok": True, "position": 2, "marker": "[2]"}
    assert len(turn.citations) == 2


def test_cite_source_tool_soft_fails_off_turn():
    from castia.tools import _cite_source_impl

    result = asyncio.run(_cite_source_impl(_activity(), name="A"))
    assert result["ok"] is False


def test_tool_families_split_and_compose():
    from castia.tools import activity_tools, agent_tools, graph_tools

    activity_names = {t.name for t in activity_tools()}
    graph_names = {t.name for t in graph_tools()}
    agent_names = {t.name for t in agent_tools()}

    assert activity_names == {"react_to_message", "cite_source"}
    assert "send_email" in graph_names and "read_inbox" in graph_names
    # agent_tools is the union of both families.
    assert agent_names == activity_names | graph_names


# -- Phase 2: inbound invoke shape ------------------------------------------


def _invoke_activity(name: str, value: dict | None = None) -> Activity:
    return Activity(
        type="invoke",
        id=ACTIVITY_ID,
        name=name,
        value=value or {},
        service_url=SERVICE_URL,
        conversation=ConversationAccount(id=CONVERSATION_ID),
        from_property=ChannelAccount(id="29:user", name="User"),
        recipient=ChannelAccount(id="28:agent", name="HAL"),
    )


def test_invoke_names_are_the_teams_wire_values():
    from castia import InvokeNames

    assert InvokeNames.feedback == "message/submitAction"
    assert InvokeNames.adaptive_card_action == "adaptiveCard/action"


def test_feedback_payload_parses_reaction_and_text():
    from castia import feedback_payload

    activity = _invoke_activity(
        "message/submitAction",
        {
            "actionName": "feedback",
            "actionValue": {"reaction": "like", "feedback": '{"feedbackText":"nice"}'},
        },
    )
    assert feedback_payload(activity) == {
        "reaction": "like",
        "feedback": '{"feedbackText":"nice"}',
    }


def test_feedback_payload_tolerates_empty_value():
    from castia import feedback_payload

    assert feedback_payload(_invoke_activity("message/submitAction")) == {
        "reaction": None,
        "feedback": None,
    }


def test_card_action_parses_verb_and_data():
    from castia import card_action

    activity = _invoke_activity(
        "adaptiveCard/action",
        {
            "action": {
                "type": "Action.Execute",
                "verb": "approve",
                "data": {"choice": "approve"},
            },
            "trigger": "manual",
        },
    )
    assert card_action(activity) == {"verb": "approve", "data": {"choice": "approve"}}


def test_message_invoke_response_shape():
    from castia import message_invoke_response

    body = message_invoke_response("done")
    assert body == {
        "statusCode": 200,
        "type": "application/vnd.microsoft.activity.message",
        "value": "done",
    }


def test_card_invoke_response_unwraps_attachment_to_content():
    from castia import card_invoke_response, decision_card

    card = decision_card("Resolved")  # a full attachment {contentType, content}
    body = card_invoke_response(card)

    assert body["statusCode"] == 200
    assert body["type"] == ADAPTIVE_CARD_CONTENT_TYPE
    # The value is the card *content*, not the attachment wrapper.
    assert body["value"]["type"] == "AdaptiveCard"
    assert "contentType" not in body["value"]


def test_card_invoke_response_accepts_bare_content():
    from castia import card_invoke_response

    bare = {"type": "AdaptiveCard", "body": []}
    assert card_invoke_response(bare)["value"] is bare


def test_decision_card_builds_execute_actions_with_verbs():
    from castia import decision_card

    attachment = decision_card(
        "Deploy?",
        {"title": "Approve", "verb": "approve"},
        "cancel",
    )
    content = attachment["content"]
    assert attachment["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
    # The prompt renders as a bold text block; buttons are Action.Execute.
    assert content["body"][0]["text"] == "Deploy?"
    actions = content["actions"]
    assert [a["type"] for a in actions] == ["Action.Execute", "Action.Execute"]
    assert actions[0]["verb"] == "approve"
    # A bare string is used as both label and verb.
    assert actions[1] == {
        "type": "Action.Execute",
        "title": "cancel",
        "verb": "cancel",
        "data": {},
    }


def test_decision_card_without_actions_is_terminal():
    from castia import decision_card

    content = decision_card("All done")["content"]
    assert content["actions"] == []
    assert content["body"][0]["text"] == "All done"


def test_router_invoke_registers_and_include_merges():
    from castia import InvokeNames, Router

    child = Router()

    @child.invoke(InvokeNames.feedback)
    async def on_feedback(msg):  # pragma: no cover - registration only
        return {}

    assert child._invokes[InvokeNames.feedback] is on_feedback

    parent = Router()
    parent.include(child)
    assert parent._invokes[InvokeNames.feedback] is on_feedback
    # An invoke-only router still reports the activity protocol.
    assert parent.registered_protocols() == ["activity"]


def test_router_invoke_duplicate_name_conflicts():
    from castia import InvokeNames, Router

    a = Router()
    b = Router()

    @a.invoke(InvokeNames.feedback)
    async def one(msg):  # pragma: no cover - registration only
        return {}

    @b.invoke(InvokeNames.feedback)
    async def two(msg):  # pragma: no cover - registration only
        return {}

    parent = Router()
    parent.include(a)
    with pytest.raises(ValueError, match=r"invoke 'message/submitAction'"):
        parent.include(b)


def test_make_invoke_dispatch_injects_message_and_returns_body():
    from castia import Message, card_action, message_invoke_response
    from castia.dispatch import make_invoke_dispatch

    seen = {}

    async def handler(msg) -> dict:
        seen["verb"] = card_action(msg.activity)["verb"]
        return message_invoke_response("ok")

    # This test module uses ``from __future__ import annotations``, so a written
    # ``msg: Message`` annotation would be the *string* "Message" and the
    # dispatch's ``is Message`` identity check would miss it. Real handler modules
    # don't use future-annotations; here we set the real class explicitly.
    handler.__annotations__["msg"] = Message

    dispatch = make_invoke_dispatch(handler)
    activity = _invoke_activity(
        "adaptiveCard/action",
        {"action": {"verb": "approve", "data": {}}},
    )
    body = asyncio.run(dispatch(activity))
    assert seen["verb"] == "approve"
    assert body["value"] == "ok"


def test_make_invoke_dispatch_none_when_handler_returns_non_dict():
    from castia.dispatch import make_invoke_dispatch

    async def handler(value) -> None:
        # A bare param receives the invoke value payload, not the message text.
        assert value == {"k": "v"}

    dispatch = make_invoke_dispatch(handler)
    body = asyncio.run(dispatch(_invoke_activity("x", {"k": "v"})))
    assert body is None


def test_server_routes_invoke_to_handler_and_returns_body():
    from fastapi.testclient import TestClient

    from castia import InvokeNames, Router, card_invoke_response, decision_card
    from castia.server import build_app

    router = Router()

    @router.invoke(InvokeNames.adaptive_card_action)
    async def on_action(msg) -> dict:
        return card_invoke_response(decision_card("Resolved"))

    app = build_app(router._routes, router._wire, router._invokes)
    client = TestClient(app)

    resp = client.post(
        "/activity/messages",
        json={
            "type": "invoke",
            "name": InvokeNames.adaptive_card_action,
            "value": {"action": {"verb": "approve", "data": {}}},
            "conversation": {"id": CONVERSATION_ID},
            "serviceUrl": SERVICE_URL,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == ADAPTIVE_CARD_CONTENT_TYPE
    assert body["value"]["type"] == "AdaptiveCard"


def test_server_unknown_invoke_acks_empty_200():
    from fastapi.testclient import TestClient

    from castia import Router
    from castia.server import build_app

    app = build_app([], {}, Router()._invokes)
    client = TestClient(app)

    resp = client.post(
        "/activity/messages",
        json={"type": "invoke", "name": "some/unhandled.invoke", "value": {}},
    )
    assert resp.status_code == 200
    assert resp.json() == {}



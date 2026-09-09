"""Unit tests for the agentic-identity fail-fast guard.

Pure model logic -- builds ``Activity`` objects directly, no SDK network. The
four turn modes we care about are exercised: the agent's Agentic-User (the only
one with a mailbox), the mailboxless ``agenticAppInstance`` (S2S), a delegated
human (OBO), and a traditional bot turn.
"""

from __future__ import annotations

import pytest

from castia import (
    AgenticIdentityError,
    Message,
    agentic_user_id,
    require_agentic_user,
)
from castia.activity import Activity, ChannelAccount, RoleTypes

AGENT_USER_OID = "11111111-1111-1111-1111-111111111111"
AGENT_APP_OID = "22222222-2222-2222-2222-222222222222"


def _activity(recipient: ChannelAccount | None) -> Activity:
    if recipient is None:
        return Activity(type="message")
    return Activity(type="message", recipient=recipient)


def _agentic_user_recipient() -> ChannelAccount:
    return ChannelAccount(
        id="agent",
        role=RoleTypes.agentic_user,
        agentic_user_id=AGENT_USER_OID,
        agentic_app_id=AGENT_APP_OID,
    )


def test_agentic_user_turn_passes():
    activity = _activity(_agentic_user_recipient())
    assert agentic_user_id(activity) == AGENT_USER_OID
    assert require_agentic_user(activity) == AGENT_USER_OID
    assert Message(activity).require_agentic_user() == AGENT_USER_OID
    assert Message(activity).agentic_user_id == AGENT_USER_OID


def test_app_instance_turn_is_rejected():
    # S2S: agentic *request* but no mailbox -- must fail even though
    # is_agentic_request() would be true.
    activity = _activity(
        ChannelAccount(
            id="agent",
            role=RoleTypes.agentic_identity,
            agentic_app_id=AGENT_APP_OID,
        )
    )
    assert activity.is_agentic_request() is True
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)


def test_delegated_human_obo_turn_is_rejected():
    activity = _activity(
        ChannelAccount(id="user@example.com", role=RoleTypes.user)
    )
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)


def test_traditional_bot_turn_is_rejected():
    activity = _activity(ChannelAccount(id="bot", role=RoleTypes.agent))
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)


def test_agentic_user_role_without_id_is_rejected():
    # Defensive: the role says agenticUser but the id is missing.
    activity = _activity(
        ChannelAccount(id="agent", role=RoleTypes.agentic_user)
    )
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)


def test_missing_recipient_is_rejected():
    activity = _activity(None)
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)


def test_real_wire_payload_deserializes_and_passes():
    # The wire delivers camelCase JSON; prove our Pydantic alias mapping
    # populates the snake_case fields our guard reads (regression guard on the
    # alias contract, not just hand-built Python models).
    wire = {
        "type": "message",
        "recipient": {
            "id": "29:agent",
            "name": "HAL",
            "role": "agenticUser",
            "agenticUserId": AGENT_USER_OID,
            "agenticAppId": AGENT_APP_OID,
            "tenantId": "dfa98250-28be-4cda-b270-45c05319c07c",
        },
    }
    activity = Activity.model_validate(wire)
    assert activity.recipient.agentic_user_id == AGENT_USER_OID
    assert require_agentic_user(activity) == AGENT_USER_OID


def test_real_wire_app_instance_payload_is_rejected():
    wire = {
        "type": "message",
        "recipient": {
            "id": "29:agent",
            "role": "agenticAppInstance",
            "agenticAppId": AGENT_APP_OID,
        },
    }
    activity = Activity.model_validate(wire)
    assert activity.is_agentic_request() is True  # but no mailbox
    assert agentic_user_id(activity) is None
    with pytest.raises(AgenticIdentityError):
        require_agentic_user(activity)

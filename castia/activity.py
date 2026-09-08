"""The Bot Framework **Activity**, as a small hand-rolled Pydantic model.

This replaces ``microsoft-agents-activity``. A Foundry Teams turn delivers a Bot
Framework Activity as camelCase JSON; we only need the handful of fields the
agent actually reads (text, identity, threading) plus the four agentic accessors
the mailbox tools depend on. Modelling it ourselves -- rather than pulling the
whole Agents SDK -- keeps the dependency surface tiny and the wire contract
explicit and testable.

Field semantics are reproduced faithfully from the SDK so live turns parse
identically (see ``tests/test_identity.py`` for the camelCase-alias regression
guard):

* ``from`` maps to :attr:`Activity.from_property` (``from`` is a keyword).
* every other field uses the standard camelCase alias (``serviceUrl`` ->
  ``service_url``, ``replyToId`` -> ``reply_to_id``, ...).
* the agentic accessors mirror ``Activity.is_agentic_request`` /
  ``get_agentic_instance_id`` / ``get_agentic_user`` / ``get_agentic_tenant_id``
  exactly, including that the *instance* id comes from ``recipient.agentic_app_id``.

Kept import-cheap (only pydantic) so importing ``castia`` never drags in an
instrumented SDK library -- telemetry has to be configured first.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class RoleTypes:
    """The ``role`` values a channel account can carry.

    A closed set of the roles this agent distinguishes. ``agent`` is a
    traditional bot (the Bot Framework wire value is ``"bot"``); ``agentic_user``
    and ``agentic_identity`` are the two agentic modes (see :mod:`castia.identity`).
    """

    user = "user"
    agent = "bot"
    agentic_identity = "agenticAppInstance"
    agentic_user = "agenticUser"


class _WireModel(BaseModel):
    """Base for every Activity sub-model: camelCase aliases, tolerant of extras.

    Mirrors the SDK's ``AgentsModel`` config so the same wire JSON validates:
    populate by field name *or* alias, and keep unknown fields rather than
    rejecting them (the platform adds channel-specific properties we ignore).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


class ChannelAccount(_WireModel):
    """A user or agent on a channel -- the ``from``/``recipient`` of an Activity."""

    id: str | None = None
    name: str | None = None
    aad_object_id: str | None = None
    role: str | None = None
    agentic_user_id: str | None = None
    agentic_app_id: str | None = None
    tenant_id: str | None = None


class ConversationAccount(_WireModel):
    """The conversation an Activity belongs to."""

    id: str | None = None
    name: str | None = None
    conversation_type: str | None = None
    tenant_id: str | None = None
    is_group: bool | None = None
    aad_object_id: str | None = None
    role: str | None = None


class Mention(_WireModel):
    """A ``mention`` entity: who was @-mentioned in the message text."""

    type: str | None = None
    mentioned: ChannelAccount | None = None
    text: str | None = None


class Activity(_WireModel):
    """A Bot Framework Activity -- the inbound Teams turn payload.

    Only the fields the agent reads are declared; anything else on the wire is
    preserved by ``extra="allow"`` but ignored. Build one with
    :meth:`model_validate` from the raw request JSON.
    """

    type: str | None = None
    id: str | None = None
    text: str | None = None
    service_url: str | None = None
    channel_id: str | None = None
    from_property: ChannelAccount | None = Field(default=None, alias="from")
    recipient: ChannelAccount | None = None
    conversation: ConversationAccount | None = None
    reply_to_id: str | None = None
    entities: list[dict[str, Any]] | None = None
    channel_data: dict[str, Any] | None = None
    # ``invoke`` turns (request/response) carry a routing ``name`` (e.g.
    # ``"message/submitAction"``, ``"adaptiveCard/action"``) and a ``value``
    # payload; both are ``None`` on a plain ``message`` turn.
    name: str | None = None
    value: Any = None

    # -- Teams surface helpers ------------------------------------------------

    @property
    def channel(self) -> str | None:
        """The main Bot Framework channel, dropping any ``channel:sub`` suffix.

        The wire delivers ``channelId`` as ``"msteams"`` (optionally
        ``"channel:sub_channel"``); routing only cares about the main channel.
        """
        if not self.channel_id:
            return None
        return self.channel_id.split(":", 1)[0].strip() or None

    def get_mentions(self) -> list[Mention]:
        """The ``mention`` entities on this activity, or an empty list."""
        if not self.entities:
            return []
        return [
            Mention.model_validate(entity)
            for entity in self.entities
            if str(entity.get("type", "")).lower() == "mention"
        ]

    # -- Agentic identity accessors (mirror the SDK exactly) ------------------

    def is_agentic_request(self) -> bool:
        """True when the recipient is either agentic role (user or app instance)."""
        return bool(
            self.recipient
            and self.recipient.role
            in (RoleTypes.agentic_identity, RoleTypes.agentic_user)
        )

    def get_agentic_instance_id(self) -> str | None:
        """The agent's app-instance id (``recipient.agentic_app_id``), if agentic."""
        if not self.is_agentic_request() or not self.recipient:
            return None
        return self.recipient.agentic_app_id

    def get_agentic_user(self) -> str | None:
        """The agent's agentic user id (``recipient.agentic_user_id``), if agentic."""
        if not self.is_agentic_request() or not self.recipient:
            return None
        return self.recipient.agentic_user_id

    def get_agentic_tenant_id(self) -> str | None:
        """The agentic tenant id, from the recipient or the conversation."""
        if not self.is_agentic_request():
            return None
        if self.recipient and self.recipient.tenant_id:
            return self.recipient.tenant_id
        if self.conversation and self.conversation.tenant_id:
            return self.conversation.tenant_id
        return None

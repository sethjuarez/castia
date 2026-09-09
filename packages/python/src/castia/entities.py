"""Reply *decorations*: the message-level metadata Teams renders around an answer.

A decoration is not a separate message and not a tool -- it is extra structure
stamped onto the outgoing answer activity: the "AI generated" label, source
citations, a sensitivity banner, the thumbs up/down feedback buttons, an
``@mention``, or a message importance. Teams reads these from three places on a
Bot Framework message activity:

* ``entities`` -- a **single** root ``https://schema.org/Message`` entity carries
  the AI label (``additionalType``), the ``citation`` list, and the ``usageInfo``
  sensitivity block. Teams rejects a message that has *more than one* root
  message entity, so all three facets must be folded into one object -- that is
  what :func:`message_entity` does. ``mention`` entities are a *different* entity
  ``type`` and may sit alongside the root message entity.
* ``channelData`` -- the ``feedbackLoop`` toggle for the thumbs up/down buttons.
* the activity's top-level ``importance`` field.

Like :mod:`castia.cards`, everything here is pure wire-dict construction -- no
network, no Azure, safe to import anywhere. The framework folds these onto the
outgoing message via :func:`castia.context.decorate_message`; a handler rarely
calls these directly (it passes ``ai_generated=``/``citations=``/... to
:meth:`castia.messages.Message.say`), but they are public so a handler can
build an entity by hand when it needs to.

Schemas mirror the Teams "AI-generated content" contract:
https://learn.microsoft.com/microsoftteams/platform/bots/how-to/bot-messages-ai-generated-content
"""

from __future__ import annotations

from typing import Any

#: The schema.org message-entity type Teams looks for.
_MESSAGE_ENTITY_TYPE = "https://schema.org/Message"

#: ``additionalType`` value that renders the "AI generated" label.
_AI_GENERATED = "AIGeneratedContent"

#: Teams caps: a message shows at most this many citations.
MAX_CITATIONS = 20


def message_entity(
    *,
    ai_generated: bool = False,
    citations: list[dict[str, Any]] | None = None,
    sensitivity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the single root ``schema.org/Message`` entity, or ``None``.

    Folds every message-level facet Teams reads from one entity -- the AI label,
    the ``citation`` list, and the ``usageInfo`` sensitivity block -- into one
    object, because Teams rejects a message carrying more than one root message
    entity. Returns ``None`` when no facet is requested, so a caller can append
    the result to ``entities`` unconditionally and add nothing when there is
    nothing to add.
    """
    if not ai_generated and not citations and not sensitivity:
        return None

    entity: dict[str, Any] = {
        "type": _MESSAGE_ENTITY_TYPE,
        "@type": "Message",
        "@context": "https://schema.org",
        "@id": "",
    }
    if ai_generated:
        entity["additionalType"] = [_AI_GENERATED]
    if citations:
        entity["citation"] = list(citations[:MAX_CITATIONS])
    if sensitivity:
        entity["usageInfo"] = sensitivity
    return entity


def citation(
    position: int,
    name: str,
    *,
    url: str = "",
    abstract: str = "",
    keywords: list[str] | None = None,
    icon: str = "",
) -> dict[str, Any]:
    """Build one ``Claim`` citation for :func:`message_entity`'s ``citations``.

    ``position`` is the 1-based number the in-text ``[n]`` marker refers to;
    ``name`` (<=80 chars) is the source title shown in the reference card;
    ``abstract`` (<=160 chars) is the hover snippet; ``keywords`` (<=3, each <=28
    chars) render as tags; ``icon`` is one of Teams' predefined document-type
    names (e.g. ``"PDF"``, ``"Word"``, ``"Excel"``) shown as the source glyph.
    Reference an in-text marker with ``"... as noted [1]."`` in the answer text.
    """
    appearance: dict[str, Any] = {"@type": "DigitalDocument", "name": name}
    if url:
        appearance["url"] = url
    if abstract:
        appearance["abstract"] = abstract
    if keywords:
        appearance["keywords"] = list(keywords[:3])
    if icon:
        appearance["image"] = {"@type": "ImageObject", "name": icon}
    return {"@type": "Claim", "position": position, "appearance": appearance}


def sensitivity_label(name: str, *, description: str = "") -> dict[str, Any]:
    """Build a ``usageInfo`` sensitivity block for :func:`message_entity`.

    Renders a shield banner (e.g. ``"Confidential"``) above the message; the
    optional ``description`` is the tooltip shown on hover.
    """
    info: dict[str, Any] = {"@type": "CreativeWork", "name": name}
    if description:
        info["description"] = description
    return info


def feedback_channel_data(kind: str = "default") -> dict[str, Any]:
    """Build the ``channelData`` that renders thumbs up/down feedback buttons.

    ``kind`` is ``"default"`` (Teams' built-in reaction dialog) or ``"custom"``
    (the bot handles the submission itself via an inbound ``invoke``). The
    framework merges this into the outgoing message's ``channelData``.
    """
    return {"feedbackLoop": {"type": kind}}


def mention_entity(account_id: str, name: str) -> dict[str, Any]:
    """Build a ``mention`` entity that pings ``account_id`` in the conversation.

    A mention needs both this entity **and** a matching ``<at>{name}</at>`` tag
    in the message text, so Teams knows which run of text to turn into the
    clickable mention::

        await msg.say(
            f"Thanks <at>{name}</at>!",
            entities=[mention_entity(user_id, name)],
        )

    ``account_id`` is the mentioned party's channel-account id (for a Teams user
    the ``8:orgid:<AAD>`` form).
    """
    return {
        "type": "mention",
        "mentioned": {"id": account_id, "name": name},
        "text": f"<at>{name}</at>",
    }

"""Inbound **invoke** activities -- the request/response half of the protocol.

The third of the three Activity shapes. A ``message`` turn is fire-and-forget
(the endpoint returns ``200`` and the reply is posted out-of-band through the
connector); an **invoke** turn is *request/response*: Teams ``POST``s an
``invoke`` activity and blocks on the HTTP body, which is the answer. That makes
invoke the home for anything the client needs a synchronous result from --
built-in feedback submissions and Adaptive Card **Universal Actions**.

This module is the small, pure surface for that shape:

* :class:`InvokeNames` -- the invoke ``name`` values this agent routes on.
* :func:`card_invoke_response` / :func:`message_invoke_response` -- build the
  ``InvokeResponse`` body a handler returns (a replacement card, or a text
  message). Returning a *card* is the proper fix for stale action cards: the
  card that fired the action is replaced in place with a terminal one, so its
  buttons can never be tapped twice.
* :func:`feedback_payload` / :func:`card_action` -- parse the inbound ``value``
  of the two invoke families into a plain dict.

Nothing here touches the network or Azure -- pure data, safe to import anywhere
(mirrors :mod:`castia.cards` / :mod:`castia.entities`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .cards import ADAPTIVE_CARD_CONTENT_TYPE

if TYPE_CHECKING:
    from .activity import Activity

#: The invoke-response ``type`` for a plain text message result.
INVOKE_MESSAGE_TYPE = "application/vnd.microsoft.activity.message"


class InvokeNames:
    """The ``activity.name`` values of the invoke turns this agent handles.

    Route each with ``@router.invoke(InvokeNames.<name>)``. These are the two
    Teams invoke families a reply's decorations/cards can generate:

    * :attr:`feedback` -- a user clicked the built-in thumbs up/down that the
      ``feedback="default"`` reply decoration renders. Teams still posts the
      submission here so the bot can record it; the handler returns an empty
      ``200`` ack.
    * :attr:`adaptive_card_action` -- a user tapped an ``Action.Execute`` button
      on a Universal Action card (see :func:`castia.cards.decision_card`). The
      handler returns a replacement card or message.
    """

    #: Built-in thumbs up/down submission (``feedback="default"`` / ``"custom"``).
    #: The wire ``activity.name`` is ``message/submitAction`` -- the camelCase
    #: sibling of ``message/fetchTask`` (confirmed live from an inbound Teams
    #: feedback turn). Teams' own SDK surfaces the *event* as
    #: ``message.submit.feedback`` (dot form), which is a normalized key, **not**
    #: the Bot Framework wire name a bot receives -- do not route on it.
    feedback = "message/submitAction"
    #: Adaptive Card Universal Action (``Action.Execute``).
    adaptive_card_action = "adaptiveCard/action"


def card_invoke_response(card: dict[str, Any]) -> dict[str, Any]:
    """An ``InvokeResponse`` body that **replaces** the acting card with ``card``.

    Accepts either a full attachment (``{"contentType", "content"}`` from
    :func:`castia.cards.adaptive_card` / :func:`castia.cards.decision_card`)
    or a bare card ``content`` dict; either way the card *content* is what Teams
    swaps in. This is the stale-card fix: return a terminal card (no buttons, or
    a "done" state) and the tapped card mutates in place.
    """
    content = card.get("content", card) if isinstance(card, dict) else card
    return {
        "statusCode": 200,
        "type": ADAPTIVE_CARD_CONTENT_TYPE,
        "value": content,
    }


def message_invoke_response(text: str) -> dict[str, Any]:
    """An ``InvokeResponse`` body that shows ``text`` as the action's result."""
    return {"statusCode": 200, "type": INVOKE_MESSAGE_TYPE, "value": text}


def feedback_payload(activity: Activity) -> dict[str, Any]:
    """Parse a ``message/submitAction`` feedback invoke into ``{reaction, feedback}``.

    The wire ``value`` is
    ``{"actionName": "feedback", "actionValue": {"reaction": "like"|"dislike",
    "feedback": "<json string>"}}``. ``reaction`` is the thumb; ``feedback`` is
    the free-text the user optionally typed (a JSON string when the card asked
    for it, else ``None``).
    """
    value = activity.value or {}
    action_value = value.get("actionValue") or {}
    return {
        "reaction": action_value.get("reaction"),
        "feedback": action_value.get("feedback"),
    }


def card_action(activity: Activity) -> dict[str, Any]:
    """Parse an ``adaptiveCard/action`` invoke into ``{verb, data}``.

    The wire ``value`` is
    ``{"action": {"type": "Action.Execute", "verb": "<verb>", "data": {...}},
    "trigger": "manual"}``. ``verb`` identifies which button fired;  ``data`` is
    that button's payload (from :func:`castia.cards.decision_card`).
    """
    value = activity.value or {}
    action = value.get("action") or {}
    return {"verb": action.get("verb"), "data": action.get("data") or {}}

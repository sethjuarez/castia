"""Rich reply payloads: Adaptive Cards and suggested actions.

Two small builders that return plain wire dicts, so a handler can hand the
framework something richer than a text string without importing the Teams SDK's
card object model. Both are consumed by :meth:`castia.messages.Message.say`
(and the streamer's ``finish``) via the ``attachments`` / ``suggestedActions``
fields of a Bot Framework message activity.

Nothing here touches the network or Azure -- it is pure data construction, safe
to import anywhere.
"""

from __future__ import annotations

from typing import Any

#: The Bot Framework attachment content-type for an Adaptive Card.
ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


class Reaction:
    """The Teams emoji reaction ids an agent can add or remove.

    ``reactionType`` is an open string on the wire, so any Teams reaction id
    works; these are the common ones (the classic six plus a few of the extended
    emoji ids used in the SDK's own examples). Pass any of these -- or a raw id
    string -- to :meth:`castia.messages.Message.react`.
    """

    like = "like"
    heart = "heart"
    laugh = "laugh"
    surprised = "surprised"
    sad = "sad"
    angry = "angry"
    #: eyes -- "I'm looking at this".
    eyes = "1f440_eyes"
    #: check -- "done".
    check = "2705_whiteheavycheckmark"
    #: hourglass -- "working on it".
    hold_on = "holdon"
    party = "party"


def adaptive_card(body: list[dict[str, Any]], **card: Any) -> dict[str, Any]:
    """Wrap Adaptive Card ``body`` elements as a message attachment.

    ``body`` is the list of card elements (``TextBlock``, ``Image``, ...). Any
    top-level card fields -- ``actions``, ``version``, ``msteams`` -- can be
    passed as keywords; ``version`` defaults to ``"1.5"``. The result goes in a
    message's ``attachments`` list::

        await msg.say("Here you go", attachments=[adaptive_card([
            {"type": "TextBlock", "text": "Deployment complete", "weight": "Bolder"},
        ])])
    """
    content: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": card.pop("version", "1.5"),
        "body": body,
    }
    content.update(card)
    return {"contentType": ADAPTIVE_CARD_CONTENT_TYPE, "content": content}


def suggested_actions(*actions: str | dict[str, Any]) -> dict[str, Any]:
    """Build a ``suggestedActions`` block of tappable reply chips.

    Each action may be a bare string (used as both the button title and the text
    posted back) or a dict with ``title``/``value``/``type`` to override. The
    default action type is ``imBack`` (Teams echoes ``value`` as the user's next
    message)::

        await msg.say("Deploy now?", suggestions=["Yes", "No", "Show diff"])
    """
    built = [_action(a) for a in actions]
    return {"actions": built, "to": []}


def _action(action: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, str):
        return {"type": "imBack", "title": action, "value": action}
    title = action.get("title") or action.get("value") or ""
    return {
        "type": action.get("type", "imBack"),
        "title": title,
        "value": action.get("value", title),
    }


def action_chips(
    *actions: str | dict[str, Any],
    prompt: str = "",
) -> dict[str, Any]:
    """Build an Adaptive Card of one-tap reply "chips" that actually works in Teams.

    Teams silently ignores Bot Framework ``suggestedActions``, so the portable
    way to offer quick replies is an Adaptive Card whose ``Action.Submit``
    buttons post a message back to the bot. Each action may be a bare string
    (used as both the button label and the text posted back) or a dict with
    ``title``/``value``. An optional ``prompt`` renders as a line of text above
    the buttons (and guarantees the card has a non-empty body). Drop the result
    in a message's ``attachments`` list::

        await msg.say("Done!", attachments=[action_chips(
            "Thanks!", "Tell me more", "Start over", prompt="Anything else?")])
    """
    body: list[dict[str, Any]] = []
    if prompt:
        body.append({"type": "TextBlock", "text": prompt, "wrap": True})
    return adaptive_card(body, actions=[_submit(a) for a in actions])


def _submit(action: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, str):
        title = value = action
    else:
        title = action.get("title") or action.get("value") or ""
        value = action.get("value", title)
    # ``msteams.imBack`` makes a tap post ``value`` as the user's next message --
    # the Teams-native equivalent of a suggested-action ``imBack`` chip, and a
    # single visible message. (We deliberately avoid ``messageBack``: its
    # separate ``text``/``displayText`` fields double-deliver in Teams 1:1 chats,
    # firing the handler twice for one tap.)
    return {
        "type": "Action.Submit",
        "title": title,
        "data": {
            "msteams": {"type": "imBack", "value": value},
            "choice": value,
        },
    }


def decision_card(
    prompt: str,
    *actions: str | dict[str, Any],
    **card: Any,
) -> dict[str, Any]:
    """An Adaptive Card of **Universal Action** buttons that resolve in place.

    Where :func:`action_chips` uses ``imBack`` -- each tap posts a *new* user
    message and the card lingers in the transcript, tappable and increasingly
    stale -- these buttons are ``Action.Execute`` and carry a ``verb``. A tap
    fires an ``adaptiveCard/action`` *invoke* (request/response), so the bot's
    ``@router.invoke(InvokeNames.adaptive_card_action)`` handler can return a
    replacement card (:func:`castia.invokes.card_invoke_response`) and the
    card mutates in place to a terminal state. That is the proper fix for stale
    action cards: once resolved, the buttons are gone.

    Each action is a bare string (used as both the label and the ``verb``) or a
    dict with ``title`` / ``verb`` / ``data``. Pass no actions to render a
    terminal, buttonless card (the natural "resolved" reply). ``prompt`` renders
    as a bold line above the buttons::

        await msg.say("Deploy?", attachments=[decision_card(
            "Ready to deploy the next version?",
            {"title": "Approve", "verb": "approve"},
            {"title": "Cancel", "verb": "cancel"})])
    """
    body: list[dict[str, Any]] = []
    if prompt:
        body.append(
            {"type": "TextBlock", "text": prompt, "wrap": True, "weight": "Bolder"}
        )
    return adaptive_card(body, actions=[_execute(a) for a in actions], **card)


def _execute(action: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, str):
        title = verb = action
        data: dict[str, Any] = {}
    else:
        title = action.get("title") or action.get("verb") or ""
        verb = action.get("verb") or title
        data = action.get("data") or {}
    # ``Action.Execute`` (Adaptive Cards 1.4+) fires an ``adaptiveCard/action``
    # invoke carrying this ``verb`` + ``data``; the bot answers with a card that
    # replaces this one. ``version`` defaults to 1.5 via ``adaptive_card``.
    return {"type": "Action.Execute", "title": title, "verb": verb, "data": data}

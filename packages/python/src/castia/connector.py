"""The Bot Framework **connector**, in plain ``httpx``.

The activity protocol is not request/response: the platform ``POST``s an inbound
Activity to ``/activity/messages``, the container answers ``200`` immediately,
and everything the agent does back to the conversation -- a reply, a typing
indicator, a reaction, a streamed answer -- is delivered *out of band* by calling
the connector at ``serviceUrl``. This module is those calls, replacing what
``CloudAdapter`` did inside the SDK. It mirrors a live-validated plain-HTTP
reference implementation for the reply, and the
Microsoft Teams SDK's own wire contract for the richer verbs (typing, reactions,
message update/delete, streaming) -- see ``microsoft/teams.ts``
``clients/reaction`` and ``http/http-stream``.

The one subtlety worth stating plainly is **which identity signs the call**, and
it is entirely determined by the inbound turn:

* **Agentic turn** (``recipient.role == "agenticUser"`` with an agentic user id):
  the conversation roster participant is the agent's *agentic user*, not the bot
  app. A bot-app token is answered ``403 BotNotInConversationRoster``, so the
  call must carry a **delegated agentic-user** token for the Agent 365 APX
  audience (:data:`~castia.credentials.APX_PRODUCTION_SCOPE`), minted via the
  ``user_fic`` chain.
* **Bot turn** (hosted, non-agentic): an app token pinned to the Azure Bot's
  ``msaAppId`` (:func:`~castia.credentials.bot_connector_token`).
* **Local run** (``azd ai agent run`` / M365 Agents Playground): the emulator
  accepts an anonymous call, so no ``Authorization`` header is sent.

Every function is **best-effort**: a failure is logged, never raised, because the
turn is already "handled" by the ``200`` the endpoint returned. The calls a
caller needs a result from -- ``post_activity`` (the created id, for streaming)
and the reaction / update / delete verbs (a success bool) -- return that result
instead of raising.

Imported lazily by the server, so ``httpx``/``azure-identity`` stay out of
``import castia``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from .activity import Activity
from .auth import is_local_run
from .cards import Reaction
from .credentials import (
    APX_PRODUCTION_SCOPE,
    AgenticIdentity,
    bearer,
    bot_connector_token,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public verbs
# ---------------------------------------------------------------------------


async def send_reply(activity: Activity, text: str) -> str | None:
    """Reply to the originating conversation with ``text``. Best-effort.

    Threads under the inbound activity (``replyToId``) and swaps ``from`` /
    ``recipient``. Folds on any reply decorations the turn accumulated (AI
    label, citations, feedback, ...) so the ``return "text"`` handler path is
    decorated exactly like an explicit :meth:`Message.say`. Returns the created
    activity's id (useful to later edit or delete it), or ``None`` on an empty
    reply / missing conversation / failure.
    """
    if not text:
        return None
    from .context import current_turn_or_none, decorate_message

    payload: dict[str, Any] = {"type": "message", "text": text}
    decorate_message(payload, current_turn_or_none())
    return await post_activity(activity, payload)


async def send_typing(activity: Activity) -> None:
    """Show the "...is typing" indicator in the conversation. Best-effort.

    A standalone ``typing`` activity; Teams clears it automatically when the next
    message arrives, so it is fire-and-forget around slow work.
    """
    await post_activity(activity, {"type": "typing"})


async def add_reaction(activity: Activity, reaction_type: str = Reaction.like) -> bool:
    """Add an emoji reaction to the inbound message. Returns success.

    ``PUT {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}
    /reactions/{reactionType}`` -- the modern Teams reaction verb (no body).
    """
    return await _reaction(activity, reaction_type, method="PUT")


async def remove_reaction(
    activity: Activity, reaction_type: str = Reaction.like
) -> bool:
    """Remove a previously added emoji reaction. Returns success."""
    return await _reaction(activity, reaction_type, method="DELETE")


async def post_activity(
    activity: Activity,
    payload: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    """POST a new activity to the conversation. Returns its id, or ``None``.

    ``payload`` supplies ``type``/``text``/``attachments``/``channelData``/...;
    the envelope fields (``from``, ``recipient``, ``conversation``,
    ``replyToId``) are filled from the inbound turn so a caller only states what
    is new. This is the connector ``createActivity`` -- the primitive every send
    (reply, typing, streamed chunk, card) is built on.
    """
    endpoint = _conversation_endpoint(activity)
    if endpoint is None:
        return None
    service_url, conversation_id = endpoint

    body = _envelope(activity, conversation_id)
    body.update(payload)

    url = f"{service_url}/v3/conversations/{conversation_id}/activities"
    response = await _send(activity, "POST", url, json=body, headers=headers)
    if response is None:
        return None
    return _created_id(response)


async def update_activity(
    activity: Activity,
    activity_id: str,
    payload: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """Replace an activity we previously sent (edit a message). Returns success.

    ``PUT {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}``.
    """
    endpoint = _conversation_endpoint(activity)
    if endpoint is None or not activity_id:
        return False
    service_url, conversation_id = endpoint

    body = _envelope(activity, conversation_id)
    body["id"] = activity_id
    body.update(payload)

    url = (
        f"{service_url}/v3/conversations/{conversation_id}"
        f"/activities/{quote(activity_id, safe='')}"
    )
    response = await _send(activity, "PUT", url, json=body, headers=headers)
    return _ok(response)


async def delete_activity(activity: Activity, activity_id: str) -> bool:
    """Delete (retract) an activity we previously sent. Returns success.

    ``DELETE {serviceUrl}/v3/conversations/{conversationId}/activities/{activityId}``.
    """
    endpoint = _conversation_endpoint(activity)
    if endpoint is None or not activity_id:
        return False
    service_url, conversation_id = endpoint
    url = (
        f"{service_url}/v3/conversations/{conversation_id}"
        f"/activities/{quote(activity_id, safe='')}"
    )
    response = await _send(activity, "DELETE", url)
    return _ok(response)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _reaction(activity: Activity, reaction_type: str, *, method: str) -> bool:
    endpoint = _conversation_endpoint(activity)
    if endpoint is None or not activity.id or not reaction_type:
        return False
    service_url, conversation_id = endpoint
    url = (
        f"{service_url}/v3/conversations/{conversation_id}"
        f"/activities/{quote(activity.id, safe='')}"
        f"/reactions/{quote(reaction_type, safe='')}"
    )
    response = await _send(activity, method, url)
    return _ok(response)


def _conversation_endpoint(activity: Activity) -> tuple[str, str] | None:
    """``(serviceUrl, conversationId)`` for outbound calls, or ``None``."""
    service_url = (activity.service_url or "").rstrip("/")
    conversation_id = activity.conversation.id if activity.conversation else None
    if not service_url or not conversation_id:
        logger.info("No serviceUrl/conversation to answer; skipping connector call.")
        return None
    return service_url, conversation_id


def _envelope(activity: Activity, conversation_id: str) -> dict[str, Any]:
    """The reply envelope: swap from/recipient, thread under the inbound turn."""
    return {
        "from": _account_json(activity.recipient),
        "recipient": _account_json(activity.from_property),
        "conversation": {"id": conversation_id},
        "replyToId": activity.id,
    }


async def _send(
    activity: Activity,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response | None:
    """The single HTTP choke point. Best-effort: returns ``None`` on any failure.

    Centralizing every connector request here keeps identity selection in one
    place and gives tests one seam to intercept.
    """
    try:
        if headers is not None:
            request_headers = dict(headers)
        else:
            request_headers = await _authorization(activity)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method, url, json=json, headers=request_headers
            )
        if response.status_code >= 400:
            logger.error(
                "Connector %s %s returned %s: %s",
                method,
                url.rsplit("/v3/", 1)[-1],
                response.status_code,
                response.text[:500],
            )
        return response
    except Exception:
        logger.exception("Connector %s failed.", method)
        return None


async def authorization(activity: Activity) -> dict[str, str]:
    """The ``Authorization`` header (if any) for this turn's outbound identity.

    Public so a caller that makes many connector calls for one turn (the
    streamer) can mint the token **once** and pass it back via ``headers=`` rather
    than re-running the ``user_fic`` chain per chunk.
    """
    return await _authorization(activity)


async def _authorization(activity: Activity) -> dict[str, str]:
    if is_local_run():
        # Emulator / Agents Playground accepts anonymous connector calls.
        return {}

    agentic_user = activity.get_agentic_user()
    if (
        activity.is_agentic_request()
        and agentic_user
        and (identity := AgenticIdentity.from_env())
    ):
        try:
            token = await identity.user_token(agentic_user, APX_PRODUCTION_SCOPE)
            return {"Authorization": bearer(token)}
        except Exception:
            logger.exception(
                "Failed to mint agentic-user connector token; "
                "falling back to bot identity."
            )

    token = await bot_connector_token()
    return {"Authorization": bearer(token)}


def _created_id(response: httpx.Response) -> str | None:
    """The ``id`` of a just-created activity (needed to stream / edit it)."""
    if response.status_code >= 400:
        return None
    try:
        return response.json().get("id")
    except Exception:  # noqa: BLE001 - a bodyless 2xx is fine, just no id
        return None


def _ok(response: httpx.Response | None) -> bool:
    return response is not None and response.status_code < 400


def _account_json(account: Any) -> dict:
    """Serialize a channel account back to camelCase wire JSON (or ``{}``)."""
    if account is None:
        return {}
    return account.model_dump(by_alias=True, exclude_none=True)

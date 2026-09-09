"""Experimental: send mail as the agent's own Agentic-User via Microsoft Graph.

Proof-of-concept for the agentic-email path -- **no MCP**, a direct Graph REST
call. On a turn that carries the agent's Agentic-User identity, we mint a
*delegated* Microsoft Graph token for the agent's own mailbox (the ``user_fic``
chain in :mod:`castia.credentials`, replacing the SDK's
``get_agentic_user_token``) and call ``sendMail``.

Every failure is captured in the returned dict rather than raised, so the
experiment can report exactly which stage failed (token vs Graph) and what the
granted scopes were. The one hard failure is identity: ``require_agentic_user``
raises if the turn is not the agent's own mailbox identity.

Imports are intentionally kept out of ``castia/__init__`` and this module is
imported lazily from the handler, so ``azure-identity``/``httpx`` load only when
an email turn actually fires -- never at ``import castia`` time.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from .credentials import agentic_user_token, bearer
from .identity import require_agentic_user

if TYPE_CHECKING:
    from .activity import Activity

_GRAPH_SEND = "https://graph.microsoft.com/v1.0/me/sendMail"
# Replying keeps the message in its original conversation thread. Graph's reply
# action builds the quoted original server-side and sends it; the comment is our
# new text placed above the quote -- exactly like clicking "Reply" in Outlook.
_GRAPH_REPLY = "https://graph.microsoft.com/v1.0/me/messages/{message_id}/reply"


async def send_as_agent(
    activity: Activity, *, to: str, subject: str, body: str
) -> dict:
    """Send an email as the agent's own mailbox. Returns a diagnostic result."""
    user_id = require_agentic_user(activity)

    try:
        token = await agentic_user_token(user_id)
    except Exception as exc:  # noqa: BLE001 - diagnostic capture for the experiment
        return {"ok": False, "stage": "token", "detail": f"{type(exc).__name__}: {exc}"}

    if not token:
        return {"ok": False, "stage": "token", "detail": "no agentic user token returned"}

    granted = _scopes_from(token)
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": True,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _GRAPH_SEND,
            headers={
                "Authorization": bearer(token),
                "Content-Type": "application/json",
            },
            content=json.dumps(message),
        )

    ok = resp.status_code == 202
    return {
        "ok": ok,
        "stage": "send",
        "status": resp.status_code,
        "granted_scopes": granted,
        "detail": "" if ok else resp.text[:500],
    }


async def reply_as_agent(
    activity: Activity, *, message_id: str, body: str
) -> dict:
    """Reply in-thread to a received message. Returns a diagnostic result.

    Unlike :func:`send_as_agent` (which composes a brand-new message), this uses
    Graph's ``/reply`` action on the received message so the response stays in the
    original conversation thread with the quoted history beneath our comment.
    """
    user_id = require_agentic_user(activity)

    try:
        token = await agentic_user_token(user_id)
    except Exception as exc:  # noqa: BLE001 - diagnostic capture for the experiment
        return {"ok": False, "stage": "token", "detail": f"{type(exc).__name__}: {exc}"}

    if not token:
        return {"ok": False, "stage": "token", "detail": "no agentic user token returned"}

    granted = _scopes_from(token)
    url = _GRAPH_REPLY.format(message_id=message_id)
    payload = {"comment": body}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": bearer(token),
                "Content-Type": "application/json",
            },
            content=json.dumps(payload),
        )

    ok = resp.status_code in (200, 202)
    return {
        "ok": ok,
        "stage": "reply",
        "status": resp.status_code,
        "granted_scopes": granted,
        "detail": "" if ok else resp.text[:500],
    }


def _scopes_from(token: str) -> str:
    """Best-effort read of the token's delegated scopes for diagnostics."""
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False})
        return claims.get("scp") or claims.get("roles") or "<none>"
    except Exception:  # noqa: BLE001 - diagnostic only
        return "<undecodable>"

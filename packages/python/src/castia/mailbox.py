"""Experimental: read the agent's own mailbox via Microsoft Graph.

The receive-side counterpart to :mod:`castia.mail`. On a turn carrying the
agent's Agentic-User identity we mint a *delegated* Microsoft Graph token for the
agent's own mailbox and ``GET /me/messages`` -- proving the agent can not only
send but also *receive* mail. **No MCP**: a direct Graph REST call.

Reading requires ``Mail.Read`` to be among the consented delegated scopes (see
the castia-ops skill's agentic-Graph-consent section). Failures are captured
in the returned dict rather than raised, except identity, which fails fast via
``require_agentic_user``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx

from .credentials import agentic_user_token, bearer
from .identity import require_agentic_user

if TYPE_CHECKING:
    from .activity import Activity

_GRAPH_MESSAGES = "https://graph.microsoft.com/v1.0/me/messages"
_SELECT = "id,from,subject,receivedDateTime,isRead,bodyPreview"


async def read_inbox_as_agent(
    activity: Activity, *, top: int = 5, unread_only: bool = False
) -> dict:
    """Read the newest messages in the agent's own mailbox. Diagnostic result."""
    user_id = require_agentic_user(activity)

    try:
        token = await agentic_user_token(user_id)
    except Exception as exc:  # noqa: BLE001 - diagnostic capture for the experiment
        return {"ok": False, "stage": "token", "detail": f"{type(exc).__name__}: {exc}"}

    if not token:
        return {"ok": False, "stage": "token", "detail": "no agentic user token returned"}

    params = {
        "$top": str(max(1, min(top, 25))),
        "$select": _SELECT,
        "$orderby": "receivedDateTime desc",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"
    url = f"{_GRAPH_MESSAGES}?{urlencode(params)}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            url,
            headers={
                "Authorization": bearer(token),
                "Content-Type": "application/json",
            },
        )

    ok = resp.status_code == 200
    if not ok:
        return {"ok": False, "stage": "read", "status": resp.status_code, "detail": resp.text[:500]}

    messages = [_summarize(m) for m in resp.json().get("value", [])]
    return {"ok": True, "stage": "read", "status": 200, "count": len(messages), "messages": messages}


def _summarize(message: dict) -> dict:
    """Flatten a Graph message into the few fields worth reporting."""
    sender = (message.get("from") or {}).get("emailAddress") or {}
    return {
        "id": message.get("id", ""),
        "from": sender.get("address", "<unknown>"),
        "subject": message.get("subject", "(no subject)"),
        "received": message.get("receivedDateTime", ""),
        "unread": not message.get("isRead", True),
        "preview": (message.get("bodyPreview") or "").strip()[:200],
    }

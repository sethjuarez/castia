"""Experimental: create a document on the agent's own Agentic-User OneDrive.

Sibling to :mod:`castia.mail`. Same auth path -- **no MCP** -- a direct
Microsoft Graph REST call. On a turn that carries the agent's Agentic-User
identity, we mint a *delegated* Graph token (the ``user_fic`` chain in
:mod:`castia.credentials`) and ``PUT`` file content into the agent's own
OneDrive (``/me/drive/root:/{path}:/content``). Needs the ``Files.ReadWrite``
delegated scope to be among the blueprint's consented permissions.

Every failure is captured in the returned dict rather than raised, so the
experiment can report exactly which stage failed (token vs Graph). The one hard
failure is identity: ``require_agentic_user`` raises if the turn is not the
agent's own mailbox/drive identity.

Imported lazily from the handler, exactly like ``mail.py``, so ``azure-identity``
loads only when a document turn actually fires.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from .credentials import agentic_user_token, bearer
from .identity import require_agentic_user

if TYPE_CHECKING:
    from .activity import Activity

# Simple upload (<=250MB): PUT raw bytes to the drive path. .default returns
# whatever delegated Graph permissions the blueprint has consented -- writing a
# file needs Files.ReadWrite (or .All) to be among them.
_GRAPH_DRIVE_ROOT = "https://graph.microsoft.com/v1.0/me/drive/root:/{path}:/content"
# A newly-uploaded item lives on the agent's OWN OneDrive, so the human who
# asked for it has no access and would hit a "request access" wall. We invite
# them directly (by their Entra object id from the turn) so the document arrives
# already shared. driveRecipient supports objectId, so no email lookup / extra
# Graph scope is needed beyond the Files.ReadWrite the upload already uses.
_GRAPH_ITEM_INVITE = "https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/invite"


async def create_doc_as_agent(
    activity: Activity,
    *,
    path: str,
    content: str,
    content_type: str = "text/markdown",
) -> dict:
    """Create/replace a document on the agent's own OneDrive. Diagnostic result."""
    user_id = require_agentic_user(activity)

    try:
        token = await agentic_user_token(user_id)
    except Exception as exc:  # noqa: BLE001 - diagnostic capture for the experiment
        return {"ok": False, "stage": "token", "detail": f"{type(exc).__name__}: {exc}"}

    if not token:
        return {"ok": False, "stage": "token", "detail": "no agentic user token returned"}

    granted = _scopes_from(token)
    # Encode each path segment but keep the slashes that separate OneDrive folders.
    encoded = "/".join(quote(seg) for seg in path.strip("/").split("/"))
    url = _GRAPH_DRIVE_ROOT.format(path=encoded)
    auth_header = bearer(token)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            url,
            headers={
                "Authorization": auth_header,
                "Content-Type": content_type,
            },
            content=content.encode("utf-8"),
        )

    ok = resp.status_code in (200, 201)
    if not ok:
        return {
            "ok": False,
            "stage": "upload",
            "status": resp.status_code,
            "granted_scopes": granted,
            "web_url": "",
            "detail": resp.text[:500],
        }

    item = resp.json()
    web_url = item.get("webUrl", "")
    shared = await _share_with_requester(
        auth_header=auth_header, item_id=item.get("id", ""), activity=activity
    )
    return {
        "ok": True,
        "stage": "upload",
        "status": resp.status_code,
        "granted_scopes": granted,
        "web_url": web_url,
        "shared": shared,
        "detail": "",
    }


async def _share_with_requester(
    *, auth_header: str, item_id: str, activity: Activity
) -> dict:
    """Grant the interacting human access to the freshly created item.

    The document was written to the agent's own OneDrive, so the requester has no
    access by default. We resolve their Entra object id from the turn's sender
    (``from_property.aad_object_id``) and invite them with write access, avoiding
    the "request access" wall. Diagnostic dict; never raises.
    """
    sender = getattr(activity, "from_property", None)
    requester = getattr(sender, "aad_object_id", None) if sender else None
    if not item_id:
        return {"ok": False, "detail": "no item id returned from upload"}
    if not requester:
        return {"ok": False, "detail": "no requester object id on the activity"}

    url = _GRAPH_ITEM_INVITE.format(item_id=item_id)
    payload = {
        "recipients": [{"objectId": requester}],
        "requireSignIn": True,
        "sendInvitation": True,
        "roles": ["write"],
        "message": "Sharing the document you asked me to create.",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            headers={"Authorization": auth_header, "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
    ok = resp.status_code in (200, 201)
    return {
        "ok": ok,
        "status": resp.status_code,
        "recipient": requester,
        "role": "write",
        "detail": "" if ok else resp.text[:300],
    }


def _scopes_from(token: str) -> str:
    """Best-effort read of the token's delegated scopes for diagnostics."""
    try:
        import jwt

        claims = jwt.decode(token, options={"verify_signature": False})
        return claims.get("scp") or claims.get("roles") or "<none>"
    except Exception:  # noqa: BLE001 - diagnostic only
        return "<undecodable>"

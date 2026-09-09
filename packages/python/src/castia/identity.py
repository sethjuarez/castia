"""Agentic-identity detection for the hosted agent.

A turn only carries the agent's *own mailbox* identity when it runs as the
agent's **Agentic-User** -- the AI-teammate account (Frontier preview). The SDK
surfaces this on ``activity.recipient``: ``role == "agenticUser"`` together with
an ``agentic_user_id`` (the agent's Entra object id, 1:1 with the agent). Two
neighbouring modes look similar but have **no mailbox** and must be rejected:

* ``agenticAppInstance`` -- the agent's app-instance (S2S) identity.
* a delegated human (OBO) -- ``role == "user"``.

Note ``Activity.is_agentic_request()`` is deliberately *not* the gate: it also
returns true for ``agenticAppInstance``, which cannot send mail as the agent.
The mailbox gate is specifically ``role == agenticUser``.

Kept import-cheap (the ``Activity`` import is type-checking only) so importing
``castia`` stays free of instrumented modules -- see ``application.Agent.run``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .activity import RoleTypes

if TYPE_CHECKING:
    from .activity import Activity


class AgenticIdentityError(PermissionError):
    """Raised when a turn lacks the agent's own Agentic-User (mailbox) identity."""


def agentic_user_id(activity: Activity) -> str | None:
    """Return the agent's own agentic user id for this turn, or ``None``.

    Non-``None`` only when the turn runs as the agent's Agentic-User -- i.e.
    ``recipient.role == "agenticUser"`` with a populated ``agentic_user_id``.
    Every other mode (bot, delegated human/OBO, ``agenticAppInstance``) yields
    ``None`` because none of them can act as the agent's mailbox.
    """
    recipient = activity.recipient
    if recipient is None or recipient.role != RoleTypes.agentic_user:
        return None
    return recipient.agentic_user_id or None


def require_agentic_user(activity: Activity) -> str:
    """Return the agent's agentic user id, or raise ``AgenticIdentityError``.

    Fail-fast gate for any action performed *as the agent itself* (e.g. sending
    mail from the agent's mailbox). Rejects traditional bot/user turns,
    delegated-human (OBO) turns, and the ``agenticAppInstance`` (S2S) mode that
    has no mailbox. Only a turn running as the agent's Agentic-User passes.
    """
    user_id = agentic_user_id(activity)
    if not user_id:
        role = getattr(activity.recipient, "role", None)
        raise AgenticIdentityError(
            "this action requires the agent's Agentic-User identity "
            f"(Frontier preview); recipient.role={role!r} carries no mailbox"
        )
    return user_id

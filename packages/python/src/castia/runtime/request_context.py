"""Request-scoped Foundry platform identity for native wire protocols.

Foundry hosted agents can receive an opaque per-request call id and a stable
per-user id on protocol requests. Code that calls back into Foundry services
must forward only the call id; the user id is for local state partitioning.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

FOUNDRY_CALL_ID_HEADER = "x-agent-foundry-call-id"
FOUNDRY_USER_ID_HEADER = "x-agent-user-id"
FOUNDRY_SESSION_ID_HEADER = "x-agent-session-id"


@dataclass(frozen=True)
class RequestContext:
    """Platform identity bound to the current HTTP request."""

    call_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None

    def platform_headers(self) -> dict[str, str]:
        """Headers safe to forward to Foundry platform services."""
        return {FOUNDRY_CALL_ID_HEADER: self.call_id} if self.call_id else {}


_EMPTY = RequestContext()
_current: ContextVar[RequestContext] = ContextVar(
    "castia_request_context", default=_EMPTY
)


def current_request_context() -> RequestContext:
    """Return the request context, or an empty context outside a request."""
    return _current.get()


def set_request_context(context: RequestContext) -> Token[RequestContext]:
    """Bind ``context`` as current; reset with :func:`reset_request_context`."""
    return _current.set(context)


def reset_request_context(token: Token[RequestContext]) -> None:
    """Restore the previous request context."""
    try:
        _current.reset(token)
    except ValueError:
        _current.set(_EMPTY)

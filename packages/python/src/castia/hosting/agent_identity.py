"""Agent identity for hosted Responses: ``agent_reference`` and ``agent_session_id``.

Foundry storage refuses to create a response whose snapshot lacks
``agent_reference`` with a ``name`` and ``version``. The agentserver Responses
SDK copies the validated request ``agent_reference`` onto every response
snapshot and its output items, and resolves ``agent_session_id`` from the
request, then ``FOUNDRY_AGENT_SESSION_ID``, then a value derived from the
conversation. These helpers mirror that behavior for Castia.

When a request omits ``agent_reference`` (or its ``version``), Castia falls
back to the container's own platform-injected identity, ``FOUNDRY_AGENT_NAME``
and ``FOUNDRY_AGENT_VERSION``. It never guesses a version for a different
agent name.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Mapping
from copy import deepcopy

from castia.hosting.response_ids import extract_partition_key

__all__ = [
    "AGENT_NAME_ENV",
    "AGENT_SESSION_ID_ENV",
    "AGENT_VERSION_ENV",
    "SESSION_ID_HEADER",
    "InvalidAgentReferenceError",
    "SessionHeaderMiddleware",
    "is_complete_agent_reference",
    "resolve_agent_reference",
    "resolve_agent_session_id",
    "session_header_value",
]

SESSION_ID_HEADER = "x-agent-session-id"
# ``request.state`` attribute carrying the POST /responses resolved session.
SESSION_STATE_KEY = "castia_agent_session_id"

AGENT_NAME_ENV = "FOUNDRY_AGENT_NAME"
AGENT_VERSION_ENV = "FOUNDRY_AGENT_VERSION"
AGENT_SESSION_ID_ENV = "FOUNDRY_AGENT_SESSION_ID"
_REFERENCE_TYPE = "agent_reference"
# agentserver's fallback name when deriving a session id without a reference.
_DEFAULT_AGENT_NAME = "server-default-agent"
_SESSION_ID_LENGTH = 63


class InvalidAgentReferenceError(ValueError):
    """The request ``agent_reference`` is malformed."""

    def __init__(self, message: str, *, param: str) -> None:
        super().__init__(message)
        self.param = param


def _env(name: str, env: Mapping[str, str] | None) -> str:
    value = (os.environ if env is None else env).get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _normalize(value: object) -> dict | None:
    """agentserver ``_normalize_agent_reference``: validate; ``None`` when absent."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidAgentReferenceError(
            "agent_reference must be an object", param="agent_reference"
        )
    reference = deepcopy(value)
    reference.setdefault("type", _REFERENCE_TYPE)
    if reference["type"] != _REFERENCE_TYPE:
        raise InvalidAgentReferenceError(
            "agent_reference.type must be 'agent_reference'",
            param="agent_reference.type",
        )
    name = reference.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidAgentReferenceError(
            "agent_reference.name must be a non-empty string",
            param="agent_reference.name",
        )
    reference["name"] = name.strip()
    return reference


def resolve_agent_reference(
    value: object, *, env: Mapping[str, str] | None = None
) -> dict | None:
    """The request ``agent_reference``, completed from the hosted agent's identity.

    Returns the validated request reference. When it is absent, returns the
    ``FOUNDRY_AGENT_NAME``/``FOUNDRY_AGENT_VERSION`` identity if both are set.
    When the request names this agent but omits ``version``, the hosted
    version is filled in. Returns ``None`` when neither source names an agent.

    :raises InvalidAgentReferenceError: the request value is malformed.
    """
    reference = _normalize(value)
    name = _env(AGENT_NAME_ENV, env)
    version = _env(AGENT_VERSION_ENV, env)
    if reference is None:
        if name and version:
            return {"type": _REFERENCE_TYPE, "name": name, "version": version}
        return None
    current = reference.get("version")
    if (
        (not isinstance(current, str) or not current.strip())
        and version
        and reference["name"] == name
    ):
        reference["version"] = version
    return reference


def is_complete_agent_reference(value: object) -> bool:
    """True when ``value`` has type ``agent_reference`` plus a name and version."""
    if not isinstance(value, Mapping) or value.get("type") != _REFERENCE_TYPE:
        return False
    name = value.get("name")
    version = value.get("version")
    return (
        isinstance(name, str)
        and bool(name.strip())
        and isinstance(version, str)
        and bool(version.strip())
    )


def resolve_agent_session_id(
    body: Mapping,
    *,
    agent_reference: Mapping | None,
    previous_response_id: str | None,
    conversation_id: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    """agentserver ``_resolve_session_id``: request, then env, then derived.

    The derived id hashes ``name:version:partition`` of the conversation or
    previous response, so a conversation keeps one session; a one-shot
    request gets a random id.
    """
    requested = body.get("agent_session_id")
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    platform = _env(AGENT_SESSION_ID_ENV, env)
    if platform:
        return platform
    source = conversation_id or previous_response_id
    if not source:
        return secrets.token_hex(32)[:_SESSION_ID_LENGTH]
    hint = extract_partition_key(source) or source
    reference = agent_reference or {}
    name = reference.get("name") or _DEFAULT_AGENT_NAME
    version = reference.get("version") or ""
    seed = f"{name}:{version}:{hint}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_SESSION_ID_LENGTH]


def session_header_value(
    resolved: str | None, *, env: Mapping[str, str] | None = None
) -> str | None:
    """agentserver ``_session_headers``: the ``x-agent-session-id`` value.

    POST /responses reports its resolved session; other Responses endpoints
    report ``FOUNDRY_AGENT_SESSION_ID``. ``None`` means no header.
    """
    return resolved or _env(AGENT_SESSION_ID_ENV, env) or None


def _is_responses_path(scope) -> bool:
    # Like Starlette's route path: match relative to any root_path/mount.
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root):
        path = path[len(root) :]
    return path == "/responses" or path.startswith("/responses/")


class SessionHeaderMiddleware:
    """Pure ASGI middleware setting ``x-agent-session-id`` on Responses endpoints."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not _is_responses_path(scope):
            await self.app(scope, receive, send)
            return

        async def send_with_session(message) -> None:
            if message["type"] == "http.response.start":
                state = scope.get("state") or {}
                value = session_header_value(state.get(SESSION_STATE_KEY))
                encoded = _header_bytes(value)
                if encoded is not None:
                    name = SESSION_ID_HEADER.encode("latin-1")
                    headers = [
                        (key, item)
                        for key, item in message.get("headers", [])
                        if key.lower() != name
                    ]
                    headers.append((name, encoded))
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_session)


def _header_bytes(value: str | None) -> bytes | None:
    # A client-supplied session id is arbitrary text; never emit one that is
    # not a valid header value.
    if not value or any(ch in value for ch in "\r\n\0"):
        return None
    try:
        return value.encode("latin-1")
    except UnicodeEncodeError:
        return None

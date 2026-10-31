"""Foundry memory-snapshot lifecycle endpoints.

Foundry may snapshot an idle hosted-agent process and later restore it for a
session. Around that, the platform calls ``POST /_agent/before-snapshot`` and
``POST /_agent/after-restore``. Castia has no process state to release, so the
endpoints only validate the request, pin the restored session, and apply the
sparse environment overrides. The contract mirrors
``azure-ai-agentserver-core`` 2.3.0 (``AgentServerHost``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

BEFORE_SNAPSHOT_PATHS = ("/_agent/before-snapshot", "/_before_snapshot")
AFTER_RESTORE_PATHS = ("/_agent/after-restore", "/_after_restore")

_SESSION_ID_ENV = "FOUNDRY_AGENT_SESSION_ID"
_SESSION_GUID_ENV = "FOUNDRY_AGENT_SESSION_GUID"
_HOSTING_ENV = "FOUNDRY_HOSTING_ENVIRONMENT"
_SESSION_GUID = re.compile(r"[0-9a-f]{32}")


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _ok() -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _read_json_object(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise ValueError("content type must be application/json")
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("body must be a JSON object")
    return payload


def _parse_session_context(payload: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    context = payload.get("session_context")
    if not isinstance(context, dict):
        raise TypeError("session_context must be a JSON object")
    session_id = context.get("session_id")
    restore_id = context.get("restore_id")
    overrides = context.get("session_env_overrides", {})
    if not isinstance(session_id, str) or not session_id or "\0" in session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(restore_id, str) or not restore_id:
        raise ValueError("restore_id must be a non-empty string")
    if not isinstance(overrides, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in overrides.items()
    ):
        raise ValueError("session_env_overrides must map strings to strings")
    if any(
        not key or "=" in key or "\0" in key or "\0" in value
        for key, value in overrides.items()
    ):
        raise ValueError("session_env_overrides contains an invalid entry")
    if overrides.get(_SESSION_ID_ENV, session_id) != session_id:
        raise ValueError("FOUNDRY_AGENT_SESSION_ID must match session_id")
    guid = overrides.get(_SESSION_GUID_ENV)
    if guid and os.environ.get(_HOSTING_ENV) and not _SESSION_GUID.fullmatch(guid):
        raise ValueError("FOUNDRY_AGENT_SESSION_GUID must be a 32-character hex GUID")
    return session_id, restore_id, dict(overrides)


class SnapshotLifecycle:
    """Per-app snapshot state: one assigned session, idempotent cycles."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.assigned_session_id: str | None = None
        self.completed_restore_ids: set[str] = set()
        self.before_snapshot_completed = False

    async def before_snapshot(self, request: Request) -> JSONResponse:
        try:
            await _read_json_object(request)
        except (TypeError, ValueError):
            return _error(
                "invalid_before_snapshot_request",
                "The before-snapshot request is invalid.",
                400,
            )
        async with self.lock:
            self.before_snapshot_completed = True
        return _ok()

    async def after_restore(self, request: Request) -> JSONResponse:
        try:
            session_id, restore_id, overrides = _parse_session_context(
                await _read_json_object(request)
            )
        except (TypeError, ValueError):
            return _error(
                "invalid_after_restore_request",
                "The after-restore request is invalid.",
                400,
            )
        async with self.lock:
            if self.assigned_session_id not in (None, session_id):
                return _error(
                    "session_mismatch",
                    "The restored process is already assigned to another session.",
                    409,
                )
            if restore_id in self.completed_restore_ids:
                return _ok()
            environment = {_SESSION_ID_ENV: session_id, **overrides}
            previous = {key: os.environ.get(key) for key in environment}
            try:
                os.environ.update(environment)
            except (OSError, ValueError):
                logger.exception("Applying the restored session environment failed")
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                return _error(
                    "after_restore_failed", "The after-restore hook failed.", 500
                )
            self.assigned_session_id = session_id
            self.completed_restore_ids.add(restore_id)
            self.before_snapshot_completed = False
        return _ok()


def register_snapshot_routes(app: FastAPI) -> SnapshotLifecycle:
    """Mount the snapshot lifecycle endpoints (and their legacy aliases)."""
    lifecycle = SnapshotLifecycle()
    for path in BEFORE_SNAPSHOT_PATHS:
        app.add_api_route(path, lifecycle.before_snapshot, methods=["POST"])
    for path in AFTER_RESTORE_PATHS:
        app.add_api_route(path, lifecycle.after_restore, methods=["POST"])
    return lifecycle

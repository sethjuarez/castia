"""Opt-in local developer diagnostics for the most recent Castia turn."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

_ENABLED_ENV = "CASTIA_DEV_DIAGNOSTICS"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_MAX_SUMMARY = 500

_CURRENT: ContextVar[dict[str, Any] | None] = ContextVar(
    "castia_dev_diagnostics_current",
    default=None,
)
_LAST_TURN: dict[str, Any] | None = None


def enabled() -> bool:
    """Return whether local turn diagnostics are enabled for this process."""
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in _TRUE_VALUES


def setting_name() -> str:
    return _ENABLED_ENV


def last_turn() -> dict[str, Any] | None:
    return deepcopy(_LAST_TURN)


@contextmanager
def turn(protocol: str, input_text: str):
    """Capture one local request if diagnostics are enabled."""
    if not enabled():
        yield None
        return

    started = time.time()
    record: dict[str, Any] = {
        "protocol": protocol,
        "started_at": started,
        "completed_at": None,
        "duration_ms": None,
        "input": input_text,
        "tool_calls": [],
        "final_output": None,
        "error": None,
    }
    token = _CURRENT.set(record)
    global _LAST_TURN
    try:
        yield record
    except Exception as exc:
        record["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raise
    finally:
        completed = time.time()
        record["completed_at"] = completed
        record["duration_ms"] = round((completed - started) * 1000)
        _LAST_TURN = deepcopy(record)
        _CURRENT.reset(token)


def record_output(text: object) -> None:
    current = _CURRENT.get()
    if current is None:
        return
    current["final_output"] = "" if text is None else str(text)


def record_tool_call(
    *,
    name: str | None,
    arguments: object = None,
    status: str = "requested",
    kind: str = "function",
    summary: object = None,
    error_type: str | None = None,
) -> dict[str, Any] | None:
    current = _CURRENT.get()
    if current is None:
        return None
    call: dict[str, Any] = {
        "kind": kind,
        "name": name or "unknown",
        "status": status,
    }
    if arguments is not None:
        call["arguments"] = arguments
    if summary is not None:
        call["summary"] = _summarize(summary)
    if error_type:
        call["error_type"] = error_type
    current["tool_calls"].append(call)
    return call


def update_tool_call(
    call: dict[str, Any] | None,
    *,
    status: str,
    summary: object = None,
    error_type: str | None = None,
) -> None:
    if call is None:
        return
    call["status"] = status
    if summary is not None:
        call["summary"] = _summarize(summary)
    if error_type:
        call["error_type"] = error_type


def record_response_tool_outputs(output: object) -> None:
    """Capture server-side tool/MCP output items from a Responses result."""
    if _CURRENT.get() is None:
        return
    for item in output or []:
        item_type = str(getattr(item, "type", "") or "")
        if "tool" not in item_type and "mcp" not in item_type:
            continue
        name = getattr(item, "name", None) or getattr(item, "server_label", None)
        error = getattr(item, "error", None)
        record_tool_call(
            name=str(name or item_type),
            kind=item_type,
            status="error" if error else "ok",
            summary=error or getattr(item, "output", None) or getattr(item, "result", None),
            error_type=type(error).__name__ if error is not None else None,
        )


def _summarize(value: object) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) > _MAX_SUMMARY:
        return text[:_MAX_SUMMARY] + "..."
    return text

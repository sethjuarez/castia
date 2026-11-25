"""Tool-error gate for evaluation runs.

An eval run whose tools are failing measures the outage, not the agent. When
the knowledge-base MCP tool started returning HTTP 402 (PaymentRequired),
accuracy dropped 13 points and the run still looked valid. :func:`tool_error_gate`
counts tool errors across a run's evidence and marks the run invalid when the
error rate exceeds a threshold, so harnesses can discard or rerun it instead of
reporting a regression.

The function is pure; it accepts any mix of:

* ``castia.tool_capture/v1`` documents (``tool_calls`` lists),
* span or :class:`~castia.observe.tracing.TraceRecord` dicts with ``attributes``
  where ``gen_ai.operation.name == "execute_tool"``,
* normalized calls from :func:`castia.observe.genai.server_tool_calls`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_ERROR_RATE = 0.1


@dataclass(frozen=True)
class ToolErrorGate:
    """Tool error counts for one eval run and whether the run is valid."""

    total: int
    errors: int
    error_rate: float
    max_error_rate: float
    min_calls: int
    valid: bool
    reason: str | None = None
    by_type: dict[str, int] = field(default_factory=dict)
    by_tool: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "castia.tool_error_gate/v1",
            "total": self.total,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "max_error_rate": self.max_error_rate,
            "min_calls": self.min_calls,
            "valid": self.valid,
            "reason": self.reason,
            "by_type": dict(self.by_type),
            "by_tool": dict(self.by_tool),
        }


def tool_error_gate(
    records: Iterable[Mapping[str, Any]],
    *,
    max_error_rate: float = DEFAULT_MAX_ERROR_RATE,
    min_calls: int = 0,
) -> ToolErrorGate:
    """Count tool errors in ``records`` and decide whether the run is valid.

    A run is invalid when ``errors / total > max_error_rate``
    (``tool_error_rate_exceeded``) or when fewer than ``min_calls`` tool calls
    were observed (``insufficient_tool_calls``, which catches tools silently
    dropped from the agent). A run with no tool calls and ``min_calls=0`` is valid.
    """
    if not 0 <= max_error_rate <= 1:
        raise ValueError("max_error_rate must be between 0 and 1")
    if min_calls < 0:
        raise ValueError("min_calls must be >= 0")
    total = 0
    by_type: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    for record in records:
        for name, error_type in _tool_outcomes(record):
            total += 1
            if error_type:
                by_type[error_type] += 1
                by_tool[name] += 1
    errors = sum(by_type.values())
    error_rate = errors / total if total else 0.0
    reason = None
    if total < min_calls:
        reason = "insufficient_tool_calls"
    elif error_rate > max_error_rate:
        reason = "tool_error_rate_exceeded"
    return ToolErrorGate(
        total=total,
        errors=errors,
        error_rate=error_rate,
        max_error_rate=max_error_rate,
        min_calls=min_calls,
        valid=reason is None,
        reason=reason,
        by_type=dict(sorted(by_type.items())),
        by_tool=dict(sorted(by_tool.items())),
    )


def _tool_outcomes(record: Mapping[str, Any]) -> Iterable[tuple[str, str | None]]:
    if not isinstance(record, Mapping):
        return
    calls = record.get("tool_calls")
    if isinstance(calls, list):
        for call in calls:
            if isinstance(call, Mapping):
                yield _capture_outcome(call)
        return
    attributes = record.get("attributes")
    if isinstance(attributes, Mapping):
        if attributes.get("gen_ai.operation.name") == "execute_tool":
            yield _span_outcome(record, attributes)
        return
    if "status" in record and ("item_type" in record or "error_type" in record):
        name = str(record.get("name") or "tool")
        yield name, (record.get("error_type") or "tool_error") if record.get(
            "status"
        ) == "error" else None


def _capture_outcome(call: Mapping[str, Any]) -> tuple[str, str | None]:
    name = str(call.get("name") or "tool")
    status = call.get("status")
    if status in ("error", "incomplete"):
        error = call.get("error")
        error_type = error.get("type") if isinstance(error, Mapping) else None
        return name, error_type or f"tool_{status}"
    if call.get("tool_reported_ok") is False:
        return name, "tool_reported_error"
    return name, None


def _span_outcome(
    record: Mapping[str, Any], attributes: Mapping[str, Any]
) -> tuple[str, str | None]:
    name = str(attributes.get("gen_ai.tool.name") or record.get("name") or "tool")
    status = record.get("status")
    if isinstance(status, Mapping):
        status = status.get("status_code") or status.get("code")
    failed = (
        str(status or "").lower() in ("error", "statuscode.error")
        or attributes.get("castia.tool.status") == "error"
        or bool(attributes.get("error.type"))
    )
    if not failed:
        failed = bool(record.get("error_type"))
    if not failed:
        return name, None
    return name, str(attributes.get("error.type") or record.get("error_type") or "tool_error")


__all__ = ["DEFAULT_MAX_ERROR_RATE", "ToolErrorGate", "tool_error_gate"]

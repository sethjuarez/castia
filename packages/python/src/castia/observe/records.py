"""Content-minimizing execution records shared by local observation tools."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class ExecutionRecord:
    timestamp: str | None = None
    agent_name: str | None = None
    agent_version: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_id: str | None = None
    source: str | None = None
    operation: str | None = None
    status: str = "unknown"
    latency_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tool_name: str | None = None
    probe_tag: str | None = None
    content: Mapping[str, Any] | None = None
    cost: float | None = None

    def __post_init__(self) -> None:
        if self.status not in {"unknown", "success", "error"}:
            raise ValueError("execution status must be unknown, success, or error")
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a nonnegative integer or null")
        for name in ("latency_ms", "cost"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{name} must be a nonnegative finite number or null")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.content is None:
            data.pop("content")
        return data


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
        return None
    return int(number) if integer else number


def _text(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _timestamp(value: Any) -> str | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_record(
    row: Mapping[str, Any], *, include_content: bool = False
) -> ExecutionRecord:
    """Normalize requests/dependencies/customEvents without inventing measurements.

    Duration in the classic App Insights tables is milliseconds. Explicit
    ``duration_ms`` also accepts workspace projections. Unknowns remain null.
    Content is allow-listed, never a wholesale copy of custom dimensions.
    """
    if not isinstance(include_content, bool):
        raise TypeError("include_content must be a boolean")
    dimensions = dict(_mapping(row.get("customDimensions") or row.get("Properties")))
    dimensions.update(_mapping(dimensions.get("attributes")))
    measurements = _mapping(row.get("customMeasurements") or row.get("Measurements"))

    def pick(*names: str) -> Any:
        for source in (row, dimensions, measurements):
            for name in names:
                if source.get(name) is not None and source[name] != "":
                    return source[name]
        return None

    success = pick("success", "Success")
    status = pick("status", "otel.status_code", "status.code")
    error = pick("error.type")
    if success is False or str(success).lower() == "false" or str(status).lower() in {"error", "failed", "failure", "2"} or error:
        normalized_status = "error"
    elif success is True or str(success).lower() == "true" or str(status).lower() in {"ok", "success", "succeeded", "1"}:
        normalized_status = "success"
    else:
        normalized_status = "unknown"
    input_tokens = _number(
        pick("input_tokens", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"),
        integer=True,
    )
    output_tokens = _number(
        pick("output_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"),
        integer=True,
    )
    total_tokens = _number(pick("total_tokens", "gen_ai.usage.total_tokens"), integer=True)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    content = None
    if include_content:
        content = {
            key: dimensions[key]
            for key in (
                "gen_ai.input.messages", "gen_ai.output.messages",
                "gen_ai.prompt", "gen_ai.completion",
            )
            if key in dimensions
        } or None
    identity = _text(pick("gen_ai.agent.id"))
    name = _text(pick(
        "agent_name", "azure.ai.agentserver.agent_name", "gen_ai.agent.name",
        "service.name", "cloud_RoleName",
    ))
    version = _text(row["agent_version"]) if "agent_version" in row else _text(pick(
        "agent_version", "azure.ai.agentserver.agent_version", "gen_ai.agent.version",
        "service.version",
    ))
    if identity and ":" in identity:
        identity_name, identity_version = identity.rsplit(":", 1)
        name = name or identity_name
        if "agent_version" not in row:
            version = version or identity_version
    return ExecutionRecord(
        timestamp=_timestamp(pick("timestamp", "TimeGenerated")),
        agent_name=name,
        agent_version=version,
        trace_id=_text(pick("trace_id", "operation_Id", "OperationId")),
        span_id=_text(pick("span_id", "id", "Id")),
        parent_id=_text(pick("parent_id", "operation_ParentId", "ParentId")),
        source=_text(pick("source", "itemType", "Type")),
        operation=_text(pick("operation", "name", "Name")),
        status=normalized_status,
        latency_ms=_number(pick("latency_ms", "duration_ms", "duration", "DurationMs")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        tool_name=_text(pick("tool_name", "gen_ai.tool.name")),
        probe_tag=_text(pick("probe_tag", "castia.probe_tag", "castia.probe_id", "probe_id")),
        content=content,
        # No pricing inference: sampled spans cannot establish a billed amount.
        cost=None,
    )


def summarize(records: Iterable[ExecutionRecord]) -> dict[str, Any]:
    """Summarize observed spans, not billed requests or complete agent turns."""
    rows = list(records)
    known_status = [row for row in rows if row.status != "unknown"]
    errors = sum(row.status == "error" for row in rows)
    latencies = sorted(row.latency_ms for row in rows if row.latency_ms is not None)

    def percentile(fraction: float) -> float | None:
        if not latencies:
            return None
        position = (len(latencies) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        return latencies[lo] + (latencies[hi] - latencies[lo]) * (position - lo)

    usage = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
        usage[field] = {
            "observed_sum": sum(values) if values else None,
            "known_records": len(values),
            "unknown_records": len(rows) - len(values),
        }
    return {
        "record_count": len(rows),
        "error_count": errors if known_status else None,
        "observed_error_count": errors,
        "known_status_count": len(known_status),
        "unknown_status_count": len(rows) - len(known_status),
        "error_rate": errors / len(known_status) if known_status else None,
        "error_rate_denominator": "known_status_records",
        "latency_ms": {
            "count": len(latencies),
            "unknown_count": len(rows) - len(latencies),
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "p50": percentile(.50),
            "p95": percentile(.95),
            "p99": percentile(.99),
        },
        "tool_errors": {
            name: (
                sum(row.status == "error" and row.tool_name == name for row in rows)
                if any(row.tool_name == name and row.status != "unknown" for row in rows)
                else None
            )
            for name in sorted({row.tool_name for row in rows if row.tool_name})
        },
        "tool_status_counts": {
            name: {
                "known": sum(row.tool_name == name and row.status != "unknown" for row in rows),
                "unknown": sum(row.tool_name == name and row.status == "unknown" for row in rows),
            }
            for name in sorted({row.tool_name for row in rows if row.tool_name})
        },
        "usage": usage,
        "cost": None,
        "cost_status": "unknown",
        "scope": "observed_spans_may_be_sampled_or_duplicate_usage",
    }

"""Synthesize ``execute_tool`` spans for provider-executed (server-side) tools.

When a Responses call carries a native MCP/toolbox tool, the provider runs the
tool and only reports it afterwards as an ``mcp_call`` (or other ``*_call``)
item in ``response.output``. No local code wraps that execution, so without
help Monitor and trace-based evaluations never see the tool call or its error.

:func:`record_server_tools` closes that gap: for every server tool item it emits
an ``execute_tool {name}`` child span of the current span, with error status
and ``error.type`` when the provider reported a failure, and records the same
calls in the opt-in ``castia.tool_capture/v1`` document linked by span id.

Spans are synthesized after the response returns, so their timing spans the
whole model request rather than the tool's own execution. They carry
``castia.tool.synthesized=true`` and ``castia.tool.timing=request_window``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

from castia.observe.genai import (
    GENAI_SEMCONV_SCHEMA_URL,
    server_tool_calls,
    server_tool_span,
)

_logger = logging.getLogger("castia.observe.server_tools")
_MAX_CONTENT_CHARS = 16 * 1024


def record_server_tools(
    output: Iterable[Any] | None,
    *,
    iteration: int | None = None,
    started_ns: int | None = None,
    system: str | None = None,
) -> list[dict[str, Any]]:
    """Emit spans and tool-capture entries for server tool items. Never raises.

    ``started_ns`` is the ``time.time_ns()`` taken before the model request; when
    omitted the spans are zero-length at the time of this call. Returns the
    normalized calls (see :func:`castia.observe.genai.server_tool_calls`).
    """
    try:
        items = list(output or [])
        calls = server_tool_calls(items)
    except Exception:
        _logger.debug("Could not normalize server tool output", exc_info=True)
        return []
    spans: list[Any] = []
    if calls:
        spans = _emit_spans(calls, iteration=iteration, started_ns=started_ns, system=system)
    try:
        from castia.observe import tool_capture

        tool_capture.record_server_tool_outputs(items, iteration=iteration, spans=spans)
    except Exception:
        _logger.debug("Could not capture server tool output", exc_info=True)
    return calls


def _emit_spans(
    calls: list[dict[str, Any]],
    *,
    iteration: int | None,
    started_ns: int | None,
    system: str | None,
) -> list[Any]:
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        from castia.observe.tracing import content_recording_enabled

        tracer = trace.get_tracer("castia", schema_url=GENAI_SEMCONV_SCHEMA_URL)
        include_content = content_recording_enabled()
    except Exception:
        _logger.debug("OpenTelemetry is unavailable for server tool spans", exc_info=True)
        return []
    ended_ns = time.time_ns()
    start_ns = started_ns if started_ns is not None and started_ns <= ended_ns else ended_ns
    spans: list[Any] = []
    for call in calls:
        try:
            shape = server_tool_span(
                call, system=system, iteration=iteration, include_content=include_content
            )
            attributes = {
                key: _bounded(value)
                for key, value in shape["attributes"].items()
                if value is not None
            }
            attributes["castia.tool.timing"] = (
                "request_window" if started_ns is not None else "response_time"
            )
            span = tracer.start_span(
                shape["span_name"], attributes=attributes, start_time=start_ns
            )
            if shape["status"] == "error":
                span.set_status(Status(StatusCode.ERROR, shape["status_description"]))
            else:
                span.set_status(Status(StatusCode.OK))
            span.end(end_time=ended_ns)
            spans.append(span)
        except Exception:
            _logger.debug("Could not emit server tool span", exc_info=True)
            spans.append(None)
    return spans


def _bounded(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_CONTENT_CHARS:
        return value[:_MAX_CONTENT_CHARS] + "...[truncated]"
    return value


__all__ = ["record_server_tools"]

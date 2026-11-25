"""Client-side trace correlation and human evaluation results.

Two gaps this closes:

* A client calling a hosted agent must send ``traceparent`` so the agent's
  ``invoke_agent`` span joins the client's trace. :func:`inject_traceparent`
  adds it to a header dict (from the current OpenTelemetry context, or a fresh
  random one when the client is not instrumented), and
  :func:`trace_http_client` installs it as an ``httpx`` request hook.
* Human reviews must land next to the trace they judge.
  :func:`record_human_evaluation` emits a ``gen_ai.evaluation.result`` event
  parented to the reviewed span, so Monitor and evals can join scores to traces.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping, MutableMapping
from typing import Any

from castia.observe.genai import (
    EVALUATION_RESULT_EVENT,
    GENAI_SEMCONV_SCHEMA_URL,
    human_evaluation_attributes,
    parse_traceparent,
)

_logger = logging.getLogger("castia.observe.correlation")
_TRACEPARENT = "traceparent"


def _has_traceparent(headers: Mapping[str, str]) -> bool:
    return any(key.lower() == _TRACEPARENT for key in headers)


def inject_traceparent(
    headers: MutableMapping[str, str] | None = None,
    *,
    create_if_missing: bool = True,
) -> MutableMapping[str, str]:
    """Return ``headers`` carrying a W3C ``traceparent`` (and ``tracestate``).

    An existing ``traceparent`` (any case) is kept. Otherwise the active
    OpenTelemetry context is injected; when there is none and
    ``create_if_missing`` is true, a random sampled ``traceparent`` is added so
    the server-side trace still has a client-known id to search for.
    """
    headers = {} if headers is None else headers
    if _has_traceparent(headers):
        return headers
    try:
        from opentelemetry import propagate

        propagate.inject(headers)
    except Exception:
        _logger.debug("OpenTelemetry propagation unavailable", exc_info=True)
    if create_if_missing and not _has_traceparent(headers):
        headers[_TRACEPARENT] = new_traceparent()
    return headers


def new_traceparent() -> str:
    """Return a random, sampled W3C ``traceparent``."""
    while True:
        trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
        if parse_traceparent(f"00-{trace_id}-{span_id}-01"):
            return f"00-{trace_id}-{span_id}-01"


def trace_http_client(client: Any, *, create_if_missing: bool = True) -> Any:
    """Add a request hook that injects ``traceparent`` into an ``httpx`` client.

    Works for ``httpx.Client`` and ``httpx.AsyncClient``; returns the client.
    Requests that already carry ``traceparent`` are left untouched.
    """

    def _inject(request: Any) -> None:
        if _TRACEPARENT in request.headers:
            return
        for key, value in inject_traceparent(
            {}, create_if_missing=create_if_missing
        ).items():
            request.headers.setdefault(key, value)

    async def _inject_async(request: Any) -> None:
        _inject(request)

    hooks = dict(client.event_hooks)
    hook = _inject_async if _is_async_client(client) else _inject
    hooks["request"] = [*hooks.get("request", []), hook]
    client.event_hooks = hooks
    return client


def record_human_evaluation(
    name: str,
    *,
    score: float | None = None,
    label: str | None = None,
    explanation: str | None = None,
    response_id: str | None = None,
    traceparent: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    agent_id: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Emit a ``gen_ai.evaluation.result`` event for a human review.

    The event is parented to the reviewed span given as ``traceparent`` (or
    ``trace_id`` + ``span_id``); without either it joins the current span.
    Exported only when an OpenTelemetry logger provider is installed (the
    Azure Monitor distro and Foundry hosting install one). Returns the
    event attributes. Raises :class:`ValueError` for an invalid evaluation or
    trace reference.
    """
    attributes = human_evaluation_attributes(
        name,
        score=score,
        label=label,
        explanation=explanation,
        response_id=response_id,
        agent_id=agent_id,
        reviewer=reviewer,
    )
    context = _parent_context(traceparent, trace_id, span_id)
    try:
        from opentelemetry._logs import SeverityNumber, get_logger

        logger = get_logger("castia", schema_url=GENAI_SEMCONV_SCHEMA_URL)
        logger.emit(
            event_name=EVALUATION_RESULT_EVENT,
            context=context,
            severity_number=SeverityNumber.INFO,
            body=None,
            attributes=attributes,
        )
    except Exception:
        _logger.debug("Could not emit human evaluation event", exc_info=True)
    return attributes


def _parent_context(
    traceparent: str | None, trace_id: str | None, span_id: str | None
) -> Any:
    if traceparent:
        parsed = parse_traceparent(traceparent)
        if parsed is None:
            raise ValueError(f"invalid traceparent: {traceparent!r}")
        trace_id, span_id, sampled = parsed
    elif trace_id or span_id:
        reference = f"00-{(trace_id or '').lower()}-{(span_id or '').lower()}-01"
        if not (trace_id and span_id) or parse_traceparent(reference) is None:
            raise ValueError("trace_id must be 32 hex chars and span_id 16 hex chars")
        sampled = True
    else:
        return None
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    span_context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context))


def _is_async_client(client: Any) -> bool:
    try:
        import httpx

        return isinstance(client, httpx.AsyncClient)
    except ImportError:  # pragma: no cover - httpx is a core dependency
        return False


__all__ = [
    "inject_traceparent",
    "new_traceparent",
    "record_human_evaluation",
    "trace_http_client",
]

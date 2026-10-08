"""GenAI operation spans that the Foundry Traces UI renders with a label.

The Foundry Traces tree labels a span from its ``gen_ai.operation.name``
attribute: ``chat`` shows as **Chat**, ``invoke_agent`` as **Agent**,
``execute_tool`` as **Tool**, and so on. A span without that attribute -- every
transport/HTTP/auth span an agent emits -- falls into the generic **Other**
bucket. The label vocabulary is closed: the UI recognises only the values in
:class:`OperationName`, assigns each its own colour/icon automatically, and
gives no way to define a custom label or colour. An unrecognised value renders
as **Other**, exactly like an unset one.

These helpers wrap a block of work in a correctly-shaped GenAI span so it reads
as a first-class step in the portal. The model call already emits its own
``chat`` span via the instrumentor enabled in :mod:`castia.observe.configuration`;
:func:`invoke_agent` gives that call an **Agent** parent, and
:func:`execute_tool` labels a tool/function call **Tool**.

    from castia import invoke_agent, execute_tool

    with invoke_agent():                 # -> "Agent"
        with execute_tool("get_weather"):  # -> "Tool"
            ...
        answer = await model.respond(text)  # model call -> "Chat" (nested)
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

#: ``gen_ai.provider.name`` / ``gen_ai.system`` value for Foundry-hosted models.
#: Matches what the model call's own ``chat`` span carries, so parent and child
#: agree on the provider.
PROVIDER = "microsoft.foundry"

_TRACER_NAME = "castia"
_CONTENT_RECORDING_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_TRACE_INFRASTRUCTURE_ENV = "CASTIA_OTEL_TRACE_INFRASTRUCTURE"
_SPAN_FILTER_NAME_ENV = "CASTIA_OTEL_SUPPRESS_SPAN_NAME_CONTAINS"
_SPAN_FILTER_TARGET_ENV = "CASTIA_OTEL_SUPPRESS_SPAN_TARGET_CONTAINS"
_SPAN_FILTER_MAX_DURATION_ENV = "CASTIA_OTEL_SUPPRESS_SPAN_MAX_DURATION_MS"
_DEFAULT_SPAN_FILTER_MAX_DURATION_MS = 2000.0
_logger = logging.getLogger("agent")
TraceSink = Callable[["TraceRecord"], None]
SpanFilter = Callable[[Any], bool]
_TRACE_SINKS: dict[str, TraceSink] = {}
_TRACE_SINK_LOCK = threading.RLock()
_SPAN_FILTERS: dict[str, SpanFilter] = {}
_SPAN_FILTER_LOCK = threading.RLock()
_CURRENT_STEP: ContextVar[_ActiveTraceStep | None] = ContextVar(
    "castia_current_trace_step", default=None
)
_EMITTING_TRACE_RECORD: ContextVar[bool] = ContextVar(
    "castia_emitting_trace_record", default=False
)
_AGENT_INVOCATION_ACTIVE: ContextVar[bool] = ContextVar(
    "castia_agent_invocation_active", default=False
)


@dataclass(frozen=True)
class TraceRecord:
    """Completed local trace span emitted to Castia trace sinks."""

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str
    status: str
    started_at: str
    ended_at: str
    duration_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
        }
        if self.error_type:
            data["error_type"] = self.error_type
        if self.error_message:
            data["error_message"] = self.error_message
        return data


@dataclass
class _ActiveTraceStep:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str
    started_monotonic: float
    started_at: str
    attributes: dict[str, Any]
    token: Token[_ActiveTraceStep | None] | None = None


class _TraceStep:
    def __init__(
        self,
        name: str,
        *,
        kind: str,
        attributes: dict[str, Any] | None = None,
        include_args: bool = False,
        include_result: bool = False,
        record_error_message: bool = True,
    ) -> None:
        self._name = name
        self._kind = kind
        self._attributes = dict(attributes or {})
        self._include_args = include_args
        self._include_result = include_result
        self._record_error_message = record_error_message
        self._active: _ActiveTraceStep | None = None

    def __enter__(self) -> Self:
        if self._active is not None:
            raise RuntimeError("Trace step context managers are not re-entrant.")
        parent = _CURRENT_STEP.get()
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        try:
            attributes = _json_safe_mapping(self._attributes)
        except Exception:
            _logger.debug("Could not sanitize Castia trace attributes", exc_info=True)
            attributes = {}
        active = _ActiveTraceStep(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex,
            parent_id=parent.span_id if parent else None,
            name=self._name,
            kind=self._kind,
            started_monotonic=time.perf_counter(),
            started_at=_utc_now_iso(),
            attributes=attributes,
        )
        active.token = _CURRENT_STEP.set(active)
        self._active = active
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, _tb: object) -> bool:
        active = self._active
        if active is None:
            return False
        if active.token is not None:
            try:
                _CURRENT_STEP.reset(active.token)
            except ValueError:
                _logger.debug("Could not reset Castia trace context", exc_info=True)
        self._active = None
        try:
            ended_at = _utc_now_iso()
            duration_ms = round(
                (time.perf_counter() - active.started_monotonic) * 1000, 3
            )
            cancelled = isinstance(exc, (GeneratorExit, asyncio.CancelledError))
            status = "cancelled" if cancelled else "error" if exc is not None else "ok"
            record = TraceRecord(
                trace_id=active.trace_id,
                span_id=active.span_id,
                parent_id=active.parent_id,
                name=active.name,
                kind=active.kind,
                status=status,
                started_at=active.started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                attributes=_json_clone(active.attributes),
                error_type=type(exc).__name__ if exc and not cancelled else None,
                error_message=(
                    str(exc)
                    if exc and not cancelled and self._record_error_message
                    else None
                ),
            )
        except Exception:
            _logger.debug("Could not build Castia trace record", exc_info=True)
            return False
        _emit_trace_record(record)
        return False

    def __call__(self, func: Any) -> Any:
        if inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func):
            raise TypeError("trace_step does not support generator functions.")
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _TraceStep(
                    self._name,
                    kind=self._kind,
                    attributes=_call_attributes(
                        self._attributes,
                        func,
                        args,
                        kwargs,
                        include_args=self._include_args,
                    ),
                    include_result=self._include_result,
                    record_error_message=self._record_error_message,
                ):
                    result = await func(*args, **kwargs)
                    if self._include_result:
                        trace_attribute("result", result, content=True)
                    return result

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _TraceStep(
                self._name,
                kind=self._kind,
                attributes=_call_attributes(
                    self._attributes,
                    func,
                    args,
                    kwargs,
                    include_args=self._include_args,
                ),
                include_result=self._include_result,
                record_error_message=self._record_error_message,
            ):
                result = func(*args, **kwargs)
                if self._include_result:
                    trace_attribute("result", result, content=True)
                return result

        return wrapper


@dataclass(frozen=True)
class HttpClientSpan:
    """Active outbound HTTP span state and headers derived from that span."""

    span: Any
    headers: dict[str, str]

    def set_response(self, status_code: int | None) -> None:
        if status_code is None:
            return
        try:
            self.span.set_attribute("http.status_code", status_code)
            self.span.set_attribute("http.response.status_code", status_code)
        except Exception:
            _logger.debug("Could not attach HTTP response status", exc_info=True)


@contextmanager
def http_client_span(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    attributes: dict[str, Any] | None = None,
    suppress_auto_instrumentation: bool = True,
) -> Iterator[HttpClientSpan]:
    """Create a manual outbound HTTP client span and derived propagation headers.

    The yielded headers carry the W3C context for this span, not its parent. When
    an upstream Foundry toolbox honors ``leaf_customer_span_id`` it should parent
    its remote work to this explicit POST dependency.
    """
    from opentelemetry import propagate, trace
    from opentelemetry.trace import SpanKind

    normalized_method = str(method or "HTTP").upper()
    parsed = urlparse(url)
    path = parsed.path or "/"
    span_name = f"{normalized_method} {path}"
    span_attributes: dict[str, Any] = {
        "http.method": normalized_method,
        "http.request.method": normalized_method,
        "http.url": url,
        "url.full": url,
        "url.path": path,
        "castia.telemetry.source": "castia",
    }
    if parsed.scheme:
        span_attributes["url.scheme"] = parsed.scheme
    if parsed.hostname:
        span_attributes["server.address"] = parsed.hostname
    if parsed.port:
        span_attributes["server.port"] = parsed.port
    if attributes:
        span_attributes.update(attributes)

    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        span_name,
        kind=SpanKind.CLIENT,
        attributes=_otel_span_attributes(span_attributes),
    ) as span:
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        injected = dict(headers or {})
        injected.update(carrier)
        traceparent = carrier.get("traceparent")
        if traceparent:
            injected["leaf_customer_span_id"] = traceparent
        with _maybe_suppress_auto_instrumentation(suppress_auto_instrumentation):
            yield HttpClientSpan(span=span, headers=injected)


@contextmanager
def _maybe_suppress_auto_instrumentation(enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return
    try:
        from opentelemetry import context
        from opentelemetry.instrumentation.utils import _SUPPRESS_INSTRUMENTATION_KEY
    except (AttributeError, ImportError):
        yield
        return
    token = context.attach(context.set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
    try:
        yield
    finally:
        context.detach(token)


def trace_step(
    name: str | Any | None = None,
    *,
    kind: str = "step",
    attributes: dict[str, Any] | None = None,
    include_args: bool = False,
    include_result: bool = False,
    record_error_message: bool = True,
) -> _TraceStep | Any:
    """Trace a local Castia step as a context manager or decorator.

    Args/results are only recorded when explicitly requested and the existing
    GenAI content-recording environment gate is enabled.
    """
    if callable(name):
        func = name
        step = _TraceStep(
            _callable_name(func),
            kind=kind,
            attributes=attributes,
            include_args=include_args,
            include_result=include_result,
            record_error_message=record_error_message,
        )
        return step(func)
    step_name = str(name) if name else "castia.step"
    return _TraceStep(
        step_name,
        kind=kind,
        attributes=attributes,
        include_args=include_args,
        include_result=include_result,
        record_error_message=record_error_message,
    )


def trace_attribute(key: str, value: Any, *, content: bool = False) -> bool:
    """Attach an attribute to the active Castia trace step."""
    active = _CURRENT_STEP.get()
    if active is None:
        return False
    if content and not content_recording_enabled():
        return False
    try:
        active.attributes[key] = _json_safe(value)
        return True
    except Exception:
        _logger.debug("Could not attach Castia trace attribute", exc_info=True)
        return False


def register_trace_sink(name: str, sink: TraceSink) -> None:
    """Register a sink called with each completed :class:`TraceRecord`."""
    if not name or not name.strip():
        raise ValueError("Trace sink name must be non-empty.")
    if not callable(sink):
        raise TypeError("Trace sink must be callable.")
    with _TRACE_SINK_LOCK:
        _TRACE_SINKS[name] = sink


def remove_trace_sink(name: str) -> bool:
    """Remove a registered trace sink by name."""
    with _TRACE_SINK_LOCK:
        return _TRACE_SINKS.pop(name, None) is not None


def clear_trace_sinks() -> None:
    """Remove all registered trace sinks."""
    with _TRACE_SINK_LOCK:
        _TRACE_SINKS.clear()


def registered_trace_sinks() -> tuple[str, ...]:
    """Return registered sink names."""
    with _TRACE_SINK_LOCK:
        return tuple(_TRACE_SINKS)


def register_span_filter(name: str, predicate: SpanFilter) -> None:
    """Register an export-time span suppression predicate.

    Predicates receive the OpenTelemetry readable span and return ``True`` to
    drop it before export. Castia-owned semantic spans are always protected.
    """
    if not name or not name.strip():
        raise ValueError("Span filter name must be non-empty.")
    if not callable(predicate):
        raise TypeError("Span filter predicate must be callable.")
    with _SPAN_FILTER_LOCK:
        _SPAN_FILTERS[name] = predicate


def suppress_telemetry_spans(
    name: str,
    *,
    name_contains: str | list[str] | tuple[str, ...] = (),
    target_contains: str | list[str] | tuple[str, ...] = (),
    max_duration_ms: float | None = _DEFAULT_SPAN_FILTER_MAX_DURATION_MS,
) -> None:
    """Register a conservative substring-based span suppression rule."""
    names = _normalize_filter_terms(name_contains)
    targets = _normalize_filter_terms(target_contains)
    if not names and not targets:
        raise ValueError("At least one name_contains or target_contains value is required.")
    if max_duration_ms is not None and (
        isinstance(max_duration_ms, bool)
        or not isinstance(max_duration_ms, (int, float))
        or not math.isfinite(max_duration_ms)
        or max_duration_ms < 0
    ):
        raise ValueError("max_duration_ms must be a non-negative finite number or None.")

    def predicate(span: Any) -> bool:
        if _span_has_exception_evidence(span):
            return False
        duration_ms = _span_duration_ms(span)
        if max_duration_ms is not None and (
            duration_ms is None or duration_ms > max_duration_ms
        ):
            return False
        fields = _span_filter_fields(span)
        return any(term in fields["name"] for term in names) or any(
            term in target for term in targets for target in fields["targets"]
        )

    register_span_filter(name, predicate)


def remove_span_filter(name: str) -> bool:
    """Remove a registered span filter by name."""
    with _SPAN_FILTER_LOCK:
        return _SPAN_FILTERS.pop(name, None) is not None


def clear_span_filters() -> None:
    """Remove all registered span suppression filters."""
    with _SPAN_FILTER_LOCK:
        _SPAN_FILTERS.clear()


def registered_span_filters() -> tuple[str, ...]:
    """Return registered span filter names."""
    with _SPAN_FILTER_LOCK:
        return tuple(_SPAN_FILTERS)


def should_suppress_telemetry_span(span: Any) -> bool:
    """Return whether app-configured span filters should suppress ``span``."""
    if _env_flag(_TRACE_INFRASTRUCTURE_ENV, False):
        return False
    if _is_protected_semantic_span(span):
        return False
    if _env_span_filter_matches(span):
        return True
    with _SPAN_FILTER_LOCK:
        filters = tuple(_SPAN_FILTERS.items())
    for name, predicate in filters:
        try:
            if predicate(span):
                return True
        except Exception:
            _logger.debug("Castia span filter %s failed", name, exc_info=True)
    return False


def trace_context_headers(extra_headers: Any | None = None) -> dict[str, str]:
    """Return headers carrying the current OpenTelemetry trace context.

    Use when an SDK accepts per-request headers but its transport instrumentation
    does not produce a complete cross-process parent chain. Caller-supplied
    headers win for explicit overrides.
    """
    from opentelemetry import propagate

    headers: dict[str, str] = {}
    propagate.inject(headers)
    if extra_headers:
        headers.update(dict(extra_headers))
    return headers


def jsonl_trace_sink(path: str | os.PathLike[str]) -> TraceSink:
    """Create a local JSONL sink for completed trace records."""
    destination = Path(path)
    lock = threading.Lock()

    def sink(record: TraceRecord) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False, default=str)
        with lock, destination.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    return sink


def otel_trace_sink(*, tracer_name: str = "castia.local") -> TraceSink:
    """Create a sink that exports completed local trace records as OTel spans."""

    def sink(record: TraceRecord) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        attributes = _otel_record_attributes(record)
        span = trace.get_tracer(tracer_name).start_span(
            record.name,
            attributes=attributes,
            start_time=_iso_to_unix_nanos(record.started_at),
        )
        try:
            if record.status == "error":
                description = record.error_message or record.error_type
                span.set_status(Status(StatusCode.ERROR, description))
            elif record.status == "ok":
                span.set_status(Status(StatusCode.OK))
            if record.error_type:
                span.set_attribute("exception.type", record.error_type)
            if record.error_message:
                span.set_attribute("exception.message", record.error_message)
        finally:
            span.end(end_time=_iso_to_unix_nanos(record.ended_at))

    return sink


def _emit_trace_record(record: TraceRecord) -> None:
    if _EMITTING_TRACE_RECORD.get():
        return
    with _TRACE_SINK_LOCK:
        sinks = tuple(_TRACE_SINKS.items())
    token = _EMITTING_TRACE_RECORD.set(True)
    try:
        for name, sink in sinks:
            try:
                sink(replace(record, attributes=_json_clone(record.attributes)))
            except Exception:
                _logger.debug("Castia trace sink %s failed", name, exc_info=True)
    finally:
        _EMITTING_TRACE_RECORD.reset(token)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _iso_to_unix_nanos(value: str) -> int:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        timestamp = datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    delta = timestamp.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _otel_record_attributes(record: TraceRecord) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "castia.trace.id": record.trace_id,
        "castia.trace.span_id": record.span_id,
        "castia.trace.kind": record.kind,
        "castia.trace.status": record.status,
        "castia.trace.duration_ms": record.duration_ms,
        "gen_ai.system": PROVIDER,
        "gen_ai.provider.name": PROVIDER,
    }
    if record.parent_id:
        attributes["castia.trace.parent_id"] = record.parent_id
    if record.error_type:
        attributes["error.type"] = record.error_type
    for key, value in record.attributes.items():
        if key not in attributes:
            attributes[key] = _otel_attribute_value(value)
    return {key: value for key, value in attributes.items() if value is not None}


def _otel_span_attributes(values: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _otel_attribute_value(value)
        for key, value in values.items()
        if value is not None
    }


def _otel_attribute_value(value: Any) -> Any:
    if isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if (
        isinstance(value, list | tuple)
        and all(_is_otel_sequence_value(item) for item in value)
        and _is_homogeneous_otel_sequence(value)
    ):
        return list(value)
    return json.dumps(_json_safe(value), ensure_ascii=False, default=str)


def _is_otel_sequence_value(value: Any) -> bool:
    if isinstance(value, str | bool | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_homogeneous_otel_sequence(values: list[Any] | tuple[Any, ...]) -> bool:
    if not values:
        return True
    first_type = type(values[0])
    return all(type(value) is first_type for value in values)


def _normalize_filter_terms(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    else:
        values = tuple(value)
    return tuple(term.strip().lower() for term in values if term and term.strip())


def _env_filter_terms(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw:
        return ()
    return tuple(
        term.strip().lower()
        for chunk in raw.split(";")
        for term in chunk.split(",")
        if term.strip()
    )


def _env_filter_max_duration_ms() -> float | None:
    raw = os.environ.get(_SPAN_FILTER_MAX_DURATION_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_SPAN_FILTER_MAX_DURATION_MS
    if raw.strip().lower() in {"none", "off", "unlimited"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        _logger.debug(
            "Ignoring invalid %s value %r", _SPAN_FILTER_MAX_DURATION_ENV, raw
        )
        return _DEFAULT_SPAN_FILTER_MAX_DURATION_MS
    if not math.isfinite(value) or value < 0:
        _logger.debug(
            "Ignoring invalid %s value %r", _SPAN_FILTER_MAX_DURATION_ENV, raw
        )
        return _DEFAULT_SPAN_FILTER_MAX_DURATION_MS
    return value


def _env_span_filter_matches(span: Any) -> bool:
    if _env_flag(_TRACE_INFRASTRUCTURE_ENV, False):
        return False
    names = _env_filter_terms(_SPAN_FILTER_NAME_ENV)
    targets = _env_filter_terms(_SPAN_FILTER_TARGET_ENV)
    if not names and not targets:
        return False
    if _span_has_exception_evidence(span):
        return False
    max_duration_ms = _env_filter_max_duration_ms()
    duration_ms = _span_duration_ms(span)
    if max_duration_ms is not None and (
        duration_ms is None or duration_ms > max_duration_ms
    ):
        return False
    fields = _span_filter_fields(span)
    return any(term in fields["name"] for term in names) or any(
        term in target for term in targets for target in fields["targets"]
    )


def _is_protected_semantic_span(span: Any) -> bool:
    attributes = getattr(span, "attributes", {}) or {}
    operation = str(attributes.get("gen_ai.operation.name", "") or "").lower()
    telemetry_scope = str(attributes.get("castia.telemetry.scope", "") or "").lower()
    toolbox_marker = attributes.get("castia.toolbox.mcp")
    name = str(getattr(span, "name", "") or "").lower()
    operation_names = {
        OperationName.CHAT,
        OperationName.CREATE_AGENT,
        OperationName.EMBEDDINGS,
        OperationName.EXECUTE_TOOL,
        OperationName.GENERATE_CONTENT,
        OperationName.INVOKE_AGENT,
        OperationName.TEXT_COMPLETION,
    }
    return (
        operation in operation_names
        or name.startswith(tuple(f"{value} " for value in operation_names))
        or telemetry_scope in {"client_roundtrip", "toolbox_mcp_http"}
        or any(str(key).startswith("castia.trace.") for key in attributes)
        or toolbox_marker is True
    )


def _span_filter_fields(span: Any) -> dict[str, Any]:
    attributes = getattr(span, "attributes", {}) or {}
    name = str(getattr(span, "name", "") or "").lower()
    target_keys = (
        "http.url",
        "url.full",
        "http.target",
        "target",
        "server.address",
        "net.peer.name",
        "http.host",
        "network.peer.address",
    )
    return {
        "name": name,
        "targets": tuple(
            str(attributes.get(key, "") or "").lower()
            for key in target_keys
            if attributes.get(key)
        ),
    }


def _span_has_exception_evidence(span: Any) -> bool:
    attributes = getattr(span, "attributes", {}) or {}
    if any(str(key).startswith("exception.") for key in attributes):
        return True
    events = getattr(span, "events", ()) or ()
    return any(str(getattr(event, "name", "") or "").lower() == "exception" for event in events)


def _span_duration_ms(span: Any) -> float | None:
    start_time = getattr(span, "start_time", None)
    end_time = getattr(span, "end_time", None)
    if start_time is None or end_time is None:
        return None
    return (end_time - start_time) / 1_000_000


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() == "true"


def _json_safe_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in values.items()}


def _json_clone(values: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(_json_safe_mapping(values), ensure_ascii=False, default=str))


def _json_safe(value: Any, seen: set[int] | None = None) -> Any:
    seen = seen or set()
    if isinstance(value, str | int | float | bool) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    if isinstance(value, dict):
        if id(value) in seen:
            return "<recursion>"
        seen.add(id(value))
        try:
            return {str(key): _json_safe(item, seen) for key, item in value.items()}
        finally:
            seen.discard(id(value))
    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            return "<recursion>"
        seen.add(id(value))
        try:
            return [_json_safe(item, seen) for item in value]
        finally:
            seen.discard(id(value))
    try:
        json.dumps(value)
        return value
    except Exception:  # noqa: BLE001 - arbitrary user values must not break tracing
        return _safe_repr(value)


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:  # noqa: BLE001 - arbitrary __repr__ implementations may fail
        return f"<unrepresentable {type(value).__name__}>"


def _callable_name(func: Any) -> str:
    module = getattr(func, "__module__", "")
    qualname = getattr(func, "__qualname__", getattr(func, "__name__", "callable"))
    return f"{module}.{qualname}" if module else qualname


def _call_attributes(
    base: dict[str, Any],
    func: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    include_args: bool,
) -> dict[str, Any]:
    attributes = dict(base)
    attributes.setdefault("code.function", _callable_name(func))
    if include_args and content_recording_enabled():
        attributes["args"] = args
        attributes["kwargs"] = kwargs
    return attributes


def _get_tracer():
    from opentelemetry import trace

    return trace.get_tracer(_TRACER_NAME)


def content_recording_enabled() -> bool:
    """Whether content-bearing trace attributes may be emitted."""
    return os.environ.get(_CONTENT_RECORDING_ENV, "").strip().lower() == "true"


def record_turn_input(span: Any, text: str) -> None:
    """Attach the external user turn text when content recording is enabled."""
    if not content_recording_enabled() or not text:
        return
    _set_json_attribute(
        span,
        "gen_ai.input.messages",
        [{"role": "user", "content": text}],
    )


def record_turn_output(span: Any, text: str) -> None:
    """Attach the final agent answer when content recording is enabled."""
    if not content_recording_enabled() or not text:
        return
    _set_json_attribute(
        span,
        "gen_ai.output.messages",
        [{"role": "assistant", "content": text}],
    )


def _set_json_attribute(span: Any, key: str, value: object) -> None:
    try:
        span.set_attribute(key, json.dumps(value, ensure_ascii=False))
    except Exception:
        _logger.debug("Failed to set content trace attribute", exc_info=True)


class OperationName:
    """The ``gen_ai.operation.name`` values the Foundry Traces UI labels.

    This is the complete, closed set the portal recognises. Any other value is
    rendered as **Other** with no colour or icon -- the UI has no mechanism for
    custom labels or colours.
    """

    CHAT = "chat"
    INVOKE_AGENT = "invoke_agent"
    EXECUTE_TOOL = "execute_tool"
    CREATE_AGENT = "create_agent"
    EMBEDDINGS = "embeddings"
    GENERATE_CONTENT = "generate_content"
    TEXT_COMPLETION = "text_completion"


def _identity_attributes(name: str) -> dict[str, str]:
    """Agent identity attributes, mirroring the identity span processor.

    The processor stamps these on every span already; setting them here too
    keeps the helper self-contained (and correct even if reused without that
    processor) and costs nothing -- the values are identical.
    """
    attributes = {
        "gen_ai.agent.name": name,
        "castia.telemetry.source": "castia",
    }
    version = os.environ.get("FOUNDRY_AGENT_VERSION")
    if version:
        attributes["gen_ai.agent.id"] = f"{name}:{version}"
        attributes["gen_ai.agent.version"] = version
    return attributes


def is_agent_invocation_active() -> bool:
    """Return whether the current context is inside a Castia agent invocation."""
    return _AGENT_INVOCATION_ACTIVE.get()


@contextmanager
def invoke_agent(name: str | None = None, *, system: str = PROVIDER) -> Iterator[Any]:
    """Wrap a turn's work in an ``invoke_agent`` span (labels it **Agent**).

    ``name`` defaults to the platform-injected ``FOUNDRY_AGENT_NAME``. The span
    is named ``invoke_agent {name}`` to match the ``chat {model}`` convention the
    instrumentor uses, and becomes the parent of any model or tool span created
    inside the block.
    """
    agent_name = name or os.environ.get("FOUNDRY_AGENT_NAME", "agent")
    attributes = {
        "gen_ai.operation.name": OperationName.INVOKE_AGENT,
        "gen_ai.system": system,
        "gen_ai.provider.name": system,
        **_identity_attributes(agent_name),
    }
    tracer = _get_tracer()
    token = _AGENT_INVOCATION_ACTIVE.set(True)
    try:
        with tracer.start_as_current_span(
            f"{OperationName.INVOKE_AGENT} {agent_name}", attributes=attributes
        ) as span:
            yield span
    finally:
        _AGENT_INVOCATION_ACTIVE.reset(token)


@contextmanager
def execute_tool(
    name: str,
    *,
    system: str = PROVIDER,
    tool_type: str | None = None,
    call_id: str | None = None,
) -> Iterator[Any]:
    """Wrap a tool/function call in an ``execute_tool`` span (labels it **Tool**).

    ``name`` is the tool being called; the span is named ``execute_tool {name}``
    and carries ``gen_ai.tool.name`` so the portal shows which tool ran.
    """
    attributes = {
        "gen_ai.operation.name": OperationName.EXECUTE_TOOL,
        "gen_ai.system": system,
        "gen_ai.provider.name": system,
        "gen_ai.tool.name": name,
        "castia.telemetry.source": "castia",
    }
    if tool_type:
        attributes["gen_ai.tool.type"] = tool_type
    if call_id:
        attributes["gen_ai.tool.call.id"] = call_id
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        f"{OperationName.EXECUTE_TOOL} {name}", attributes=attributes
    ) as span:
        yield span

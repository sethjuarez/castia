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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

#: ``gen_ai.provider.name`` / ``gen_ai.system`` value for Foundry-hosted models.
#: Matches what the model call's own ``chat`` span carries, so parent and child
#: agree on the provider.
PROVIDER = "microsoft.foundry"

_TRACER_NAME = "castia"
_CONTENT_RECORDING_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_logger = logging.getLogger("agent")
TraceSink = Callable[["TraceRecord"], None]
_TRACE_SINKS: dict[str, TraceSink] = {}
_TRACE_SINK_LOCK = threading.RLock()
_CURRENT_STEP: ContextVar[_ActiveTraceStep | None] = ContextVar(
    "castia_current_trace_step", default=None
)
_EMITTING_TRACE_RECORD: ContextVar[bool] = ContextVar(
    "castia_emitting_trace_record", default=False
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
    ) -> None:
        self._name = name
        self._kind = kind
        self._attributes = dict(attributes or {})
        self._include_args = include_args
        self._include_result = include_result
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
            status = "error" if exc is not None else "ok"
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
                error_type=type(exc).__name__ if exc else None,
                error_message=str(exc) if exc else None,
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
            ):
                result = func(*args, **kwargs)
                if self._include_result:
                    trace_attribute("result", result, content=True)
                return result

        return wrapper


def trace_step(
    name: str | Any | None = None,
    *,
    kind: str = "step",
    attributes: dict[str, Any] | None = None,
    include_args: bool = False,
    include_result: bool = False,
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
        )
        return step(func)
    step_name = str(name) if name else "castia.step"
    return _TraceStep(
        step_name,
        kind=kind,
        attributes=attributes,
        include_args=include_args,
        include_result=include_result,
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
    attributes = {"gen_ai.agent.name": name}
    version = os.environ.get("FOUNDRY_AGENT_VERSION")
    if version:
        attributes["gen_ai.agent.id"] = f"{name}:{version}"
        attributes["gen_ai.agent.version"] = version
    return attributes


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
    with tracer.start_as_current_span(
        f"{OperationName.INVOKE_AGENT} {agent_name}", attributes=attributes
    ) as span:
        yield span


@contextmanager
def execute_tool(name: str, *, system: str = PROVIDER) -> Iterator[Any]:
    """Wrap a tool/function call in an ``execute_tool`` span (labels it **Tool**).

    ``name`` is the tool being called; the span is named ``execute_tool {name}``
    and carries ``gen_ai.tool.name`` so the portal shows which tool ran.
    """
    attributes = {
        "gen_ai.operation.name": OperationName.EXECUTE_TOOL,
        "gen_ai.system": system,
        "gen_ai.provider.name": system,
        "gen_ai.tool.name": name,
    }
    tracer = _get_tracer()
    with tracer.start_as_current_span(
        f"{OperationName.EXECUTE_TOOL} {name}", attributes=attributes
    ) as span:
        yield span

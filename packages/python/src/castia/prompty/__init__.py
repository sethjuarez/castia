"""Experimental Prompty integration for Castia agents.

Prompty is optional: install ``castia[prompty]`` before importing this module.
The existing ``castia.Model`` path remains the compatibility default; these
helpers let an app opt into Prompty as a runtime/eval harness while keeping
``.agent_configs`` as the optimizer contract.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import Parameter, isawaitable, signature
from typing import Any

import httpx

from castia.integrations.toolbox import (
    McpToolboxError,
    TokenProvider,
    ToolboxMcpClient,
    resolve_toolbox_endpoint,
    serialize_mcp_result,
)
from castia.observe import dev_diagnostics
from castia.optimizing.config import AgentConfig, load_agent_config
from castia.runtime.usage import record_response_usage

DEFAULT_FOUNDRY_CONNECTION = "foundry-default"
DEFAULT_TOOLBOX_CONNECTION = "contract-toolbox"
_CONTENT_RECORDING_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_TRACE_INTERNAL_ENV = "CASTIA_PROMPTY_TRACE_INTERNAL"
_DEFAULT_PROMPTY_SPANS = {"turn_async", "run_async"}
_TOOLBOX_TOOL_TYPE = "toolbox"
_NO_TOOLBOX_ENDPOINT_DIAGNOSTIC = (
    "No toolbox MCP endpoint was resolved. Set TOOLBOX_ENDPOINT, "
    "TOOLBOX_MCP_ENDPOINT, TOOLBOX_NAME with its platform endpoint variable, "
    "or FOUNDRY_PROJECT_ENDPOINT plus TOOLBOX_NAME."
)
_PROMPTY_SECRET_KEY_PATTERN = re.compile(
    r"secret|password|credential|passphrase|bearer|cookie|api[_.]?key|token(?!s)|auth(?!ors?\b)",
    re.IGNORECASE,
)
_logger = logging.getLogger("agent")

@dataclass
class _TurnTimeline:
    include_content: bool
    events: list[dict[str, Any]]
    _next_step: int = 1

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        event.setdefault("step", self._next_step)
        self._next_step += 1
        self.events.append(event)
        return event


_CURRENT_TIMELINE: ContextVar[_TurnTimeline | None] = ContextVar(
    "castia_prompty_timeline",
    default=None,
)


_CURRENT_ACTIVITY: ContextVar[object | None] = ContextVar(
    "castia_prompty_activity",
    default=None,
)


class PromptyIntegrationError(RuntimeError):
    """Raised when optional Prompty integration dependencies are unavailable."""


@dataclass(frozen=True)
class ToolboxPreflightResult:
    """Result of checking a Foundry toolbox MCP endpoint before model use."""

    ok: bool
    endpoint: str | None
    tool_names: tuple[str, ...] = ()
    missing_tools: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    tools: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


def _prompty():
    try:
        import prompty  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise PromptyIntegrationError(
            "Prompty support is optional. Install castia[prompty] to use "
            "castia.prompty."
        ) from exc
    return prompty


def _content_recording_enabled() -> bool:
    return os.environ.get(_CONTENT_RECORDING_ENV, "").strip().lower() == "true"


def register_prompty_otel_tracing(
    *,
    enable_content_recording: bool | None = None,
    name: str = "otel",
    tracer_name: str = "prompty",
    provider: object | None = None,
) -> bool:
    """Register Prompty's OTel backend with Castia privacy defaults.

    Prompty emits spans for the prompt pipeline. Castia always registers the
    backend so those inner spans are visible, but drops Prompty ``inputs`` and
    ``result`` attributes unless content recording is explicitly enabled or the
    existing Foundry content-recording opt-in environment variable is ``true``.
    Call this after Castia observability has been configured so the backend
    binds to the active Microsoft OTel provider.
    """
    include_content = _content_recording_enabled() if enable_content_recording is None else enable_content_recording
    include_internal = os.environ.get(_TRACE_INTERNAL_ENV, "").strip().lower() == "true"
    prompty = _prompty()
    from opentelemetry import trace as otel_trace

    prompty.Tracer.add(
        name,
        _prompty_otel_backend(
            tracer_name=tracer_name,
            provider=provider or otel_trace.get_tracer_provider(),
            include_content=include_content,
            include_internal=include_internal,
        ),
    )
    return True


def register_prompty_trace_sinks(
    *,
    enable_content_recording: bool | None = None,
    name: str = "castia",
) -> bool:
    """Register a Prompty backend that emits Castia local trace records.

    This mirrors :func:`register_prompty_otel_tracing` but targets Castia's
    pluggable local trace sink registry. It is useful when running locally with
    a JSONL/Copilot/dev sink and can be registered alongside Prompty's OTel
    backend; both consume the same Prompty trace callbacks.
    """
    include_content = _content_recording_enabled() if enable_content_recording is None else enable_content_recording
    include_internal = os.environ.get(_TRACE_INTERNAL_ENV, "").strip().lower() == "true"
    prompty = _prompty()
    prompty.Tracer.add(
        name,
        _prompty_trace_sink_backend(
            include_content=include_content,
            include_internal=include_internal,
        ),
    )
    return True


def _prompty_trace_sink_backend(*, include_content: bool, include_internal: bool):
    from castia.observe.tracing import trace_attribute, trace_step

    @contextmanager
    def tracer(span_name: str):
        if not _should_trace_prompty_span(span_name, include_internal=include_internal):
            yield _prompty_noop_add
            return
        with trace_step(
            f"prompty {span_name}",
            kind="prompty",
            attributes={"castia.prompty.span.name": span_name},
            record_error_message=include_content,
        ):

            def filtered_add(key: str, value: Any) -> None:
                if key == "result" and isinstance(value, dict) and "exception" in value:
                    _record_prompty_trace_exception(
                        value["exception"], include_content=include_content
                    )
                    return
                if key in {"inputs", "result"} and not include_content:
                    return
                trace_attribute(
                    f"castia.prompty.{key}",
                    _sanitize_prompty_trace_attribute(key, value),
                )
                if include_content:
                    _map_prompty_trace_content(key, value)

            yield filtered_add

    return tracer


def _prompty_otel_backend(
    *,
    tracer_name: str,
    provider: object,
    include_content: bool,
    include_internal: bool,
):
    import traceback

    from opentelemetry.trace import SpanKind, Status, StatusCode
    from prompty.tracing.tracer import (  # type: ignore[import-not-found]
        sanitize,
        to_dict,
    )

    @contextmanager
    def tracer(span_name: str):
        if not _should_trace_prompty_span(span_name, include_internal=include_internal):
            yield _prompty_noop_add
            return
        otel_tracer = provider.get_tracer(tracer_name)
        with otel_tracer.start_as_current_span(
            f"prompty {span_name}",
            kind=SpanKind.INTERNAL,
            attributes={"castia.prompty.span.name": span_name},
        ) as span:

            def filtered_add(key: str, value: Any) -> None:
                if key == "result" and isinstance(value, dict) and "exception" in value:
                    _record_prompty_exception(span, value["exception"])
                    return
                _record_prompty_timeline_event(key, value, include_content=include_content)
                if key in {"inputs", "result"} and not include_content:
                    return
                _set_prompty_span_attribute(span, key, sanitize(key, value), to_dict=to_dict)
                if include_content:
                    _map_prompty_content_attribute(span, key, value)

            try:
                yield filtered_add
                _set_prompty_timeline_attributes(span)
                span.set_status(Status(StatusCode.OK))
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                if exc.__traceback__:
                    span.set_attribute(
                        "exception.stacktrace",
                        "".join(traceback.format_tb(exc.__traceback__)),
                    )
                raise

    return tracer


def _should_trace_prompty_span(span_name: str, *, include_internal: bool) -> bool:
    return include_internal or span_name in _DEFAULT_PROMPTY_SPANS


def _prompty_noop_add(_key: str, _value: Any) -> None:
    return None


def _sanitize_prompty_trace_attribute(key: str, value: Any) -> Any:
    try:
        from prompty.tracing.tracer import sanitize  # type: ignore[import-not-found]
    except ImportError:
        return _fallback_sanitize_prompty_trace_attribute(key, value)
    return sanitize(key, value)


def _fallback_sanitize_prompty_trace_attribute(key: str, value: Any) -> Any:
    if _PROMPTY_SECRET_KEY_PATTERN.search(key):
        return "******"
    if isinstance(value, Mapping):
        return {
            item_key: _fallback_sanitize_prompty_trace_attribute(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [
            _fallback_sanitize_prompty_trace_attribute(key, item) for item in value
        ]
    return value


def _record_prompty_timeline_event(key: str, value: Any, *, include_content: bool) -> None:
    timeline = _CURRENT_TIMELINE.get()
    if timeline is None:
        return
    if key == "inputs":
        text = _input_text(value)
        event: dict[str, Any] = {"type": "turn_start"}
        if include_content and text:
            event["input"] = text
        timeline.append(event)
    elif key == "result":
        text = value if isinstance(value, str) else _safe_json(value)
        event = {"type": "turn_end"}
        if include_content and text:
            event["output"] = text
        timeline.append(event)


def _set_prompty_timeline_attributes(span: Any) -> None:
    timeline = _CURRENT_TIMELINE.get()
    if timeline is None or not timeline.events:
        return
    try:
        span.set_attribute("castia.turn.timeline", _safe_json(timeline.events))
        span.set_attribute("castia.turn.summary", _timeline_summary(timeline.events))
        span.set_attribute(
            "castia.turn.tool_call_count",
            sum(1 for event in timeline.events if event.get("type") == "tool"),
        )
    except Exception:
        _logger.debug("Failed to set Prompty turn timeline attributes", exc_info=True)


def _timeline_summary(events: Sequence[Mapping[str, Any]]) -> str:
    labels = []
    for event in events:
        kind = event.get("type")
        if kind == "tool":
            labels.append(f"tool:{event.get('name', 'unknown')}")
        else:
            labels.append(str(kind or "event"))
    return " -> ".join(labels)


def _set_prompty_span_attribute(span: Any, key: str, value: Any, *, to_dict: Callable[[Any], Any]) -> None:
    try:
        serialized = to_dict(value)
        if isinstance(serialized, (dict, list)):
            span.set_attribute(f"castia.prompty.{key}", _safe_json(serialized))
        elif serialized is not None:
            span.set_attribute(f"castia.prompty.{key}", str(serialized))
    except Exception:
        _logger.debug("Failed to set Prompty trace attribute", exc_info=True)


def _record_prompty_exception(span: Any, exc_info: Mapping[str, Any]) -> None:
    from opentelemetry.trace import Status, StatusCode

    message = str(exc_info.get("message", ""))
    span.set_status(Status(StatusCode.ERROR, message))
    span.set_attribute("exception.type", str(exc_info.get("type", "")))
    span.set_attribute("exception.message", message)
    stacktrace = exc_info.get("traceback")
    if stacktrace:
        span.set_attribute(
            "exception.stacktrace",
            "".join(stacktrace) if isinstance(stacktrace, list) else str(stacktrace),
        )


def _record_prompty_trace_exception(
    exc_info: Mapping[str, Any], *, include_content: bool
) -> None:
    from castia.observe.tracing import trace_attribute

    message = str(exc_info.get("message", ""))
    trace_attribute("castia.prompty.status", "error")
    trace_attribute("castia.prompty.exception.type", str(exc_info.get("type", "")))
    if include_content:
        trace_attribute("castia.prompty.exception.message", message)


def _map_prompty_trace_content(key: str, value: Any) -> None:
    from castia.observe.tracing import trace_attribute

    if key == "inputs":
        text = _input_text(value)
        if text:
            trace_attribute(
                "gen_ai.input.messages",
                [{"role": "user", "content": text}],
            )
    elif key == "result":
        text = value if isinstance(value, str) else _safe_json(value)
        if text:
            trace_attribute(
                "gen_ai.output.messages",
                [{"role": "assistant", "content": text}],
            )


def _map_prompty_content_attribute(span: Any, key: str, value: Any) -> None:
    if key == "inputs":
        text = _input_text(value)
        if text:
            span.set_attribute(
                "gen_ai.input.messages",
                _safe_json([{"role": "user", "content": text}]),
            )
    elif key == "result":
        text = value if isinstance(value, str) else _safe_json(value)
        if text:
            span.set_attribute(
                "gen_ai.output.messages",
                _safe_json([{"role": "assistant", "content": text}]),
            )


def _input_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text
        nested_inputs = value.get("inputs")
        if isinstance(nested_inputs, Mapping):
            nested_text = nested_inputs.get("text")
            if isinstance(nested_text, str):
                return nested_text
    return None


def register_foundry_default_connection(
    *,
    name: str = DEFAULT_FOUNDRY_CONNECTION,
    endpoint: str | None = None,
    client: object | None = None,
) -> object:
    """Register a Prompty reference connection using Castia's Foundry auth path.

    Pass ``client`` in tests or advanced hosts to register an already-built
    OpenAI-compatible client. Without it, Castia builds an async
    ``AIProjectClient`` with ``DefaultAzureCredential`` and registers its
    OpenAI client under ``name``.
    """
    prompty = _prompty()
    if client is None:
        from azure.ai.projects.aio import AIProjectClient
        from azure.identity.aio import DefaultAzureCredential

        project = AIProjectClient(
            endpoint=endpoint or os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )
        client = project.get_openai_client()
    client = _TraceContextOpenAIClient(client)
    prompty.register_connection(name, client=client)
    return client


class _TraceContextResponses:
    def __init__(self, responses: object) -> None:
        self._responses = responses

    def create(self, *args: Any, **kwargs: Any) -> Any:
        from castia.observe.tracing import trace_context_headers

        kwargs["extra_headers"] = trace_context_headers(kwargs.get("extra_headers"))
        result = self._responses.create(*args, **kwargs)
        if kwargs.get("stream"):
            return result
        if isawaitable(result):
            return _record_usage_after(result)
        record_response_usage(result)
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._responses, name)


async def _record_usage_after(pending: Awaitable[Any]) -> Any:
    response = await pending
    record_response_usage(response)
    return response


class _TraceContextOpenAIClient:
    """OpenAI client proxy that injects current OTel context per Responses call."""

    def __init__(self, client: object) -> None:
        self._client = client

    @property
    def responses(self) -> _TraceContextResponses:
        return _TraceContextResponses(self._client.responses)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def prompty_agent_from_config(
    config: AgentConfig | None = None,
    *,
    name: str = "castia-prompty-agent",
    connection_name: str = DEFAULT_FOUNDRY_CONNECTION,
    tools: Sequence[object] = (),
):
    """Build an in-memory Prompty agent from Castia ``AgentConfig``.

    ``.agent_configs`` remains the optimizer source of truth; this helper simply
    projects the resolved model and instructions into Prompty's model/agent
    schema for a runtime or eval harness.
    """
    prompty = _prompty()
    from prompty.model.contracts.templates import (  # type: ignore[import-not-found]
        Jinja2Format,
        PromptyParser,
    )

    resolved = config or load_agent_config()
    return prompty.PromptAgent(
        name=name,
        inputs=[
            prompty.Property(
                name="text",
                kind="string",
                description="The external user turn text.",
                required=True,
            )
        ],
        model=prompty.Model(
            id=resolved.model,
            provider="foundry",
            api_type="responses",
            connection=prompty.ReferenceConnection(name=connection_name),
        ),
        tools=list(tools),
        template=prompty.Template(
            format=Jinja2Format(),
            parser=PromptyParser(),
        ),
        instructions="system:\n" + resolved.instructions + "\n\nuser:\n{{ text }}",
        metadata={
            "castia.agent_config_source": resolved.source,
            "castia.optimizer_contract": ".agent_configs",
        },
    )


def load_prompty_agent(path: str | os.PathLike[str], *, allowed_file_roots: Sequence[str | os.PathLike[str]] | None = None):
    """Load a sidecar ``.prompty`` file without replacing ``.agent_configs``."""
    prompty = _prompty()
    return prompty.load(str(path), allowed_file_roots=allowed_file_roots)


@dataclass
class PromptyRunner:
    """Tiny async adapter around Prompty's top-level invocation pipeline."""

    agent: object
    tool_functions: Mapping[str, Callable[..., Any]] | None = None
    max_iterations: int = 10

    async def turn(self, text: str, *, activity: object | None = None, **inputs: object) -> str:
        """Run one external user turn through Prompty and return text.

        ``activity`` is the identity-bearing turn activity handed to Castia
        :class:`~castia.inference.tools.Tool` impls (for example
        ``graph_tools()``), matching ``Model.respond_with_tools(activity=...)``.
        When omitted, the ambient Activity turn is used if there is one.
        ``activity`` is reserved and is never sent as a Prompty input.
        """
        prompty = _prompty()
        timeline = _TurnTimeline(include_content=_content_recording_enabled(), events=[])
        token = _CURRENT_TIMELINE.set(timeline)
        activity_token = _CURRENT_ACTIVITY.set(activity if activity is not None else _ambient_activity())
        try:
            result = await prompty.turn_async(
                self.agent,
                {"text": text, **inputs},
                tools=_traced_tool_functions(self.tool_functions or {}),
                max_iterations=self.max_iterations,
            )
        finally:
            _CURRENT_ACTIVITY.reset(activity_token)
            _CURRENT_TIMELINE.reset(token)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)


def _ambient_activity() -> object | None:
    from castia.runtime.context import current_turn_or_none

    turn = current_turn_or_none()
    return turn.activity if turn is not None else None


def prompty_tools_from_castia(
    tools: Sequence[Any],
) -> tuple[list[object], dict[str, Callable[..., Awaitable[str]]]]:
    """Project Castia :class:`~castia.inference.tools.Tool` s into Prompty.

    Returns ``(function_tools, tool_functions)`` for
    :func:`configured_prompty_runner`. Each callback runs ``Tool.run`` with the
    turn's ``activity`` (see :meth:`PromptyRunner.turn`), inside an
    ``execute_tool`` span whose ``gen_ai.tool.type`` is ``Tool.kind``. Results
    are JSON-encoded, as on the ``Model.respond_with_tools`` path.
    """
    prompty = _prompty()
    function_tools: list[object] = []
    tool_functions: dict[str, Callable[..., Awaitable[str]]] = {}
    for tool in tools:
        function_tools.append(
            prompty.FunctionTool(
                name=tool.name,
                description=tool.description,
                parameters=_prompty_parameters_from_input_schema(prompty, tool.parameters),
            )
        )
        tool_functions[tool.name] = _traced_tool(
            tool.name,
            _castia_tool_callback(tool),
            tool_type=getattr(tool, "kind", None) or "function",
        )
    return function_tools, tool_functions


def _castia_tool_callback(tool: Any) -> Callable[..., Awaitable[str]]:
    async def call(**arguments: Any) -> str:
        result = await tool.run(_CURRENT_ACTIVITY.get(), **arguments)
        return result if isinstance(result, str) else _safe_json(result)

    call.__name__ = tool.name
    call.__doc__ = tool.description
    return call


def _is_castia_tool(value: object) -> bool:
    from castia.inference.tools import Tool

    return isinstance(value, Tool)


_TRACED_TOOL_MARKER = "__castia_traced_tool__"


def _traced_tool_functions(tool_functions: Mapping[str, Callable[..., Any]]) -> dict[str, Callable[..., Awaitable[Any]]]:
    return {
        name: _traced_tool(name, tool_function, tool_type=_traced_tool_type(tool_function))
        for name, tool_function in tool_functions.items()
    }


def _traced_tool_type(tool_function: Callable[..., Any]) -> str | None:
    marker = getattr(tool_function, _TRACED_TOOL_MARKER, None)
    return marker[1] if marker is not None else None


def _traced_tool(
    name: str,
    tool_function: Callable[..., Any],
    *,
    tool_type: str | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap one Prompty tool callback in an ``execute_tool`` span (idempotent)."""
    marker = getattr(tool_function, _TRACED_TOOL_MARKER, None)
    if marker is not None:
        if marker[:2] == (name, tool_type):
            return tool_function
        tool_function = marker[2]

    @functools.wraps(tool_function)
    async def call_tool(*args: Any, **kwargs: Any) -> Any:
        from castia.observe.tracing import execute_tool

        timeline = _CURRENT_TIMELINE.get()
        event = _tool_timeline_event(timeline, name, args, kwargs)
        diagnostic_call = dev_diagnostics.record_tool_call(
            name=name,
            arguments=_tool_arguments(args, kwargs),
            status="running",
            kind="prompty",
        )
        span_cm = execute_tool(name, tool_type=tool_type) if tool_type else execute_tool(name)
        with span_cm as span:
            _set_tool_span_start_attributes(span, event, name, args, kwargs)
            try:
                result = tool_function(*args, **kwargs)
                if isawaitable(result):
                    result = await result
            except Exception as exc:
                _set_tool_span_error_attributes(span, event, exc)
                dev_diagnostics.update_tool_call(
                    diagnostic_call,
                    status="error",
                    summary=f"{type(exc).__name__}: {exc}",
                    error_type=type(exc).__name__,
                )
                raise
            else:
                _set_tool_span_success_attributes(span, event, result)
                dev_diagnostics.update_tool_call(
                    diagnostic_call,
                    status="ok",
                    summary=result,
                )
                return result

    setattr(call_tool, _TRACED_TOOL_MARKER, (name, tool_type, tool_function))
    return call_tool


def _tool_timeline_event(
    timeline: _TurnTimeline | None,
    name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> dict[str, Any] | None:
    if timeline is None:
        return None
    event: dict[str, Any] = {"type": "tool", "name": name, "status": "running"}
    if timeline.include_content:
        event["arguments"] = _tool_arguments(args, kwargs)
    return timeline.append(event)


def _set_tool_span_start_attributes(
    span: Any,
    event: Mapping[str, Any] | None,
    name: str,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> None:
    _safe_set_attribute(span, "castia.step.kind", "tool")
    _safe_set_attribute(span, "castia.tool.status", "running")
    _safe_set_attribute(span, "gen_ai.tool.name", name)
    if event is not None:
        _safe_set_attribute(span, "castia.step.index", int(event["step"]))
    if _content_recording_enabled():
        _safe_set_attribute(span, "gen_ai.tool.arguments", _safe_json(_tool_arguments(args, kwargs)))


def _set_tool_span_success_attributes(span: Any, event: dict[str, Any] | None, result: Any) -> None:
    text = result if isinstance(result, str) else _safe_json(result)
    _safe_set_attribute(span, "castia.tool.status", "ok")
    if event is not None:
        event["status"] = "ok"
        if event.get("arguments") is None and _content_recording_enabled():
            event["arguments"] = {}
        if _content_recording_enabled() and text:
            event["output"] = text
    if _content_recording_enabled() and text:
        _safe_set_attribute(span, "gen_ai.tool.output", text)


def _set_tool_span_error_attributes(span: Any, event: dict[str, Any] | None, exc: Exception) -> None:
    message = str(exc)
    _safe_set_attribute(span, "castia.tool.status", "error")
    _safe_set_attribute(span, "castia.tool.error_type", type(exc).__name__)
    _safe_set_attribute(span, "castia.tool.error", message)
    if event is not None:
        event["status"] = "error"
        event["error_type"] = type(exc).__name__
        event["error"] = message


def _tool_arguments(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    if args:
        arguments["args"] = list(args)
    if kwargs:
        arguments["kwargs"] = dict(kwargs)
    return arguments


def _safe_set_attribute(span: Any, key: str, value: Any) -> None:
    try:
        span.set_attribute(key, value)
    except Exception:
        _logger.debug("Failed to set Prompty tool trace attribute", exc_info=True)


def configured_prompty_runner(
    config: AgentConfig | None = None,
    *,
    prompty_path: str | os.PathLike[str] | None = None,
    connection_name: str = DEFAULT_FOUNDRY_CONNECTION,
    tools: Sequence[object] = (),
    tool_functions: Mapping[str, Callable[..., Any]] | None = None,
    max_iterations: int = 10,
    enable_otel: bool | None = None,
) -> PromptyRunner:
    """Create an experimental Prompty-backed runner.

    Use ``prompty_path`` to load a sidecar ``.prompty`` file, or omit it to build
    an in-memory agent from Castia ``AgentConfig``. ``.agent_configs`` remains
    the optimizer contract either way.

    ``tools`` accepts Prompty tool definitions and Castia
    :class:`~castia.inference.tools.Tool` s (such as ``graph_tools()``) in any
    mix; Castia tools are projected with :func:`prompty_tools_from_castia`.
    Explicit ``tool_functions`` win on a name clash.
    """
    if enable_otel is not None:
        register_prompty_otel_tracing(enable_content_recording=enable_otel)
    castia_tools = [tool for tool in tools if _is_castia_tool(tool)]
    prompty_tools = [tool for tool in tools if not _is_castia_tool(tool)]
    functions: dict[str, Callable[..., Any]] = {}
    if castia_tools:
        castia_defs, castia_functions = prompty_tools_from_castia(castia_tools)
        prompty_tools.extend(castia_defs)
        functions.update(castia_functions)
    functions.update(tool_functions or {})
    if prompty_path is not None:
        agent = load_prompty_agent(prompty_path)
        if prompty_tools:
            existing = list(getattr(agent, "tools", None) or [])
            names = {getattr(tool, "name", None) for tool in existing}
            agent.tools = [*existing, *(tool for tool in prompty_tools if getattr(tool, "name", None) not in names)]
    else:
        agent = prompty_agent_from_config(config, connection_name=connection_name, tools=prompty_tools)
    return PromptyRunner(
        agent,
        tool_functions=functions,
        max_iterations=max_iterations,
    )


class ToolboxRuntimeConfigError(PromptyIntegrationError):
    """The configured Foundry toolbox failed preflight before any model call."""


def prompty_runner_provider(
    config: AgentConfig | str | os.PathLike[str] | None = None,
    *,
    prompty_path: str | os.PathLike[str] | None = None,
    tools: Sequence[object] = (),
    toolbox: bool | Sequence[str] | None = None,
    toolbox_descriptions: Mapping[str, str] | None = None,
    connection_name: str = DEFAULT_FOUNDRY_CONNECTION,
    max_iterations: int = 10,
    enable_otel: bool | None = None,
) -> Callable[[], Awaitable[PromptyRunner]]:
    """Return an async provider that builds (once) and caches a Prompty runner.

    The first call registers the Foundry connection, loads ``config`` (an
    ``AgentConfig`` or a ``.agent_configs`` directory; ``None`` uses the
    default), optionally preflights the toolbox, and builds the runner with
    :func:`configured_prompty_runner`. Later calls return the cached runner;
    failures are not cached, so the next call retries.

    ``toolbox``: ``None`` uses the toolbox when an endpoint resolves from the
    environment, ``True`` requires it, ``False`` disables it, and a sequence
    requires exactly those tool names. Toolbox definitions come from MCP
    ``tools/list``, with descriptions taken from ``toolbox_descriptions`` or
    the config's optimizer ``tool_definitions``. A failed preflight raises
    :class:`ToolboxRuntimeConfigError` with its diagnostics. ``tools`` may mix
    Castia ``Tool`` objects and Prompty tool definitions.
    """
    cached: list[PromptyRunner] = []
    lock = asyncio.Lock()

    async def provider() -> PromptyRunner:
        if cached:
            return cached[0]
        async with lock:
            if cached:
                return cached[0]
            runner = await _build_prompty_runner(
                config,
                prompty_path=prompty_path,
                tools=tools,
                toolbox=toolbox,
                toolbox_descriptions=toolbox_descriptions,
                connection_name=connection_name,
                max_iterations=max_iterations,
                enable_otel=enable_otel,
            )
            cached.append(runner)
            return runner

    return provider


async def _build_prompty_runner(
    config: AgentConfig | str | os.PathLike[str] | None,
    *,
    prompty_path: str | os.PathLike[str] | None,
    tools: Sequence[object],
    toolbox: bool | Sequence[str] | None,
    toolbox_descriptions: Mapping[str, str] | None,
    connection_name: str,
    max_iterations: int,
    enable_otel: bool | None,
) -> PromptyRunner:
    resolved_config = config if isinstance(config, AgentConfig) else load_agent_config(config)
    toolbox_defs: list[object] = []
    toolbox_functions: dict[str, Callable[..., Any]] = {}
    use_toolbox = bool(resolve_toolbox_endpoint()) if toolbox is None else toolbox is not False
    if use_toolbox:
        allowed = None if isinstance(toolbox, bool) or toolbox is None else tuple(toolbox)
        preflight = await toolbox_preflight(allowed)
        if not preflight.ok:
            raise ToolboxRuntimeConfigError(
                " ".join(preflight.diagnostics or ("Foundry toolbox preflight failed.",))
            )
        optimized = _optimized_tool_functions(resolved_config.tool_definitions)
        descriptions = dict(toolbox_descriptions or {})
        descriptions.update({name: fn["description"] for name, fn in optimized.items() if fn.get("description")})
        toolbox_defs = toolbox_prompty_tools_from_schema(
            preflight.tools,
            allowed_tools=allowed,
            descriptions=descriptions,
        )
        for definition in toolbox_defs:
            _apply_optimized_parameter_descriptions(definition, optimized.get(getattr(definition, "name", None)))
        client = ToolboxMcpClient(preflight.endpoint)
        toolbox_functions = {
            definition.name: _toolbox_function(definition.name, client) for definition in toolbox_defs
        }
    _reject_duplicate_tool_names([*toolbox_defs, *tools])
    register_foundry_default_connection(name=connection_name)
    return configured_prompty_runner(
        resolved_config,
        prompty_path=prompty_path,
        connection_name=connection_name,
        tools=[*toolbox_defs, *tools],
        tool_functions=toolbox_functions,
        max_iterations=max_iterations,
        enable_otel=enable_otel,
    )


def _optimized_tool_functions(definitions: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for item in definitions or ():
        if not isinstance(item, Mapping):
            continue
        func = item.get("function") if isinstance(item.get("function"), Mapping) else item
        if isinstance(func.get("name"), str) and func["name"]:
            out[func["name"]] = func
    return out


def _apply_optimized_parameter_descriptions(definition: object, optimized: Mapping[str, Any] | None) -> None:
    parameters = optimized.get("parameters") if optimized else None
    properties = parameters.get("properties") if isinstance(parameters, Mapping) else None
    if not isinstance(properties, Mapping):
        return
    for prop in getattr(definition, "parameters", None) or ():
        schema = properties.get(getattr(prop, "name", None))
        if isinstance(schema, Mapping) and isinstance(schema.get("description"), str) and schema["description"]:
            prop.description = schema["description"]


def _reject_duplicate_tool_names(tools: Sequence[object]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str):
            continue
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        raise PromptyIntegrationError(
            "Duplicate Prompty tool names (toolbox and app tools must not overlap): " + ", ".join(duplicates)
        )


class ToolboxToolHandler:
    """Prompty tool handler that executes a selected toolbox MCP tool locally."""

    def __init__(self, client: ToolboxMcpClient) -> None:
        self.client = client

    def execute_tool(self, tool: Any, args: dict[str, Any], agent: Any, parent_inputs: dict[str, Any]) -> str:
        raise NotImplementedError("Use async Prompty execution for toolbox MCP tools.")

    async def execute_tool_async(self, tool: Any, args: dict[str, Any], agent: Any, parent_inputs: dict[str, Any]) -> str:
        name = str(getattr(tool, "name", "") or "")

        async def _call(**arguments: Any) -> str:
            return serialize_mcp_result(await self.client.call_tool(name, arguments))

        return await _traced_tool(name, _call, tool_type=_TOOLBOX_TOOL_TYPE)(**args)


def register_toolbox_tool_handler(
    *,
    kind: str = "toolbox",
    client: ToolboxMcpClient | None = None,
) -> ToolboxToolHandler:
    """Register a Prompty handler for custom ``kind: toolbox`` tools."""
    _prompty()
    from prompty.core.tool_dispatch import (  # type: ignore[import-not-found]
        register_tool_handler,
    )

    handler = ToolboxToolHandler(client or ToolboxMcpClient())
    register_tool_handler(kind, handler)
    return handler


def register_toolbox_function(
    name: str,
    *,
    client: ToolboxMcpClient | None = None,
) -> Callable[..., Awaitable[str]]:
    """Register one toolbox MCP tool as a Prompty function callback.

    The registered (and returned) callback is already wrapped in an
    ``execute_tool`` span, so it is traced whether Prompty resolves it from its
    global registry or the app also passes it in ``tool_functions``.
    """
    _prompty()
    from prompty.core.tool_dispatch import (  # type: ignore[import-not-found]
        register_tool,
    )

    traced = _toolbox_function(name, client or ToolboxMcpClient())
    register_tool(name, traced)
    return traced


def _toolbox_function(name: str, client: ToolboxMcpClient) -> Callable[..., Awaitable[str]]:
    async def _call(**arguments: Any) -> str:
        return serialize_mcp_result(await client.call_tool(name, arguments))

    return _traced_tool(name, _call, tool_type=_TOOLBOX_TOOL_TYPE)


def toolbox_prompty_tools(
    allowed_tools: Sequence[str],
    *,
    descriptions: Mapping[str, str] | None = None,
    param_guidance: Mapping[str, Mapping[str, str]] | None = None,
) -> list[object]:
    """Create Prompty function-tool definitions for toolbox-hosted tools."""
    prompty = _prompty()
    out = []
    for name in allowed_tools:
        guidance = dict((param_guidance or {}).get(name, {}))
        out.append(
            prompty.FunctionTool(
                name=name,
                description=(descriptions or {}).get(name),
                parameters=[
                    prompty.Property(name=param, kind="string", description=description, required=False)
                    for param, description in guidance.items()
                ],
            )
        )
    return out


async def toolbox_prompty_tools_from_mcp(
    allowed_tools: Sequence[str],
    *,
    client: ToolboxMcpClient | None = None,
    descriptions: Mapping[str, str] | None = None,
) -> list[object]:
    """Build Prompty function tools from authoritative MCP ``tools/list`` schemas.

    Prefer this for Prompty-owned toolbox loops. It preserves the upstream
    MCP input schema shape instead of asking the app to hand-author parameter
    names and kinds.
    """
    resolved_client = client or ToolboxMcpClient()
    tools = await resolved_client.list_tools()
    return toolbox_prompty_tools_from_schema(
        tools,
        allowed_tools=allowed_tools,
        descriptions=descriptions,
    )


def toolbox_prompty_tools_from_schema(
    tools: Sequence[Mapping[str, Any]],
    *,
    allowed_tools: Sequence[str] | None = None,
    descriptions: Mapping[str, str] | None = None,
) -> list[object]:
    """Create Prompty function-tool definitions from MCP ``tools/list`` entries."""
    prompty = _prompty()
    selected = _select_mcp_tools(tools, allowed_tools)
    out = []
    for tool in selected:
        name = str(tool.get("name") or "")
        schema = tool.get("inputSchema")
        description = (descriptions or {}).get(name)
        if description is None and isinstance(tool.get("description"), str):
            description = tool["description"]
        out.append(
            prompty.FunctionTool(
                name=name,
                description=description,
                parameters=_prompty_parameters_from_input_schema(prompty, schema),
            )
        )
    return out


async def toolbox_preflight(
    allowed_tools: Sequence[str] | None = None,
    *,
    endpoint: str | None = None,
    env: Mapping[str, str] | None = None,
    token_provider: TokenProvider | None = None,
    headers: Mapping[str, str] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ToolboxPreflightResult:
    """Resolve and check a Foundry toolbox MCP endpoint with actionable output.

    The default token path mints an Entra bearer for
    ``https://ai.azure.com/.default``, matching Castia's runtime toolbox client.
    """
    resolved = endpoint or resolve_toolbox_endpoint(env)
    if not resolved:
        return ToolboxPreflightResult(
            ok=False,
            endpoint=None,
            diagnostics=(_NO_TOOLBOX_ENDPOINT_DIAGNOSTIC,),
        )

    client = ToolboxMcpClient(
        resolved,
        token_provider=token_provider,
        headers=headers,
        client=http_client,
    )
    try:
        from azure.core.exceptions import AzureError
    except ImportError:  # pragma: no cover - base castia depends on azure-core
        AzureError = RuntimeError  # type: ignore[assignment]

    try:
        tools = await client.list_tools()
    except (McpToolboxError, httpx.HTTPError, ValueError, AzureError) as exc:
        return ToolboxPreflightResult(
            ok=False,
            endpoint=resolved,
            diagnostics=(f"Unable to call MCP tools/list at {resolved}: {exc}",),
        )

    names = tuple(str(tool.get("name")) for tool in tools if isinstance(tool.get("name"), str))
    requested = tuple(allowed_tools or ())
    missing = tuple(name for name in requested if name not in names)
    diagnostics = _toolbox_preflight_diagnostics(
        endpoint=resolved,
        requested=requested,
        names=names,
        missing=missing,
    )
    return ToolboxPreflightResult(
        ok=not missing,
        endpoint=resolved,
        tool_names=names,
        missing_tools=missing,
        diagnostics=diagnostics,
        tools=tuple(tools),
    )


def _select_mcp_tools(
    tools: Sequence[Mapping[str, Any]],
    allowed_tools: Sequence[str] | None,
) -> list[Mapping[str, Any]]:
    named = {tool.get("name"): tool for tool in tools if isinstance(tool.get("name"), str)}
    if allowed_tools is None:
        return list(named.values())
    missing = [name for name in allowed_tools if name not in named]
    if missing:
        available = ", ".join(sorted(str(name) for name in named)) or "<none>"
        raise McpToolboxError(
            "Requested toolbox tools are not present in MCP tools/list: "
            f"{', '.join(missing)}. Available tools: {available}."
        )
    return [named[name] for name in allowed_tools]


def _prompty_parameters_from_input_schema(prompty: Any, schema: Any) -> list[object]:
    if not isinstance(schema, Mapping):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    return [
        _prompty_property_from_schema(
            prompty,
            name=str(name),
            schema=prop_schema,
            required=name in required_names,
        )
        for name, prop_schema in properties.items()
        if isinstance(name, str)
    ]


def _prompty_property_from_schema(
    prompty: Any,
    *,
    name: str,
    schema: Any,
    required: bool,
) -> object:
    prop_schema = schema if isinstance(schema, Mapping) else {}
    kind = _json_schema_kind(prop_schema)
    kwargs: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "description": prop_schema.get("description") if isinstance(prop_schema.get("description"), str) else None,
        "required": required,
    }
    if isinstance(prop_schema.get("enum"), list):
        kwargs["enum_values"] = list(prop_schema["enum"])
    if "default" in prop_schema:
        kwargs["default"] = prop_schema["default"]
    factory = prompty.Property
    if kind == "array":
        factory = getattr(prompty, "ArrayProperty", prompty.Property)
        if isinstance(prop_schema.get("items"), Mapping):
            kwargs["items"] = _prompty_schema_shape(prompty, prop_schema["items"])
    if kind == "object":
        factory = getattr(prompty, "ObjectProperty", prompty.Property)
        nested = _prompty_parameters_from_input_schema(prompty, prop_schema)
        if nested:
            kwargs["properties"] = nested
        if "additionalProperties" in prop_schema:
            kwargs["additionalProperties"] = prop_schema["additionalProperties"]
            kwargs["additional_properties"] = prop_schema["additionalProperties"]
    return _construct_prompty_object(factory, kwargs)


def _prompty_schema_shape(prompty: Any, schema: Mapping[str, Any]) -> object:
    return _prompty_property_from_schema(
        prompty,
        name="items",
        schema=schema,
        required=False,
    )


def _json_schema_kind(schema: Mapping[str, Any]) -> str:
    kind = schema.get("type")
    if isinstance(kind, list):
        for candidate in kind:
            if isinstance(candidate, str) and candidate != "null":
                return candidate
        return "string"
    if isinstance(kind, str):
        return kind
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "string"


def _construct_prompty_object(factory: Callable[..., Any], kwargs: Mapping[str, Any]) -> object:
    filtered = _filter_supported_kwargs(factory, kwargs)
    try:
        return factory(**filtered)
    except TypeError:
        minimal = {
            key: value
            for key, value in filtered.items()
            if key in {"name", "kind", "description", "required"} and value is not None
        }
        return factory(**minimal)


def _filter_supported_kwargs(factory: Callable[..., Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parameters = signature(factory).parameters
    except (TypeError, ValueError):
        return {key: value for key, value in kwargs.items() if value is not None}
    if any(param.kind == Parameter.VAR_KEYWORD for param in parameters.values()):
        return {key: value for key, value in kwargs.items() if value is not None}
    return {
        key: value
        for key, value in kwargs.items()
        if key in parameters and value is not None
    }


def _toolbox_preflight_diagnostics(
    *,
    endpoint: str,
    requested: Sequence[str],
    names: Sequence[str],
    missing: Sequence[str],
) -> tuple[str, ...]:
    diagnostics: list[str] = [f"Resolved toolbox MCP endpoint: {endpoint}."]
    diagnostics.append(f"tools/list returned {len(names)} tool(s): {', '.join(names) if names else '<none>'}.")
    if missing:
        diagnostics.append(
            "Missing requested toolbox tool(s): "
            f"{', '.join(missing)}. Update allowed_tools to match tools/list exactly."
        )
        suffix_matches = []
        for missing_name in missing:
            matches = [name for name in names if name.endswith("___" + missing_name)]
            if matches:
                suffix_matches.append(f"{missing_name} -> {', '.join(matches)}")
        if suffix_matches:
            diagnostics.append(
                "Some requested bare names have fully qualified toolbox matches: "
                + "; ".join(suffix_matches)
                + "."
            )
    elif requested:
        diagnostics.append(f"All requested toolbox tool(s) are available: {', '.join(requested)}.")
    else:
        diagnostics.append("No allowed_tools were requested; inspect tool_names before exposing tools to Prompty.")
    return tuple(diagnostics)


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = [
    "DEFAULT_FOUNDRY_CONNECTION",
    "DEFAULT_TOOLBOX_CONNECTION",
    "McpToolboxError",
    "PromptyIntegrationError",
    "PromptyRunner",
    "ToolboxMcpClient",
    "ToolboxPreflightResult",
    "ToolboxRuntimeConfigError",
    "ToolboxToolHandler",
    "configured_prompty_runner",
    "load_prompty_agent",
    "prompty_agent_from_config",
    "prompty_runner_provider",
    "prompty_tools_from_castia",
    "register_foundry_default_connection",
    "register_prompty_otel_tracing",
    "register_prompty_trace_sinks",
    "register_toolbox_function",
    "register_toolbox_tool_handler",
    "serialize_mcp_result",
    "toolbox_preflight",
    "toolbox_prompty_tools",
    "toolbox_prompty_tools_from_mcp",
    "toolbox_prompty_tools_from_schema",
]

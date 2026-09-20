"""Experimental Prompty integration for Castia agents.

Prompty is optional: install ``castia[prompty]`` before importing this module.
The existing ``castia.Model`` path remains the compatibility default; these
helpers let an app opt into Prompty as a runtime/eval harness while keeping
``.agent_configs`` as the optimizer contract.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import Parameter, isawaitable, signature
from typing import Any
from uuid import uuid4

import httpx

from castia.integrations.toolbox import AI_FOUNDRY_SCOPE, resolve_toolbox_endpoint
from castia.optimizing.config import AgentConfig, load_agent_config

DEFAULT_FOUNDRY_CONNECTION = "foundry-default"
DEFAULT_TOOLBOX_CONNECTION = "contract-toolbox"
_CONTENT_RECORDING_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_TRACE_INTERNAL_ENV = "CASTIA_PROMPTY_TRACE_INTERNAL"
_DEFAULT_PROMPTY_SPANS = {"turn_async", "run_async"}
_NO_TOOLBOX_ENDPOINT_DIAGNOSTIC = (
    "No toolbox MCP endpoint was resolved. Set TOOLBOX_ENDPOINT, "
    "TOOLBOX_MCP_ENDPOINT, TOOLBOX_NAME with its platform endpoint variable, "
    "or FOUNDRY_PROJECT_ENDPOINT plus TOOLBOX_NAME."
)
_REFERENCE_PAYLOAD_KEYS = frozenset({"ref_id", "uri", "sourceData", "snippet"})
_logger = logging.getLogger("agent")

TokenProvider = Callable[[], str | Awaitable[str]]


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


class PromptyIntegrationError(RuntimeError):
    """Raised when optional Prompty integration dependencies are unavailable."""


class McpToolboxError(RuntimeError):
    """A toolbox MCP JSON-RPC or protocol error."""


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
    prompty.register_connection(name, client=client)
    return client


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

    async def turn(self, text: str, **inputs: object) -> str:
        """Run one external user turn through Prompty and return text."""
        prompty = _prompty()
        timeline = _TurnTimeline(include_content=_content_recording_enabled(), events=[])
        token = _CURRENT_TIMELINE.set(timeline)
        try:
            result = await prompty.turn_async(
                self.agent,
                {"text": text, **inputs},
                tools=_traced_tool_functions(self.tool_functions or {}),
                max_iterations=self.max_iterations,
            )
        finally:
            _CURRENT_TIMELINE.reset(token)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)


def _traced_tool_functions(tool_functions: Mapping[str, Callable[..., Any]]) -> dict[str, Callable[..., Awaitable[Any]]]:
    from castia.observe.tracing import execute_tool

    traced = {}
    for name, tool_function in tool_functions.items():

        async def call_tool(*args: Any, _name: str = name, _tool_function: Callable[..., Any] = tool_function, **kwargs: Any) -> Any:
            timeline = _CURRENT_TIMELINE.get()
            event = _tool_timeline_event(timeline, _name, args, kwargs)
            with execute_tool(_name) as span:
                _set_tool_span_start_attributes(span, event, _name, args, kwargs)
                try:
                    result = _tool_function(*args, **kwargs)
                    if isawaitable(result):
                        result = await result
                except Exception as exc:
                    _set_tool_span_error_attributes(span, event, exc)
                    raise
                else:
                    _set_tool_span_success_attributes(span, event, result)
                    return result

        traced[name] = call_tool
    return traced


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
    """
    if enable_otel is not None:
        register_prompty_otel_tracing(enable_content_recording=enable_otel)
    agent = (
        load_prompty_agent(prompty_path)
        if prompty_path is not None
        else prompty_agent_from_config(config, connection_name=connection_name, tools=tools)
    )
    return PromptyRunner(
        agent,
        tool_functions=dict(tool_functions or {}),
        max_iterations=max_iterations,
    )


class ToolboxMcpClient:
    """Minimal JSON-RPC client for Foundry toolbox MCP endpoints."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        token_provider: TokenProvider | None = None,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved = endpoint or resolve_toolbox_endpoint()
        if not resolved:
            raise ValueError("A toolbox MCP endpoint is required.")
        self.endpoint = resolved
        self.token_provider = token_provider
        self.headers = dict(headers or {})
        self.client = client

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpToolboxError("tools/list returned no tools array.")
        return [tool for tool in tools if isinstance(tool, dict)]

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        if not isinstance(result, dict):
            raise McpToolboxError("tools/call returned a non-object result.")
        return result

    async def _request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.headers,
        }
        token = await _maybe_await(self.token_provider() if self.token_provider else _default_toolbox_token())
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": method,
        }
        if params is not None:
            payload["params"] = dict(params)

        async def send(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(self.endpoint, json=payload, headers=headers)

        if self.client is not None:
            response = await send(self.client)
        else:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await send(client)

        try:
            data = response.json()
        except ValueError as exc:
            raise McpToolboxError(f"MCP endpoint returned non-JSON HTTP {response.status_code}.") from exc
        if response.status_code >= 400:
            raise McpToolboxError(f"MCP endpoint returned HTTP {response.status_code}: {_safe_json(data)}")
        if isinstance(data, dict) and data.get("error"):
            raise McpToolboxError(f"MCP error from {method}: {_safe_json(data['error'])}")
        if not isinstance(data, dict) or "result" not in data:
            raise McpToolboxError(f"MCP response for {method} did not include result.")
        return data["result"]


class ToolboxToolHandler:
    """Prompty tool handler that executes a selected toolbox MCP tool locally."""

    def __init__(self, client: ToolboxMcpClient) -> None:
        self.client = client

    def execute_tool(self, tool: Any, args: dict[str, Any], agent: Any, parent_inputs: dict[str, Any]) -> str:
        raise NotImplementedError("Use async Prompty execution for toolbox MCP tools.")

    async def execute_tool_async(self, tool: Any, args: dict[str, Any], agent: Any, parent_inputs: dict[str, Any]) -> str:
        result = await self.client.call_tool(getattr(tool, "name", ""), args)
        return serialize_mcp_result(result)


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
    """Register one toolbox MCP tool as a Prompty function callback."""
    _prompty()
    from prompty.core.tool_dispatch import (  # type: ignore[import-not-found]
        register_tool,
    )

    resolved_client = client or ToolboxMcpClient()

    async def _call(**arguments: Any) -> str:
        return serialize_mcp_result(await resolved_client.call_tool(name, arguments))

    register_tool(name, _call)
    return _call


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
    if "enum" in prop_schema:
        kwargs["enum"] = prop_schema["enum"]
    if "default" in prop_schema:
        kwargs["default"] = prop_schema["default"]
    if kind == "array" and isinstance(prop_schema.get("items"), Mapping):
        kwargs["items"] = _prompty_schema_shape(prompty, prop_schema["items"])
    if kind == "object":
        nested = _prompty_parameters_from_input_schema(prompty, prop_schema)
        if nested:
            kwargs["properties"] = nested
        if "additionalProperties" in prop_schema:
            kwargs["additionalProperties"] = prop_schema["additionalProperties"]
            kwargs["additional_properties"] = prop_schema["additionalProperties"]
    return _construct_prompty_object(prompty.Property, kwargs)


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


def serialize_mcp_result(result: Mapping[str, Any]) -> str:
    """Serialize MCP ``tools/call`` output into safe text for a model loop."""
    if result.get("isError"):
        raise McpToolboxError(_mcp_error_message(result))
    content = result.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        references: list[Mapping[str, Any]] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                continue
            parsed_reference = _reference_from_text(item["text"])
            if parsed_reference is not None:
                references.append(parsed_reference)
            else:
                text_parts.append(item["text"])
        if text_parts or references:
            return _format_mcp_text(text_parts, references)
    return _safe_json(result)


def _mcp_error_message(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        messages: list[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
                continue
            text = item["text"].strip()
            parsed = _loads_json_object(text)
            if parsed is not None:
                message = _string_at(parsed, ("message", "errorMessage", "detail"))
                error = parsed.get("error")
                if message:
                    messages.append(message)
                elif isinstance(error, Mapping):
                    messages.append(_string_at(error, ("message", "detail")) or _safe_json(error))
                elif isinstance(error, str):
                    messages.append(error)
                else:
                    messages.append(_safe_json(parsed))
            elif text:
                messages.append(text)
        if messages:
            return "\n".join(messages)
    return _safe_json(result)


def _format_mcp_text(text_parts: Sequence[str], references: Sequence[Mapping[str, Any]]) -> str:
    parts = [part for part in (_compact_text(text) for text in text_parts) if part]
    if references:
        reference_lines = [
            _format_reference(reference, index)
            for index, reference in enumerate(references, start=1)
        ]
        parts.append("References:\n" + "\n".join(reference_lines))
    return "\n\n".join(parts)


def _format_reference(reference: Mapping[str, Any], index: int) -> str:
    source_data = reference.get("sourceData")
    source = source_data if isinstance(source_data, Mapping) else {}
    label = _compact_text(_string_at(reference, ("ref_id", "id", "referenceId")) or str(index), limit=80)
    title = _compact_text(
        _string_at(reference, ("title", "name", "source", "sourceName"))
        or _string_at(source, ("title", "name", "source", "sourceName", "fileName", "displayName")),
        limit=160,
    )
    uri = _compact_text(
        _string_at(reference, ("uri", "url"))
        or _string_at(source, ("uri", "url", "sourceUrl", "webUrl")),
        limit=240,
    )
    snippet = _compact_text(
        _string_at(reference, ("snippet", "text", "content", "excerpt"))
        or _string_at(source, ("snippet", "text", "content", "excerpt", "summary")),
        limit=500,
    )

    summary = title or uri or "reference"
    if uri and uri != summary:
        summary = f"{summary} - {uri}"
    line = f"- [{label}] {summary}"
    if snippet:
        line += f"\n  Snippet: {snippet}"
    return line


def _reference_from_text(text: str) -> Mapping[str, Any] | None:
    parsed = _loads_json_object(text.strip())
    if parsed is None:
        return None
    if parsed.get("kind") == "reference" and _REFERENCE_PAYLOAD_KEYS.intersection(parsed):
        return parsed
    return None


def _loads_json_object(text: str) -> Mapping[str, Any] | None:
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _string_at(mapping: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _compact_text(text: str | None, *, limit: int | None = None) -> str:
    if text is None:
        return ""
    compact = " ".join(text.split())
    if limit is not None and len(compact) > limit:
        return compact[: max(0, limit - 3)].rstrip() + "..."
    return compact


async def _default_toolbox_token() -> str:
    from castia.integrations.toolbox import toolbox_token

    return await toolbox_token(AI_FOUNDRY_SCOPE)


async def _maybe_await(value: str | Awaitable[str]) -> str:
    if hasattr(value, "__await__"):
        return await value  # type: ignore[misc]
    return value


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
    "ToolboxToolHandler",
    "configured_prompty_runner",
    "load_prompty_agent",
    "prompty_agent_from_config",
    "register_foundry_default_connection",
    "register_prompty_otel_tracing",
    "register_toolbox_function",
    "register_toolbox_tool_handler",
    "serialize_mcp_result",
    "toolbox_preflight",
    "toolbox_prompty_tools",
    "toolbox_prompty_tools_from_mcp",
    "toolbox_prompty_tools_from_schema",
]

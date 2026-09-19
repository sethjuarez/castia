"""Experimental Prompty integration for Castia agents.

Prompty is optional: install ``castia[prompty]`` before importing this module.
The existing ``castia.Model`` path remains the compatibility default; these
helpers let an app opt into Prompty as a runtime/eval harness while keeping
``.agent_configs`` as the optimizer contract.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from castia.integrations.toolbox import AI_FOUNDRY_SCOPE, resolve_toolbox_endpoint
from castia.optimizing.config import AgentConfig, load_agent_config

DEFAULT_FOUNDRY_CONNECTION = "foundry-default"
DEFAULT_TOOLBOX_CONNECTION = "contract-toolbox"
_CONTENT_RECORDING_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"

TokenProvider = Callable[[], str | Awaitable[str]]


class PromptyIntegrationError(RuntimeError):
    """Raised when optional Prompty integration dependencies are unavailable."""


class McpToolboxError(RuntimeError):
    """A toolbox MCP JSON-RPC or protocol error."""


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
    """Register Prompty's OTel backend only when content recording is enabled.

    Prompty's generic OTel tracer records structured ``inputs`` and ``result``
    attributes. To keep Castia privacy defaults aligned, this helper is a no-op
    unless explicitly enabled or the existing Foundry content-recording opt-in
    environment variable is ``true``. Call it after Castia observability has
    been configured so the Prompty backend binds to the active Microsoft OTel
    provider.
    """
    enabled = _content_recording_enabled() if enable_content_recording is None else enable_content_recording
    if not enabled:
        return False
    prompty = _prompty()
    from opentelemetry import trace as otel_trace
    from prompty.tracing.otel import otel_tracer  # type: ignore[import-not-found]

    prompty.Tracer.add(name, otel_tracer(tracer_name=tracer_name, provider=provider or otel_trace.get_tracer_provider()))
    return True


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
        result = await prompty.turn_async(
            self.agent,
            {"text": text, **inputs},
            tools=dict(self.tool_functions or {}),
            max_iterations=self.max_iterations,
        )
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)


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


def serialize_mcp_result(result: Mapping[str, Any]) -> str:
    """Serialize MCP ``tools/call`` output into safe text for a model loop."""
    if result.get("isError"):
        raise McpToolboxError(_safe_json(result))
    content = result.get("content")
    if isinstance(content, list):
        text_parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        if text_parts and len(text_parts) == len(content):
            return "\n".join(text_parts)
    return _safe_json(result)


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
    "ToolboxToolHandler",
    "configured_prompty_runner",
    "load_prompty_agent",
    "prompty_agent_from_config",
    "register_foundry_default_connection",
    "register_prompty_otel_tracing",
    "register_toolbox_function",
    "register_toolbox_tool_handler",
    "serialize_mcp_result",
    "toolbox_prompty_tools",
]

"""Consuming a Microsoft Foundry **Toolbox** from a hosted agent.

A *toolbox* is a curated set of tools the platform exposes behind a single
MCP-compatible endpoint, with centralized auth, governance, and versioning. A
hosted (custom-code) agent is **not** configured with tools in the portal --
your code owns the tool loop -- so the way a toolbox reaches this agent is:

1. Someone creates the toolbox (REST/SDK/CLI) and it gets a stable MCP URL::

       {project_endpoint}/toolboxes/{name}/mcp?api-version=v1

2. ``azd`` injects that address into the container as an environment variable
   (the toolbox *name* embedded in the URL is the only "which toolbox"
   selector -- there is no separate picker).
3. This module reads it at startup and turns it into a single Responses-API
   ``mcp`` tool spec, which the model service resolves **server-side** within a
   turn. Unlike :mod:`castia.inference.tools` function tools, a toolbox tool needs no
   local impl -- the toolbox runs the tool and the model folds the result in.

Server-side ``mcp`` specs do not declare each federated tool as a local
function, but tool wording still matters: Foundry's guidance says descriptions
steer tool choice, and the OpenAI Responses MCP schema exposes
``server_description`` as model-visible context for a remote MCP server. Pass a
``descriptions`` map (and optional ``param_guidance``) when you need local
post-facto wording for selected toolbox tools. The map is folded into
``server_description`` for runtime steering and into a private optimizer sidecar
that :mod:`castia.optimizing.baseline` serializes to ``tools.json``. This keeps the
validated server-side toolbox call path while making selected federated tools
eligible for Foundry Agent Optimizer description rewrites. Because the builder
is offline and cannot list a remote toolbox, overrides require ``allowed_tools``
so stale names fail loud instead of silently dropping optimizer guidance.
Responses returns MCP calls with ``server_label`` separate from ``name``; live
validation against a Foundry toolbox showed the model-visible name is the
``allowed_tools`` entry (for example ``web``), not ``{server_label}___web``.
Toolbox-federated connection names may still contain their own ``___`` prefix
(for example ``contracts-kb-mcp___knowledge_base_retrieve``).

Env forms are supported in this precedence (see :func:`resolve_toolbox_endpoint`):

* an explicit full URL in ``TOOLBOX_ENDPOINT`` / ``TOOLBOX_MCP_ENDPOINT`` -- a
  passthrough name **we** choose for the container; or
* the **platform-native** ``TOOLBOX_<NORMALIZED_NAME>_MCP_ENDPOINT`` that the
  ``azd ai toolbox`` extension itself writes to the azd environment on
  ``create``/``show`` (normalized = upper-cased, non-alphanumerics -> ``_``),
  resolved from ``TOOLBOX_NAME``; or
* ``FOUNDRY_PROJECT_ENDPOINT`` + ``TOOLBOX_NAME`` (+ optional ``TOOLBOX_VERSION``),
  which this module composes into the same URL. The unversioned URL resolves the
  toolbox's promoted default version, so version bumps need no redeploy.

The builders here are **pure** (offline-testable); :func:`toolbox_token` is the
one impure seam -- it mints an Entra token for the toolbox audience from the
container's managed identity, mirroring :func:`castia.hosting.credentials.bot_connector_token`.

.. note::
   Validated live (hal ``hello-world-autopilot`` v40, 2026-09-09): the Responses
   model service **accepts** a Foundry-toolbox ``mcp`` tool authenticated with a
   raw ``https://ai.azure.com`` bearer header minted from the container's managed
   identity -- **no ``project_connection_id`` required**. The turn's span chain
   showed ``tools/list`` + ``tools/call`` against the toolbox succeeding. The
   ``project_connection_id`` path remains supported for stored-connection auth.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from castia.inference.tools import Tool

if TYPE_CHECKING:
    import httpx

#: The Entra audience the toolbox MCP endpoint authenticates against. Mirrors the
#: ``azd ai connection create remote-tool ... --audience https://ai.azure.com``
#: guidance for a toolbox remote-tool connection.
AI_FOUNDRY_SCOPE = "https://ai.azure.com/.default"

#: Env var carrying a complete toolbox MCP URL (name embedded). ``TOOLBOX_MCP_ENDPOINT``
#: is accepted as an alias some scaffolds inject.
_FULL_URL_ENVS = ("TOOLBOX_ENDPOINT", "TOOLBOX_MCP_ENDPOINT")
#: Env vars used to *compose* the URL when a full one isn't supplied.
_PROJECT_ENDPOINT_ENV = "FOUNDRY_PROJECT_ENDPOINT"
_NAME_ENV = "TOOLBOX_NAME"
_VERSION_ENV = "TOOLBOX_VERSION"

_API_VERSION = "v1"
_FEDERATED_NAME_SEPARATOR = "___"

# Private castia metadata carried on a raw MCP spec. ``Model.respond_with_tools``
# strips it before calling Responses; ``python -m castia optimize`` consumes it
# when generating ``.agent_configs/baseline/tools.json``.
OPTIMIZER_TOOL_DEFINITIONS_KEY = "x-castia-optimizer-tool-definitions"
_SERVER_DESCRIPTION_KEY = "x-castia-server-description"
_REFERENCE_PAYLOAD_KEYS = frozenset({"ref_id", "uri", "sourceData", "snippet"})
TokenProvider = Callable[[], str | Awaitable[str]]


class ToolboxConfigurationError(ValueError):
    """A toolbox endpoint or selection is missing or malformed."""


class ToolboxAuthenticationError(RuntimeError):
    """Toolbox token acquisition failed."""


class McpToolboxError(RuntimeError):
    """A toolbox MCP JSON-RPC or protocol error."""


class ToolboxMcpClient:
    """Minimal JSON-RPC client for Foundry toolbox MCP endpoints."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        token_provider: TokenProvider | None = None,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        trace_requests: bool = True,
    ) -> None:
        resolved = endpoint or resolve_toolbox_endpoint()
        if not resolved:
            raise ValueError(
                "A toolbox MCP endpoint is required. Set TOOLBOX_ENDPOINT or "
                "TOOLBOX_MCP_ENDPOINT, or set FOUNDRY_PROJECT_ENDPOINT and "
                "TOOLBOX_NAME so Castia can compose it."
            )
        validate_toolbox_endpoint(resolved)
        self.endpoint = resolved
        self.token_provider = token_provider
        self.headers = dict(headers or {})
        self.client = client
        self.trace_requests = trace_requests

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpToolboxError("tools/list returned no tools array.")
        if not tools:
            raise McpToolboxError("tools/list returned zero tools for this toolbox.")
        return [tool for tool in tools if isinstance(tool, dict)]

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        if not isinstance(result, dict):
            raise McpToolboxError("tools/call returned a non-object result.")
        return result

    async def _request(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.headers,
        }
        try:
            token = await _maybe_await(
                self.token_provider() if self.token_provider else toolbox_token()
            )
        except Exception as exc:
            raise McpToolboxError(
                f"Toolbox token acquisition failed: {type(exc).__name__}: {exc}"
            ) from exc
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
            if not self.trace_requests:
                return await client.post(self.endpoint, json=payload, headers=headers)
            from castia.observe.tracing import http_client_span

            with http_client_span(
                "POST",
                self.endpoint,
                headers=headers,
                attributes={
                    "castia.telemetry.scope": "toolbox_mcp_http",
                    "castia.toolbox.mcp": True,
                    "castia.toolbox.mcp.method": method,
                },
            ) as outbound:
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers=outbound.headers,
                )
                outbound.set_response(response.status_code)
                return response

        if self.client is not None:
            response = await send(self.client)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                response = await send(client)

        try:
            data = response.json()
        except ValueError as exc:
            raise McpToolboxError(
                f"MCP endpoint returned non-JSON HTTP {response.status_code}."
            ) from exc
        if response.status_code >= 400:
            raise McpToolboxError(
                f"MCP endpoint returned HTTP {response.status_code}: {_safe_json(data)}"
            )
        if isinstance(data, dict) and data.get("error"):
            raise McpToolboxError(_mcp_json_rpc_error_message(method, data["error"]))
        if not isinstance(data, dict) or "result" not in data:
            raise McpToolboxError(f"MCP response for {method} did not include result.")
        return data["result"]


async def toolbox_tools_from_mcp(
    allowed_tools: Sequence[str] | None = None,
    *,
    endpoint: str | None = None,
    token_provider: TokenProvider | None = None,
    headers: Mapping[str, str] | None = None,
    descriptions: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    mcp_client: ToolboxMcpClient | None = None,
    trace_requests: bool = True,
) -> list[Tool]:
    """Build local Castia tools from a Foundry toolbox MCP ``tools/list`` schema.

    Unlike :func:`toolbox_mcp_tool`, these are ordinary local
    :class:`castia.inference.tools.Tool` objects. ``Model.respond_with_tools``
    executes them client-side through ``tools/call``, so every remote toolbox
    invocation is wrapped in Castia's ``execute_tool`` telemetry span.
    """
    resolved_client = mcp_client or ToolboxMcpClient(
        endpoint,
        token_provider=token_provider,
        headers=headers,
        client=client,
        trace_requests=trace_requests,
    )
    return toolbox_tools_from_schema(
        await resolved_client.list_tools(),
        allowed_tools=allowed_tools,
        descriptions=descriptions,
        client=resolved_client,
    )


def toolbox_tools_from_schema(
    tools: Sequence[Mapping[str, Any]],
    *,
    allowed_tools: Sequence[str] | None = None,
    descriptions: Mapping[str, str] | None = None,
    client: ToolboxMcpClient,
) -> list[Tool]:
    """Create local Castia tools from MCP tool schema entries."""
    selected = _select_mcp_tools(tools, allowed_tools)
    out: list[Tool] = []
    for schema in selected:
        name = str(schema.get("name") or "")
        parameters = schema.get("inputSchema")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}, "additionalProperties": True}
        description = (descriptions or {}).get(name)
        if description is None and isinstance(schema.get("description"), str):
            description = schema["description"]

        async def _call(_activity: object, _name: str = name, **arguments: Any) -> dict:
            result = await client.call_tool(_name, arguments)
            return {"ok": True, "result": serialize_mcp_result(result)}

        out.append(
            Tool(
                name=name,
                description=description or f"Call the remote toolbox tool {name}.",
                parameters=dict(parameters),
                impl=_call,
                kind="mcp",
            )
        )
    return out


def platform_endpoint_env(name: str) -> str:
    """The azd-toolbox-extension env var name for a toolbox's computed MCP URL.

    ``azd ai toolbox create``/``show`` write the endpoint to
    ``TOOLBOX_<NORMALIZED_NAME>_MCP_ENDPOINT`` in the active azd environment (and
    ``delete`` clears it). Normalization upper-cases the name and replaces every
    run of non-alphanumeric characters with a single underscore, so ``hal-smoke``
    -> ``TOOLBOX_HAL_SMOKE_MCP_ENDPOINT``.
    """
    slug = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").upper()
    return f"TOOLBOX_{slug}_MCP_ENDPOINT"


def compose_toolbox_endpoint(
    project_endpoint: str, name: str, *, version: str | None = None
) -> str:
    """Build the toolbox MCP URL from a project endpoint and a toolbox name.

    ``version`` selects a pinned ``/versions/{version}/`` URL; omit it for the
    unversioned URL, which resolves the promoted default version.
    """
    base = project_endpoint.rstrip("/")
    if version:
        path = f"/toolboxes/{name}/versions/{version}/mcp"
    else:
        path = f"/toolboxes/{name}/mcp"
    return f"{base}{path}?api-version={_API_VERSION}"


def resolve_toolbox_endpoint(env: Mapping[str, str] | None = None) -> str | None:
    """Resolve the toolbox MCP URL from the environment, or ``None`` if unset.

    Precedence:

    1. an explicit full URL in ``TOOLBOX_ENDPOINT`` / ``TOOLBOX_MCP_ENDPOINT``
       (a passthrough var we plumb into the container ourselves);
    2. the **platform-native** ``TOOLBOX_<NORMALIZED_NAME>_MCP_ENDPOINT`` the azd
       toolbox extension writes, keyed off ``TOOLBOX_NAME`` (see
       :func:`platform_endpoint_env`) -- so you can forward the extension's own
       var verbatim;
    3. compose from ``FOUNDRY_PROJECT_ENDPOINT`` + ``TOOLBOX_NAME`` (+ optional
       ``TOOLBOX_VERSION``).

    Returns ``None`` when none is available so a caller can treat "no toolbox
    configured" as simply attaching no tool.
    """
    env = os.environ if env is None else env
    for key in _FULL_URL_ENVS:
        url = env.get(key)
        if url:
            return url
    name = env.get(_NAME_ENV)
    if name:
        platform_url = env.get(platform_endpoint_env(name))
        if platform_url:
            return platform_url
        project = env.get(_PROJECT_ENDPOINT_ENV)
        if project:
            return compose_toolbox_endpoint(
                project, name, version=env.get(_VERSION_ENV) or None
            )
    return None


def validate_toolbox_endpoint(endpoint: str) -> None:
    """Fail loud for Foundry toolbox URLs missing required query settings."""
    parsed = urlparse(endpoint)
    if "/toolboxes/" not in parsed.path and "/knowledgebases/" not in parsed.path:
        return
    if not parse_qs(parsed.query).get("api-version"):
        raise ToolboxConfigurationError(
            "Foundry toolbox MCP endpoint is missing 'api-version'. Use a URL like "
            ".../toolboxes/<name>/mcp?api-version=v1 or let Castia compose it from "
            "FOUNDRY_PROJECT_ENDPOINT and TOOLBOX_NAME."
        )


def toolbox_mcp_tool(
    endpoint: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    server_label: str = "toolbox",
    allowed_tools: list[str] | tuple[str, ...] | None = None,
    require_approval: str = "never",
    token: str | None = None,
    project_connection_id: str | None = None,
    headers: Mapping[str, str] | None = None,
    server_description: str | None = None,
    descriptions: Mapping[str, str] | None = None,
    param_guidance: Mapping[str, Mapping[str, str]] | None = None,
    required: bool = False,
) -> dict[str, Any] | None:
    """A Responses-API ``mcp`` tool spec for a Foundry toolbox, or ``None``.

    ``endpoint`` defaults to :func:`resolve_toolbox_endpoint` over ``env`` (the
    process environment by default), so in a deployed agent this is called with
    no arguments and reads ``TOOLBOX_*`` -- returning ``None`` (attach nothing)
    when no toolbox is configured.

    Auth is either a bearer ``token`` (folded into an ``Authorization`` header for
    the model service to present to the toolbox) or a ``project_connection_id``
    (the toolbox resolves auth from a stored project connection). Extra ``headers``
    are merged last. ``allowed_tools`` restricts which of the toolbox's tools the
    model may call; omit it to expose all.

    ``descriptions`` and ``param_guidance`` are local, post-facto guidance for
    selected federated tools. Keys may be the selected model-visible MCP name
    or the bare tool name after its final ``___`` segment. ``server_label``
    identifies the server; it does not prefix the tool's name. The legacy
    ``{server_label}___{tool}`` spelling is accepted as an alias but normalizes
    back to the selected tool name. Unknown or ambiguous keys raise. Since the
    builder cannot discover a remote toolbox offline, ``allowed_tools`` is
    required whenever overrides are supplied.
    """
    override_defs = _optimizer_tool_definitions(
        server_label=server_label,
        allowed_tools=allowed_tools,
        descriptions=descriptions,
        param_guidance=param_guidance,
    )
    endpoint = endpoint or resolve_toolbox_endpoint(env)
    if not endpoint:
        if required:
            raise ToolboxConfigurationError(
                "No toolbox MCP endpoint configured. Set TOOLBOX_ENDPOINT or "
                "TOOLBOX_MCP_ENDPOINT, set TOOLBOX_NAME with its platform "
                "TOOLBOX_<NAME>_MCP_ENDPOINT, or set FOUNDRY_PROJECT_ENDPOINT "
                "and TOOLBOX_NAME so Castia can compose the URL."
            )
        return None
    validate_toolbox_endpoint(endpoint)
    spec: dict[str, Any] = {
        "type": "mcp",
        "server_label": server_label,
        "server_url": endpoint,
        "require_approval": require_approval,
    }
    if allowed_tools:
        spec["allowed_tools"] = list(allowed_tools)
    if server_description or override_defs:
        if server_description:
            spec[_SERVER_DESCRIPTION_KEY] = server_description
        spec["server_description"] = _server_description(
            server_description, override_defs
        )
    if override_defs:
        spec[OPTIMIZER_TOOL_DEFINITIONS_KEY] = override_defs
    if project_connection_id:
        spec["project_connection_id"] = project_connection_id
    merged: dict[str, str] = {}
    if token:
        # Concatenate rather than interpolate: the workspace secret-redaction
        # filter rewrites an interpolated-bearer literal on save. See
        # castia.hosting.credentials.bearer for the same guard.
        merged["Authorization"] = "Bearer " + token
    if headers:
        merged.update(headers)
    if merged:
        spec["headers"] = merged
    return spec


def knowledge_base_mcp_tool(
    endpoint: str,
    *,
    search_token: str | None = None,
    allowed_tools: list[str] | tuple[str, ...] = ("knowledge_base_retrieve",),
    server_label: str = "knowledge-base",
    require_approval: str = "never",
    token: str | None = None,
    project_connection_id: str | None = None,
    server_description: str | None = None,
    descriptions: Mapping[str, str] | None = None,
    param_guidance: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """A Responses-API ``mcp`` tool spec for a **Foundry IQ** knowledge base.

    Foundry IQ knowledge bases are their own MCP server (Azure AI Search agentic
    retrieval): ``{search}/knowledgebases/{kb}/mcp?api-version=...``. They accept
    a per-user Azure AI Search token forwarded as ``x-ms-query-source-authorization``
    -- the same header a managed prompt agent templates from ``structured_inputs``.
    Pass it as ``search_token``. ``token`` (an ``Authorization`` bearer) and
    ``project_connection_id`` carry the outer connection auth, if used. Override
    descriptions follow :func:`toolbox_mcp_tool` and default to resolving against
    ``knowledge_base_retrieve``.
    """
    headers: dict[str, str] = {}
    if search_token:
        headers["x-ms-query-source-authorization"] = search_token
    spec = toolbox_mcp_tool(
        endpoint,
        server_label=server_label,
        allowed_tools=allowed_tools,
        require_approval=require_approval,
        token=token,
        project_connection_id=project_connection_id,
        headers=headers or None,
        server_description=server_description,
        descriptions=descriptions,
        param_guidance=param_guidance,
    )
    # endpoint is a required non-empty argument here, so toolbox_mcp_tool never
    # returns None; assert to satisfy the type checker and fail loud on misuse.
    assert spec is not None
    return spec


def _optimizer_tool_definitions(
    *,
    server_label: str,
    allowed_tools: list[str] | tuple[str, ...] | None,
    descriptions: Mapping[str, str] | None,
    param_guidance: Mapping[str, Mapping[str, str]] | None,
) -> list[dict[str, Any]]:
    descriptions = descriptions or {}
    param_guidance = param_guidance or {}
    if not descriptions and not param_guidance:
        return []
    if not allowed_tools:
        raise ValueError(
            "allowed_tools is required when toolbox descriptions or "
            "param_guidance are provided; castia cannot validate override names "
            "without a selected toolbox tool list"
        )

    selected = [_SelectedTool(server_label, name) for name in allowed_tools]
    resolved_descriptions = _resolve_overrides("descriptions", descriptions, selected)
    resolved_params = _resolve_overrides("param_guidance", param_guidance, selected)

    out: list[dict[str, Any]] = []
    for tool in selected:
        description = resolved_descriptions.get(tool.final_name)
        params = resolved_params.get(tool.final_name, {})
        if description is None and not params:
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool.final_name,
                    "description": description
                    or f"Call the federated toolbox tool {tool.upstream_name!r}.",
                    "parameters": _parameter_schema(params),
                },
            }
        )
    return out


class _SelectedTool:
    def __init__(self, server_label: str, upstream_name: str) -> None:
        self.upstream_name = upstream_name
        self.final_name = upstream_name
        self.legacy_labeled_name = (
            f"{server_label}{_FEDERATED_NAME_SEPARATOR}{upstream_name}"
        )
        self.bare_name = upstream_name.rsplit(_FEDERATED_NAME_SEPARATOR, 1)[-1]

    def aliases(self) -> set[str]:
        return {
            self.final_name,
            self.upstream_name,
            self.legacy_labeled_name,
            self.bare_name,
        }


def _resolve_overrides(
    label: str, overrides: Mapping[str, Any], selected: list[_SelectedTool]
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in overrides.items():
        matches = [tool for tool in selected if key in tool.aliases()]
        if not matches:
            allowed = ", ".join(tool.final_name for tool in selected)
            raise ValueError(
                f"unknown toolbox {label} key {key!r}; expected one of: {allowed}"
            )
        if len(matches) > 1:
            choices = ", ".join(tool.final_name for tool in matches)
            raise ValueError(
                f"ambiguous toolbox {label} key {key!r}; multiple allowed tools "
                f"share the bare name {key!r}. Use the fully qualified toolbox "
                f"tool name instead; choices: {choices}"
            )
        resolved[matches[0].final_name] = value
    return resolved


def _parameter_schema(guidance: Mapping[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            name: {"description": description}
            for name, description in guidance.items()
        },
        "required": [],
        "additionalProperties": True,
    }


def _server_description(
    base: str | None, tool_definitions: list[dict[str, Any]]
) -> str:
    lines: list[str] = []
    if base:
        lines.append(base)
    if tool_definitions:
        lines.append("Local tool guidance:")
        for item in tool_definitions:
            func = item["function"]
            lines.append(f"- {func['name']}: {func['description']}")
            props = func.get("parameters", {}).get("properties", {})
            for name, schema in props.items():
                if schema.get("description"):
                    lines.append(f"  - {name}: {schema['description']}")
    return "\n".join(lines)


def apply_optimized_toolbox_tools(
    spec: dict[str, Any], tool_definitions: tuple[dict, ...] | list[dict]
) -> dict[str, Any]:
    """Apply optimizer-rewritten toolbox descriptions to a raw MCP spec.

    Matches candidate ``tools.json`` entries by the optimizer sidecar's function
    names, rewrites only descriptions and parameter-description text, and
    regenerates ``server_description`` so candidate runs can actually exercise
    the rewritten wording while preserving the validated server-side MCP runtime.
    Specs without a toolbox sidecar pass through unchanged.
    """
    current = spec.get(OPTIMIZER_TOOL_DEFINITIONS_KEY)
    if not isinstance(current, list) or not current or not tool_definitions:
        return spec

    lookup: dict[str, dict] = {}
    for item in tool_definitions:
        if not isinstance(item, dict):
            continue
        func = item.get("function")
        if not isinstance(func, dict):
            func = item if item.get("name") else {}
        name = func.get("name")
        if name:
            lookup[name] = func

    if not lookup:
        return spec

    changed = False
    rewritten: list[dict[str, Any]] = []
    for item in current:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue
        func = item.get("function")
        if not isinstance(func, dict):
            rewritten.append(item)
            continue
        optimized = lookup.get(func.get("name"))
        if optimized is None:
            rewritten.append(item)
            continue

        new_func = dict(func)
        description = optimized.get("description")
        if description and description != func.get("description"):
            new_func["description"] = description
            changed = True

        parameters = _merge_parameter_descriptions(
            new_func.get("parameters"), optimized.get("parameters")
        )
        if parameters is not new_func.get("parameters"):
            new_func["parameters"] = parameters
            changed = True

        rewritten.append({**item, "function": new_func})

    if not changed:
        return spec

    base = spec.get(_SERVER_DESCRIPTION_KEY)
    if not isinstance(base, str):
        base = None
    out = {**spec, OPTIMIZER_TOOL_DEFINITIONS_KEY: rewritten}
    out["server_description"] = _server_description(base, rewritten)
    return out


def _merge_parameter_descriptions(current: object, optimized: object | None) -> object:
    if not isinstance(current, dict) or not isinstance(optimized, dict):
        return current
    cur_props = current.get("properties")
    opt_props = optimized.get("properties")
    if not isinstance(cur_props, dict) or not isinstance(opt_props, dict):
        return current

    merged_props: dict[str, Any] = {}
    changed = False
    for name, schema in cur_props.items():
        opt_schema = opt_props.get(name)
        if (
            isinstance(schema, dict)
            and isinstance(opt_schema, dict)
            and opt_schema.get("description")
            and opt_schema.get("description") != schema.get("description")
        ):
            merged_props[name] = {**schema, "description": opt_schema["description"]}
            changed = True
        else:
            merged_props[name] = schema

    if not changed:
        return current
    return {**current, "properties": merged_props}


def _select_mcp_tools(
    tools: Sequence[Mapping[str, Any]],
    allowed_tools: Sequence[str] | None,
) -> list[Mapping[str, Any]]:
    named = {
        str(tool.get("name")): tool
        for tool in tools
        if isinstance(tool.get("name"), str)
    }
    if allowed_tools is None:
        return list(named.values())
    missing = [name for name in allowed_tools if name not in named]
    if missing:
        available = ", ".join(sorted(named)) or "<none>"
        raise McpToolboxError(
            "Requested toolbox tools are not present in MCP tools/list: "
            f"{', '.join(missing)}. Available tools: {available}."
        )
    return [named[name] for name in allowed_tools]


def serialize_mcp_result(result: Mapping[str, Any]) -> str:
    """Serialize MCP ``tools/call`` output into safe text for a model loop."""
    if result.get("isError"):
        raise McpToolboxError(_mcp_error_message(result))
    content = result.get("content")
    if isinstance(content, list):
        text_parts: list[str] = []
        references: list[Mapping[str, Any]] = []
        for item in content:
            if (
                not isinstance(item, dict)
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                continue
            parsed_reference = _reference_from_text(item["text"])
            if parsed_reference is not None:
                references.append(parsed_reference)
            else:
                text_parts.append(item["text"])
        if text_parts or references:
            return _format_mcp_text(text_parts, references)
    return _safe_json(result)


def _mcp_json_rpc_error_message(method: str, error: object) -> str:
    text = _safe_json(error)
    lowered = text.lower()
    if "consent_required" in lowered:
        return f"CONSENT_REQUIRED from toolbox {method}: {text}"
    if "not found" in lowered or "unknown tool" in lowered:
        return f"Tool not found during toolbox {method}: {text}"
    if "schema" in lowered or "invalid" in lowered or "argument" in lowered:
        return f"Toolbox schema/tool-call error from {method}: {text}"
    return f"MCP error from {method}: {text}"


def _mcp_error_message(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        messages: list[str] = []
        for item in content:
            if (
                not isinstance(item, dict)
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                continue
            text = item["text"].strip()
            parsed = _loads_json_object(text)
            if parsed is not None:
                message = _string_at(parsed, ("message", "errorMessage", "detail"))
                error = parsed.get("error")
                if message:
                    messages.append(message)
                elif isinstance(error, Mapping):
                    messages.append(
                        _string_at(error, ("message", "detail")) or _safe_json(error)
                    )
                elif isinstance(error, str):
                    messages.append(error)
                else:
                    messages.append(_safe_json(parsed))
            elif text:
                messages.append(text)
        if messages:
            return "\n".join(messages)
    return _safe_json(result)


def _format_mcp_text(
    text_parts: Sequence[str], references: Sequence[Mapping[str, Any]]
) -> str:
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
    label = _compact_text(
        _string_at(reference, ("ref_id", "id", "referenceId")) or str(index),
        limit=80,
    )
    title = _compact_text(
        _string_at(reference, ("title", "name", "source", "sourceName"))
        or _string_at(
            source, ("title", "name", "source", "sourceName", "fileName", "displayName")
        ),
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
    if parsed.get("kind") == "reference" and _REFERENCE_PAYLOAD_KEYS.intersection(
        parsed
    ):
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


async def _maybe_await(value: str | Awaitable[str]) -> str:
    if hasattr(value, "__await__"):
        return await value  # type: ignore[misc]
    return value


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def toolbox_token(scope: str = AI_FOUNDRY_SCOPE) -> str:
    """Mint an Entra token for the toolbox audience from the container identity.

    The hosted agent authenticates to its toolbox as its own Entra identity;
    :class:`~azure.identity.aio.DefaultAzureCredential` resolves the container's
    managed identity in Foundry (and a developer login locally). Mirrors
    :func:`castia.hosting.credentials.bot_connector_token`. Impure (network) -- kept
    out of the unit-tested builders above.
    """
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        try:
            token = await credential.get_token(scope)
        except Exception as exc:
            raise ToolboxAuthenticationError(
                f"Could not acquire toolbox token for {scope}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    finally:
        await credential.close()
    return token.token

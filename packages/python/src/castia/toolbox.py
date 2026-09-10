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
   turn. Unlike :mod:`castia.tools` function tools, a toolbox tool needs no
   local impl -- the toolbox runs the tool and the model folds the result in.

Server-side ``mcp`` specs do not declare each federated tool as a local
function, but tool wording still matters: Foundry's guidance says descriptions
steer tool choice, and the OpenAI Responses MCP schema exposes
``server_description`` as model-visible context for a remote MCP server. Pass a
``descriptions`` map (and optional ``param_guidance``) when you need local
post-facto wording for selected toolbox tools. The map is folded into
``server_description`` for runtime steering and into a private optimizer sidecar
that :mod:`castia.optimize` serializes to ``tools.json``. This keeps the
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
container's managed identity, mirroring :func:`castia.credentials.bot_connector_token`.

.. note::
   Validated live (hal ``hello-world-autopilot`` v40, 2026-09-09): the Responses
   model service **accepts** a Foundry-toolbox ``mcp`` tool authenticated with a
   raw ``https://ai.azure.com`` bearer header minted from the container's managed
   identity -- **no ``project_connection_id`` required**. The turn's span chain
   showed ``tools/list`` + ``tools/call`` against the toolbox succeeding. The
   ``project_connection_id`` path remains supported for stored-connection auth.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

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
    selected federated tools. Keys may be the final model-visible name
    (``{server_label}___{tool}``), the selected upstream name, or the bare tool
    name after the final ``___`` segment. The legacy ``{server_label}___{tool}``
    spelling is accepted as an alias but normalizes back to the selected tool
    name. Unknown or ambiguous keys raise. Since the builder cannot discover a
    remote toolbox offline, ``allowed_tools`` is required whenever overrides are
    supplied.
    """
    override_defs = _optimizer_tool_definitions(
        server_label=server_label,
        allowed_tools=allowed_tools,
        descriptions=descriptions,
        param_guidance=param_guidance,
    )
    endpoint = endpoint or resolve_toolbox_endpoint(env)
    if not endpoint:
        return None
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
        # castia.credentials.bearer for the same guard.
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
                f"ambiguous toolbox {label} key {key!r}; matches: {choices}"
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


async def toolbox_token(scope: str = AI_FOUNDRY_SCOPE) -> str:
    """Mint an Entra token for the toolbox audience from the container identity.

    The hosted agent authenticates to its toolbox as its own Entra identity;
    :class:`~azure.identity.aio.DefaultAzureCredential` resolves the container's
    managed identity in Foundry (and a developer login locally). Mirrors
    :func:`castia.credentials.bot_connector_token`. Impure (network) -- kept
    out of the unit-tested builders above.
    """
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(scope)
    finally:
        await credential.close()
    return token.token

"""Hermetic unit tests for :mod:`castia.integrations.toolbox`.

All pure builders -- no network, no real credential. Exercises endpoint
resolution precedence, the ``mcp`` tool spec shape, auth folding (bearer header
vs. ``project_connection_id``), and the Foundry IQ knowledge-base variant. The
one impure seam (:func:`castia.integrations.toolbox.toolbox_token`) is not called here.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from castia.integrations.toolbox import (
    AI_FOUNDRY_SCOPE,
    OPTIMIZER_TOOL_DEFINITIONS_KEY,
    ToolboxConfigurationError,
    compose_toolbox_endpoint,
    knowledge_base_mcp_tool,
    platform_endpoint_env,
    resolve_toolbox_endpoint,
    toolbox_mcp_tool,
    validate_toolbox_endpoint,
)
from castia.prompty import McpToolboxError, ToolboxMcpClient

PROJECT = "https://acct.services.ai.azure.com/api/projects/proj"


# --- compose_toolbox_endpoint ------------------------------------------------


def test_compose_unversioned() -> None:
    url = compose_toolbox_endpoint(PROJECT, "hal-smoke")
    assert url == f"{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1"


def test_compose_versioned() -> None:
    url = compose_toolbox_endpoint(PROJECT, "hal-smoke", version="2")
    assert url == f"{PROJECT}/toolboxes/hal-smoke/versions/2/mcp?api-version=v1"


def test_compose_strips_trailing_slash() -> None:
    url = compose_toolbox_endpoint(PROJECT + "/", "hal-smoke")
    assert "//toolboxes" not in url
    assert url == f"{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1"


# --- resolve_toolbox_endpoint ------------------------------------------------


def test_resolve_full_url_env_wins() -> None:
    env = {
        "TOOLBOX_ENDPOINT": "https://x/toolboxes/a/mcp?api-version=v1",
        "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
        "TOOLBOX_NAME": "ignored",
    }
    assert resolve_toolbox_endpoint(env) == env["TOOLBOX_ENDPOINT"]


def test_resolve_mcp_endpoint_alias() -> None:
    env = {"TOOLBOX_MCP_ENDPOINT": "https://x/toolboxes/a/mcp?api-version=v1"}
    assert resolve_toolbox_endpoint(env) == env["TOOLBOX_MCP_ENDPOINT"]


def test_resolve_composes_from_name_and_project() -> None:
    env = {"FOUNDRY_PROJECT_ENDPOINT": PROJECT, "TOOLBOX_NAME": "hal-smoke"}
    assert (
        resolve_toolbox_endpoint(env)
        == f"{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1"
    )


def test_resolve_honors_version_env() -> None:
    env = {
        "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
        "TOOLBOX_NAME": "hal-smoke",
        "TOOLBOX_VERSION": "3",
    }
    assert (
        resolve_toolbox_endpoint(env)
        == f"{PROJECT}/toolboxes/hal-smoke/versions/3/mcp?api-version=v1"
    )


def test_resolve_none_when_unset() -> None:
    assert resolve_toolbox_endpoint({}) is None


def test_resolve_none_when_name_without_project() -> None:
    assert resolve_toolbox_endpoint({"TOOLBOX_NAME": "hal-smoke"}) is None


# --- platform-native TOOLBOX_<NAME>_MCP_ENDPOINT -----------------------------


def test_platform_endpoint_env_normalizes() -> None:
    assert platform_endpoint_env("hal-smoke") == "TOOLBOX_HAL_SMOKE_MCP_ENDPOINT"
    assert platform_endpoint_env("My.Box 2") == "TOOLBOX_MY_BOX_2_MCP_ENDPOINT"


def test_resolve_uses_platform_native_var() -> None:
    env = {
        "TOOLBOX_NAME": "hal-smoke",
        "TOOLBOX_HAL_SMOKE_MCP_ENDPOINT": "https://x/toolboxes/hal-smoke/versions/1/mcp?api-version=v1",
        "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
    }
    # The extension's own var is preferred over composing from the project URL.
    assert (
        resolve_toolbox_endpoint(env)
        == "https://x/toolboxes/hal-smoke/versions/1/mcp?api-version=v1"
    )


def test_resolve_full_url_beats_platform_native() -> None:
    env = {
        "TOOLBOX_ENDPOINT": "https://explicit/mcp",
        "TOOLBOX_NAME": "hal-smoke",
        "TOOLBOX_HAL_SMOKE_MCP_ENDPOINT": "https://platform/mcp",
    }
    assert resolve_toolbox_endpoint(env) == "https://explicit/mcp"


# --- toolbox_mcp_tool --------------------------------------------------------


def test_mcp_tool_none_when_no_endpoint() -> None:
    assert toolbox_mcp_tool(env={}) is None


def test_mcp_tool_required_endpoint_fails_loud() -> None:
    with pytest.raises(ToolboxConfigurationError, match="No toolbox MCP endpoint"):
        toolbox_mcp_tool(env={}, required=True)


def test_foundry_toolbox_endpoint_requires_api_version() -> None:
    with pytest.raises(ToolboxConfigurationError, match="api-version"):
        validate_toolbox_endpoint(f"{PROJECT}/toolboxes/hal-smoke/mcp")


def test_mcp_tool_basic_shape() -> None:
    spec = toolbox_mcp_tool("https://x/mcp")
    assert spec == {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
        "require_approval": "never",
    }


def test_mcp_tool_require_approval_override() -> None:
    spec = toolbox_mcp_tool("https://x/mcp", require_approval="always")
    assert spec is not None
    assert spec["require_approval"] == "always"


def test_mcp_tool_allowed_tools_listified() -> None:
    spec = toolbox_mcp_tool("https://x/mcp", allowed_tools=("web", "files"))
    assert spec is not None
    assert spec["allowed_tools"] == ["web", "files"]


def test_mcp_tool_omits_allowed_tools_when_none() -> None:
    spec = toolbox_mcp_tool("https://x/mcp")
    assert spec is not None
    assert "allowed_tools" not in spec


def test_mcp_tool_token_becomes_bearer_header() -> None:
    spec = toolbox_mcp_tool("https://x/mcp", token="ABC123")
    assert spec is not None
    assert spec["headers"] == {"Authorization": "Bearer ABC123"}


def test_mcp_tool_project_connection_id() -> None:
    spec = toolbox_mcp_tool("https://x/mcp", project_connection_id="conn-1")
    assert spec is not None
    assert spec["project_connection_id"] == "conn-1"
    assert "headers" not in spec


def test_mcp_tool_extra_headers_merge() -> None:
    spec = toolbox_mcp_tool(
        "https://x/mcp", token="ABC", headers={"x-custom": "1"}
    )
    assert spec is not None
    assert spec["headers"] == {"Authorization": "Bearer ABC", "x-custom": "1"}


def test_mcp_tool_reads_endpoint_from_env() -> None:
    env = {"FOUNDRY_PROJECT_ENDPOINT": PROJECT, "TOOLBOX_NAME": "hal-smoke"}
    spec = toolbox_mcp_tool(env=env)
    assert spec is not None
    assert spec["server_url"] == (
        f"{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1"
    )


def test_mcp_tool_overrides_emit_server_description_and_optimizer_defs() -> None:
    spec = toolbox_mcp_tool(
        "https://x/mcp",
        server_label="contracts",
        allowed_tools=("kb-conn___knowledge_base_retrieve",),
        descriptions={
            "knowledge_base_retrieve": "Search contract and billing policy sources."
        },
        param_guidance={
            "knowledge_base_retrieve": {
                "query": "A natural-language contract or billing-policy question."
            }
        },
    )

    assert spec is not None
    assert spec["allowed_tools"] == ["kb-conn___knowledge_base_retrieve"]
    assert "kb-conn___knowledge_base_retrieve" in spec["server_description"]
    assert "Search contract and billing policy sources." in spec["server_description"]

    optimizer_defs = spec[OPTIMIZER_TOOL_DEFINITIONS_KEY]
    assert optimizer_defs == [
        {
            "type": "function",
            "function": {
                "name": "kb-conn___knowledge_base_retrieve",
                "description": "Search contract and billing policy sources.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "description": (
                                "A natural-language contract or billing-policy question."
                            )
                        }
                    },
                    "required": [],
                    "additionalProperties": True,
                },
            },
        }
    ]


def test_mcp_tool_overrides_accept_selected_tool_name() -> None:
    final = "kb-conn___knowledge_base_retrieve"
    spec = toolbox_mcp_tool(
        "https://x/mcp",
        server_label="contracts",
        allowed_tools=("kb-conn___knowledge_base_retrieve",),
        descriptions={final: "Use this for contracts."},
    )

    assert spec is not None
    assert spec[OPTIMIZER_TOOL_DEFINITIONS_KEY][0]["function"]["name"] == final


def test_mcp_tool_overrides_accept_legacy_labeled_name_alias() -> None:
    spec = toolbox_mcp_tool(
        "https://x/mcp",
        server_label="contracts",
        allowed_tools=("kb-conn___knowledge_base_retrieve",),
        descriptions={
            "contracts___kb-conn___knowledge_base_retrieve": (
                "Use this for contracts."
            )
        },
    )

    assert spec is not None
    assert spec[OPTIMIZER_TOOL_DEFINITIONS_KEY][0]["function"]["name"] == (
        "kb-conn___knowledge_base_retrieve"
    )


def test_mcp_tool_overrides_require_allowed_tools() -> None:
    with pytest.raises(ValueError, match="allowed_tools is required"):
        toolbox_mcp_tool(
            "https://x/mcp",
            descriptions={"knowledge_base_retrieve": "Search contracts."},
        )


def test_mcp_tool_overrides_reject_unknown_keys() -> None:
    with pytest.raises(ValueError, match="unknown toolbox descriptions key"):
        toolbox_mcp_tool(
            "https://x/mcp",
            allowed_tools=("knowledge_base_retrieve",),
            descriptions={"stale_tool": "Search contracts."},
        )


def test_mcp_tool_overrides_reject_ambiguous_bare_keys() -> None:
    with pytest.raises(ValueError, match="fully qualified toolbox tool name"):
        toolbox_mcp_tool(
            "https://x/mcp",
            allowed_tools=("one___search", "two___search"),
            descriptions={"search": "Ambiguous."},
        )


def test_mcp_tool_overrides_reject_waypoint_ambiguous_kb_key() -> None:
    with pytest.raises(ValueError) as exc:
        toolbox_mcp_tool(
            "https://x/mcp",
            allowed_tools=(
                "knowledge_base_retrieve",
                "contracts-kb-mcp___knowledge_base_retrieve",
            ),
            descriptions={
                "knowledge_base_retrieve": (
                    "Search contract and billing policy sources."
                )
            },
        )

    message = str(exc.value)
    assert "multiple allowed tools share the bare name 'knowledge_base_retrieve'" in message
    assert "knowledge_base_retrieve" in message
    assert "contracts-kb-mcp___knowledge_base_retrieve" in message
    assert "fully qualified toolbox tool name" in message


# --- knowledge_base_mcp_tool -------------------------------------------------


def test_kb_tool_default_allowed_tools() -> None:
    spec = knowledge_base_mcp_tool("https://s/knowledgebases/kb/mcp?api-version=v1")
    assert spec["allowed_tools"] == ["knowledge_base_retrieve"]
    assert spec["server_label"] == "knowledge-base"


def test_kb_tool_search_token_header() -> None:
    spec = knowledge_base_mcp_tool(
        "https://s/knowledgebases/kb/mcp?api-version=v1", search_token="tok"
    )
    assert spec["headers"] == {"x-ms-query-source-authorization": "tok"}


def test_kb_tool_accepts_default_tool_description_override() -> None:
    spec = knowledge_base_mcp_tool(
        "https://s/knowledgebases/kb/mcp?api-version=v1",
        descriptions={"knowledge_base_retrieve": "Search the KB."},
    )

    assert spec[OPTIMIZER_TOOL_DEFINITIONS_KEY][0]["function"]["name"] == (
        "knowledge_base_retrieve"
    )


def test_kb_tool_never_none() -> None:
    spec = knowledge_base_mcp_tool("https://s/knowledgebases/kb/mcp?api-version=v1")
    assert spec is not None


# --- misc --------------------------------------------------------------------


def test_scope_constant() -> None:
    assert AI_FOUNDRY_SCOPE == "https://ai.azure.com/.default"


# --- local MCP client diagnostics -------------------------------------------


def test_toolbox_mcp_client_reports_zero_tools() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": []}},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = ToolboxMcpClient(
                "https://example.test/mcp",
                token_provider=lambda: "TOKEN",
                client=http,
            )
            with pytest.raises(McpToolboxError, match="zero tools"):
                await client.list_tools()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (
            {"code": "CONSENT_REQUIRED", "message": "admin consent needed"},
            "CONSENT_REQUIRED",
        ),
        ({"code": -32602, "message": "unknown tool lookup"}, "Tool not found"),
        (
            {"code": -32602, "message": "invalid arguments schema"},
            "schema/tool-call",
        ),
    ],
)
def test_toolbox_mcp_client_classifies_tool_call_errors(error, match) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": payload["id"], "error": error},
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = ToolboxMcpClient(
                "https://example.test/mcp",
                token_provider=lambda: "TOKEN",
                client=http,
            )
            with pytest.raises(McpToolboxError, match=match):
                await client.call_tool("lookup", {})

    asyncio.run(run())


def test_toolbox_mcp_client_reports_token_failure() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as http:
            client = ToolboxMcpClient(
                "https://example.test/mcp",
                token_provider=lambda: (_ for _ in ()).throw(RuntimeError("no token")),
                client=http,
            )
            with pytest.raises(
                McpToolboxError,
                match="Toolbox token acquisition failed",
            ):
                await client.list_tools()

    asyncio.run(run())


def test_toolbox_mcp_client_rejects_foundry_endpoint_without_api_version() -> None:
    with pytest.raises(ValueError, match="api-version"):
        ToolboxMcpClient(
            "https://acct.services.ai.azure.com/api/projects/p/toolboxes/t/mcp",
            token_provider=lambda: "TOKEN",
        )

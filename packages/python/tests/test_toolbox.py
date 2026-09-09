"""Hermetic unit tests for :mod:`castia.toolbox`.

All pure builders -- no network, no real credential. Exercises endpoint
resolution precedence, the ``mcp`` tool spec shape, auth folding (bearer header
vs. ``project_connection_id``), and the Foundry IQ knowledge-base variant. The
one impure seam (:func:`castia.toolbox.toolbox_token`) is not called here.
"""

from __future__ import annotations

from castia.toolbox import (
    AI_FOUNDRY_SCOPE,
    compose_toolbox_endpoint,
    knowledge_base_mcp_tool,
    platform_endpoint_env,
    resolve_toolbox_endpoint,
    toolbox_mcp_tool,
)

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


# --- knowledge_base_mcp_tool -------------------------------------------------


def test_kb_tool_default_allowed_tools() -> None:
    spec = knowledge_base_mcp_tool("https://s/knowledgebases/kb/mcp")
    assert spec["allowed_tools"] == ["knowledge_base_retrieve"]
    assert spec["server_label"] == "knowledge-base"


def test_kb_tool_search_token_header() -> None:
    spec = knowledge_base_mcp_tool(
        "https://s/knowledgebases/kb/mcp", search_token="tok"
    )
    assert spec["headers"] == {"x-ms-query-source-authorization": "tok"}


def test_kb_tool_never_none() -> None:
    spec = knowledge_base_mcp_tool("https://s/knowledgebases/kb/mcp")
    assert spec is not None


# --- misc --------------------------------------------------------------------


def test_scope_constant() -> None:
    assert AI_FOUNDRY_SCOPE == "https://ai.azure.com/.default"

use castia::integrations::{
    compose_toolbox_endpoint, knowledge_base_mcp_tool, platform_endpoint_env,
    resolve_toolbox_endpoint, toolbox_mcp_tool, AI_FOUNDRY_SCOPE,
};
use castia::optimizing::OPTIMIZER_TOOL_DEFINITIONS_KEY;
use serde_json::json;

const PROJECT: &str = "https://acct.services.ai.azure.com/api/projects/proj";

#[test]
fn composes_toolbox_endpoints_like_python() {
    assert_eq!(
        compose_toolbox_endpoint(&format!("{PROJECT}/"), "hal-smoke", None),
        format!("{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1")
    );
    assert_eq!(
        compose_toolbox_endpoint(PROJECT, "hal-smoke", Some("2")),
        format!("{PROJECT}/toolboxes/hal-smoke/versions/2/mcp?api-version=v1")
    );
}

#[test]
fn resolves_endpoint_precedence_like_python() {
    assert_eq!(
        platform_endpoint_env("My.Box 2"),
        "TOOLBOX_MY_BOX_2_MCP_ENDPOINT"
    );
    assert_eq!(
        resolve_toolbox_endpoint(&json!({
            "TOOLBOX_ENDPOINT": "https://explicit/mcp",
            "TOOLBOX_NAME": "hal-smoke",
            "TOOLBOX_HAL_SMOKE_MCP_ENDPOINT": "https://platform/mcp",
            "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
        }))
        .as_deref(),
        Some("https://explicit/mcp")
    );
    assert_eq!(
        resolve_toolbox_endpoint(&json!({
            "TOOLBOX_NAME": "hal-smoke",
            "TOOLBOX_HAL_SMOKE_MCP_ENDPOINT": "https://platform/mcp",
            "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
        }))
        .as_deref(),
        Some("https://platform/mcp")
    );
    assert_eq!(
        resolve_toolbox_endpoint(&json!({
            "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
            "TOOLBOX_NAME": "hal-smoke",
            "TOOLBOX_VERSION": "3",
        }))
        .as_deref(),
        Some(format!("{PROJECT}/toolboxes/hal-smoke/versions/3/mcp?api-version=v1").as_str())
    );
    assert_eq!(resolve_toolbox_endpoint(&json!({})), None);
    assert_eq!(
        resolve_toolbox_endpoint(&json!({
            "FOUNDRY_PROJECT_ENDPOINT": PROJECT,
            "TOOLBOX_NAME": "",
        })),
        None
    );
}

#[test]
fn builds_mcp_tool_spec_with_auth_headers_and_env_endpoint() {
    assert_eq!(toolbox_mcp_tool(&json!({})).unwrap(), json!(null));
    assert_eq!(
        toolbox_mcp_tool(&json!({
            "env": {"FOUNDRY_PROJECT_ENDPOINT": PROJECT, "TOOLBOX_NAME": "hal-smoke"},
            "allowedTools": ["web", "files"],
            "token": "tok",
            "headers": {"x-custom": "1"},
            "projectConnectionId": "conn-1",
        }))
        .unwrap(),
        json!({
            "type": "mcp",
            "server_label": "toolbox",
            "server_url": format!("{PROJECT}/toolboxes/hal-smoke/mcp?api-version=v1"),
            "require_approval": "never",
            "allowed_tools": ["web", "files"],
            "project_connection_id": "conn-1",
            "headers": {"Authorization": "Bearer tok", "x-custom": "1"},
        })
    );
}

#[test]
fn override_sidecars_accept_aliases_and_reject_bad_keys() {
    let spec = toolbox_mcp_tool(&json!({
        "endpoint": "https://x/mcp",
        "serverLabel": "contracts",
        "allowedTools": ["kb-conn___knowledge_base_retrieve"],
        "descriptions": {"contracts___kb-conn___knowledge_base_retrieve": "Use this for contracts."},
        "paramGuidance": {"knowledge_base_retrieve": {"query": "Ask a policy question."}},
    }))
    .unwrap();
    assert_eq!(
        spec[OPTIMIZER_TOOL_DEFINITIONS_KEY][0]["function"]["name"],
        "kb-conn___knowledge_base_retrieve"
    );
    assert!(spec["server_description"]
        .as_str()
        .unwrap()
        .contains("Ask a policy question."));

    let err = toolbox_mcp_tool(&json!({
        "endpoint": "https://x/mcp",
        "descriptions": {"knowledge_base_retrieve": "Search contracts."},
    }))
    .unwrap_err();
    assert!(err.to_string().contains("allowed_tools is required"));

    let err = toolbox_mcp_tool(&json!({
        "endpoint": "https://x/mcp",
        "allowedTools": ["one___search", "two___search"],
        "descriptions": {"search": "Ambiguous."},
    }))
    .unwrap_err();
    assert!(err
        .to_string()
        .contains("ambiguous toolbox descriptions key"));

    let err = toolbox_mcp_tool(&json!({
        "endpoint": "https://x/mcp",
        "allowedTools": ["web", 1],
    }))
    .unwrap_err();
    assert_eq!(err.to_string(), "allowed_tools must be a list of strings");

    let fallback = toolbox_mcp_tool(&json!({
        "endpoint": "https://x/mcp",
        "allowedTools": ["web"],
        "descriptions": {"web": ""},
    }))
    .unwrap();
    assert_eq!(
        fallback[OPTIMIZER_TOOL_DEFINITIONS_KEY][0]["function"]["description"],
        "Call the federated toolbox tool 'web'."
    );
}

#[test]
fn knowledge_base_tool_sets_defaults_and_search_header() {
    let spec = knowledge_base_mcp_tool(&json!({
        "endpoint": "https://s/knowledgebases/kb/mcp",
        "searchToken": "tok",
    }))
    .unwrap();
    assert_eq!(spec["server_label"], "knowledge-base");
    assert_eq!(spec["allowed_tools"], json!(["knowledge_base_retrieve"]));
    assert_eq!(spec["headers"]["x-ms-query-source-authorization"], "tok");
    assert_eq!(AI_FOUNDRY_SCOPE, "https://ai.azure.com/.default");
}

use castia::observe::{execute_tool_span, identity_attributes, invoke_agent_span, PROVIDER};
use serde_json::json;

#[test]
fn invoke_agent_span_uses_foundry_provider_env_identity_and_version() {
    let span = invoke_agent_span(None, Some("hal-autopilot"), Some("3"), None);
    assert_eq!(span["tracer_name"], "castia");
    assert_eq!(span["span_name"], "invoke_agent hal-autopilot");
    assert_eq!(span["attributes"]["gen_ai.operation.name"], "invoke_agent");
    assert_eq!(span["attributes"]["gen_ai.system"], PROVIDER);
    assert_eq!(span["attributes"]["gen_ai.provider.name"], PROVIDER);
    assert_eq!(span["attributes"]["gen_ai.agent.name"], "hal-autopilot");
    assert_eq!(span["attributes"]["gen_ai.agent.id"], "hal-autopilot:3");
    assert_eq!(span["attributes"]["gen_ai.agent.version"], "3");
}

#[test]
fn invoke_agent_explicit_name_wins_and_empty_version_is_absent() {
    let span = invoke_agent_span(
        Some("example"),
        Some("hal-autopilot"),
        Some(""),
        Some("custom.system"),
    );
    assert_eq!(span["span_name"], "invoke_agent example");
    assert_eq!(span["attributes"]["gen_ai.system"], "custom.system");
    assert_eq!(span["attributes"]["gen_ai.provider.name"], "custom.system");
    assert_eq!(span["attributes"]["gen_ai.agent.name"], "example");
    assert!(span["attributes"].get("gen_ai.agent.id").is_none());
}

#[test]
fn invoke_agent_empty_env_name_is_preserved() {
    let span = invoke_agent_span(None, Some(""), None, None);
    assert_eq!(span["span_name"], "invoke_agent ");
    assert_eq!(span["attributes"]["gen_ai.agent.name"], "");
}

#[test]
fn execute_tool_span_labels_tool_operation_and_name() {
    let span = execute_tool_span("lookup", None);
    assert_eq!(span["tracer_name"], "castia");
    assert_eq!(span["span_name"], "execute_tool lookup");
    assert_eq!(
        span["attributes"],
        json!({
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.system": PROVIDER,
            "gen_ai.provider.name": PROVIDER,
            "gen_ai.tool.name": "lookup",
        })
    );
}

#[test]
fn identity_attributes_omits_empty_version() {
    assert_eq!(
        identity_attributes("agent", Some("")),
        serde_json::Map::from_iter([("gen_ai.agent.name".to_string(), json!("agent"))])
    );
}

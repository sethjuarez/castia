use castia::delivery::{
    plan_manifest, publishable_protocols, service_protocol_items, skipped_protocols,
};
use serde_json::json;

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| value.to_string()).collect()
}

#[test]
fn filters_publishable_and_skipped_protocols() {
    let registered = strings(&["activity", "chat", "responses"]);
    assert_eq!(
        publishable_protocols(&registered),
        strings(&["activity", "responses"])
    );
    assert_eq!(skipped_protocols(&registered), strings(&["chat"]));
}

#[test]
fn builds_service_protocol_items_with_pinned_versions() {
    assert_eq!(
        service_protocol_items(&strings(&["activity", "responses"])),
        json!([
            {"protocol": "activity", "version": "2.0.0"},
            {"protocol": "responses", "version": "2.0.0"},
        ])
    );
}

#[test]
fn plans_named_service_and_endpoint_protocol_updates() {
    let plan = plan_manifest(
        "my-agent",
        &strings(&["activity", "chat", "responses"]),
        &json!({
            "services": {
                "my-agent": {
                    "protocols": [{"protocol": "activity", "version": "1.0.0"}],
                    "agentEndpoint": {"protocols": ["activity"]},
                }
            }
        }),
        true,
    )
    .unwrap();
    assert_eq!(plan["service"], "my-agent");
    assert_eq!(plan["desired"], json!(["activity", "responses"]));
    assert_eq!(plan["skipped"], json!(["chat"]));
    assert_eq!(plan["before_service"], json!(["activity"]));
    assert_eq!(plan["after_endpoint"], json!(["activity", "responses"]));
    assert_eq!(plan["changed"], true);
    assert_eq!(plan["written"], false);

    let written = plan_manifest(
        "my-agent",
        &strings(&["activity", "chat", "responses"]),
        &json!({
            "services": {
                "my-agent": {
                    "protocols": [{"protocol": "activity", "version": "1.0.0"}],
                    "agentEndpoint": {"protocols": ["activity"]},
                }
            }
        }),
        false,
    )
    .unwrap();
    assert_eq!(written["written"], true);
    assert_eq!(
        written["notes"],
        json!([
            "local-only protocol(s) not published to Foundry: chat",
            "authorizationSchemes left as authored; a non-activity protocol may need its own scheme -- review before deploy",
        ])
    );
}

#[test]
fn notes_when_endpoint_exists_without_activity_protocol() {
    let plan = plan_manifest(
        "my-agent",
        &strings(&["responses"]),
        &json!({
            "services": {
                "my-agent": {
                    "protocols": [{"protocol": "activity", "version": "2.0.0"}],
                    "agentEndpoint": {"protocols": ["activity"]},
                }
            }
        }),
        true,
    )
    .unwrap();
    assert_eq!(plan["desired"], json!(["responses"]));
    assert_eq!(
        plan["notes"],
        json!([
            "agentEndpoint present but 'activity' is not registered; review whether the Bot Service endpoint should remain"
        ])
    );
}

#[test]
fn single_service_fallback_and_multiple_service_error_match_python() {
    let plan = plan_manifest(
        "agent-name",
        &strings(&["responses"]),
        &json!({"services": {"svc": {"protocols": [{"protocol": "responses"}]}}}),
        true,
    )
    .unwrap();
    assert_eq!(plan["service"], "svc");
    assert_eq!(plan["changed"], false);

    let err = plan_manifest(
        "missing",
        &strings(&["responses"]),
        &json!({"services": {"one": {}, "two": {}}}),
        true,
    )
    .unwrap_err();
    assert_eq!(
        err.to_string(),
        "agent name 'missing' not among services ['one', 'two']; cannot pick which service to update"
    );
}

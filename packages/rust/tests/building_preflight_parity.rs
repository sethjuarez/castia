use castia::building::{
    deployment_context_diagnostics, preflight_report, required_env_diagnostics,
};
use serde_json::json;

fn status(rows: &serde_json::Value, check: &str) -> String {
    rows.as_array()
        .unwrap()
        .iter()
        .find(|row| row["check"] == check)
        .unwrap()["status"]
        .as_str()
        .unwrap()
        .to_string()
}

fn has_check(rows: &serde_json::Value, check: &str) -> bool {
    rows.as_array()
        .unwrap()
        .iter()
        .any(|row| row["check"] == check)
}

#[test]
fn required_configuration_is_reported_without_echoing_values() {
    let diagnostics = required_env_diagnostics(
        &json!({"FOUNDRY_PROJECT_ENDPOINT": "SECRET-TOKEN"}),
        &[
            "FOUNDRY_PROJECT_ENDPOINT".to_string(),
            "AZURE_AI_MODEL_DEPLOYMENT_NAME".to_string(),
        ],
    );
    assert_eq!(
        status(&diagnostics, "configuration.FOUNDRY_PROJECT_ENDPOINT"),
        "pass"
    );
    assert_eq!(
        status(&diagnostics, "configuration.AZURE_AI_MODEL_DEPLOYMENT_NAME"),
        "fail"
    );
    assert_eq!(status(&diagnostics, "cloud"), "skipped");
    assert!(!diagnostics.to_string().contains("SECRET-TOKEN"));
}

#[test]
fn deployment_context_is_advisory_unless_requested() {
    let local = deployment_context_diagnostics(&json!({}), false);
    assert_eq!(status(&local, "azd.AZURE_LOCATION"), "warning");
    assert_eq!(status(&local, "azd.AZURE_AI_PROJECT_ID"), "warning");

    let deployment = deployment_context_diagnostics(&json!({}), true);
    assert_eq!(status(&deployment, "azd.AZURE_LOCATION"), "fail");
    assert_eq!(status(&deployment, "azd.AZURE_AI_PROJECT_ID"), "fail");
    assert_eq!(status(&deployment, "azd.tenant"), "skipped");
    assert_eq!(status(&deployment, "azd.environment"), "skipped");
}

#[test]
fn deployment_context_validates_arm_id_shape_without_echoing_values() {
    let good = deployment_context_diagnostics(
        &json!({
            "AZURE_LOCATION": "private-region-value",
            "AZURE_SUBSCRIPTION_ID": "private-subscription",
            "AZURE_AI_PROJECT_ID": "/subscriptions/private-subscription/resourceGroups/private-group/providers/Microsoft.CognitiveServices/accounts/private-account/projects/private-project"
        }),
        true,
    );
    assert_eq!(status(&good, "azd.AZURE_LOCATION"), "pass");
    assert_eq!(status(&good, "azd.AZURE_AI_PROJECT_ID"), "pass");
    assert!(!has_check(&good, "azd.project_arm_id"));
    assert!(!good.to_string().contains("private-"));

    let bad = deployment_context_diagnostics(
        &json!({
            "AZURE_LOCATION": "private-region-value",
            "AZURE_SUBSCRIPTION_ID": "private-subscription",
            "AZURE_AI_PROJECT_ID": "https://project.invalid"
        }),
        true,
    );
    assert_eq!(status(&bad, "azd.project_arm_id"), "fail");
    assert!(!bad.to_string().contains("private-"));
}

#[test]
fn report_ok_is_false_when_any_diagnostic_fails() {
    let diagnostics = json!([
        {"check": "configuration.FOUNDRY_PROJECT_ENDPOINT", "status": "pass", "message": "Required setting is present."},
        {"check": "registration", "status": "fail", "message": "Cannot load/compile Agent (RuntimeError)."}
    ]);
    let report = preflight_report(
        "/tmp/agent",
        "main:app",
        &["responses".to_string()],
        &diagnostics,
    );
    assert_eq!(report["ok"], false);
    assert_eq!(report["scope"], "offline");
    assert_eq!(report["protocols"], json!(["responses"]));
    assert_eq!(report["diagnostics"], diagnostics);

    let warning_only = preflight_report(
        "/tmp/agent",
        "main:app",
        &["responses".to_string()],
        &json!([
            {"check": "configuration.FOUNDRY_PROJECT_ENDPOINT", "status": "pass", "message": "Required setting is present."},
            {"check": "azd.AZURE_LOCATION", "status": "warning", "message": "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources."},
            {"check": "cloud", "status": "skipped", "message": "Credentials, RBAC, model availability and provisioning not checked."}
        ]),
    );
    assert_eq!(warning_only["ok"], true);
    assert_eq!(
        preflight_report("/tmp/agent", "main:app", &[], &json!({"bad": true}))["ok"],
        false
    );
}

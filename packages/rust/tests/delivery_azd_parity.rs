use castia::delivery::{
    command_error_message, validate_deployment_input, validate_environment_values, verify_payload,
};
use serde_json::json;

const ENDPOINT: &str = "https://test.services.ai.azure.com/api/projects/project";
const SUBSCRIPTION: &str = "11111111-1111-1111-1111-111111111111";
const RESOURCE: &str = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project";

fn environment() -> serde_json::Value {
    json!({
        "AZURE_ENV_NAME": "dev",
        "FOUNDRY_PROJECT_ENDPOINT": ENDPOINT,
        "AZURE_AI_PROJECT_ID": RESOURCE,
        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION,
    })
}

fn payload() -> serde_json::Value {
    json!({
        "name": "smoke",
        "version": "3",
        "status": "active",
        "definition": {
            "kind": "hosted",
            "environment_variables": {
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "OPTIMIZATION_CANDIDATE_ID": "candidate",
            },
            "code_configuration": {"content_hash": "hash"},
        },
    })
}

#[test]
fn deployment_input_pins_approved_project_and_service() {
    let approved = validate_deployment_input("smoke", None, ENDPOINT, RESOURCE, 900.0).unwrap();
    assert_eq!(approved["service"], "smoke");
    assert_eq!(approved["project_endpoint"], ENDPOINT);
    assert_eq!(approved["project_resource_id"], RESOURCE);
    assert_eq!(approved["subscription_id"], SUBSCRIPTION);

    assert!(validate_deployment_input("../smoke", None, ENDPOINT, RESOURCE, 900.0).is_err());
    assert!(validate_deployment_input("smoke", Some("../dev"), ENDPOINT, RESOURCE, 900.0).is_err());
    assert!(
        validate_deployment_input("smoke", None, "http://example.com", RESOURCE, 900.0).is_err()
    );
    assert!(validate_deployment_input(
        "smoke",
        None,
        ENDPOINT,
        &RESOURCE.replace("/projects/project", "/projects/other"),
        900.0,
    )
    .is_err());
    assert!(validate_deployment_input(
        "smoke",
        None,
        ENDPOINT,
        &RESOURCE.replace(SUBSCRIPTION, "not-a-uuid"),
        900.0,
    )
    .is_err());
}

#[test]
fn environment_values_block_mismatched_context_before_resource_commands() {
    assert_eq!(
        validate_environment_values(&environment(), None, ENDPOINT, RESOURCE, SUBSCRIPTION)
            .unwrap(),
        "dev"
    );
    assert!(validate_environment_values(
        &environment(),
        Some("approved"),
        ENDPOINT,
        RESOURCE,
        SUBSCRIPTION
    )
    .unwrap_err()
    .to_string()
    .contains("different environment"));
    for change in [
        json!({"FOUNDRY_PROJECT_ENDPOINT": "https://other.services.ai.azure.com/api/projects/project"}),
        json!({"FOUNDRY_PROJECT_ENDPOINT": null}),
        json!({"AZURE_AI_PROJECT_ENDPOINT": "https://other.invalid"}),
        json!({"AZURE_AI_PROJECT_ID": RESOURCE.replace("/accounts/test/", "/accounts/other/")}),
        json!({"FOUNDRY_PROJECT_RESOURCE_ID": "/subscriptions/other"}),
        json!({"AZURE_SUBSCRIPTION_ID": "22222222-2222-2222-2222-222222222222"}),
        json!({"AZURE_ENV_NAME": null}),
    ] {
        let mut values = environment();
        values
            .as_object_mut()
            .unwrap()
            .extend(change.as_object().unwrap().clone());
        assert!(
            validate_environment_values(&values, None, ENDPOINT, RESOURCE, SUBSCRIPTION).is_err(),
            "{values}"
        );
    }
}

#[test]
fn verify_payload_rejects_mismatched_or_missing_remote_state() {
    let receipt = verify_payload(
        &payload(),
        "smoke",
        ENDPOINT,
        RESOURCE,
        Some("model"),
        Some("candidate"),
        None,
    )
    .unwrap();
    assert_eq!(receipt["agent_version"], "3");
    assert_eq!(receipt["model"], "model");
    assert_eq!(receipt["candidate_id"], "candidate");
    assert_eq!(receipt["content_hash"], "hash");

    for change in [
        json!({"name": "other"}),
        json!({"status": "failed"}),
        json!({"version": null}),
        json!({"definition": {}}),
    ] {
        let mut value = payload();
        value
            .as_object_mut()
            .unwrap()
            .extend(change.as_object().unwrap().clone());
        assert!(verify_payload(
            &value,
            "smoke",
            ENDPOINT,
            RESOURCE,
            Some("model"),
            None,
            None
        )
        .is_err());
    }
    assert!(verify_payload(
        &payload(),
        "smoke",
        ENDPOINT,
        RESOURCE,
        None,
        Some("different"),
        None
    )
    .is_err());

    let mut no_env = payload();
    no_env["definition"]
        .as_object_mut()
        .unwrap()
        .remove("environment_variables");
    let receipt = verify_payload(&no_env, "smoke", ENDPOINT, RESOURCE, None, None, None).unwrap();
    assert_eq!(receipt["model"], serde_json::Value::Null);
    assert_eq!(receipt["candidate_id"], serde_json::Value::Null);
}

#[test]
fn command_error_message_redacts_external_output() {
    let message = command_error_message(
        &[
            "deploy".to_string(),
            "smoke".to_string(),
            "--no-prompt".to_string(),
            "--secret".to_string(),
            "token".to_string(),
        ],
        1,
        "SECRET-STDOUT",
        "SECRET-STDERR",
    );
    assert_eq!(
        message,
        "azd deploy smoke --no-prompt exited 1; inspect deployment logs and verify remote state before retrying"
    );
    assert!(!message.contains("SECRET"));
    assert!(!message.contains("token"));
}

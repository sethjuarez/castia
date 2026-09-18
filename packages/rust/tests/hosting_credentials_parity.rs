use castia::hosting::{
    agentic_identity_from_env, bearer, bot_connector_credential, hosting_scopes,
    instance_token_request, is_local_run, tenant_token_endpoint, token_response_access_token,
    user_fic_token_request,
};
use serde_json::json;

#[test]
fn local_run_detection_matches_python_auth() {
    assert!(is_local_run(
        &json!({"AGENT_DIGITAL_WORKER": "1", "FOUNDRY_AGENT_TENANT_ID": "tenant"})
    ));
    assert!(is_local_run(&json!({})));
    assert!(!is_local_run(&json!({"FOUNDRY_AGENT_TENANT_ID": "tenant"})));
    assert!(!is_local_run(&json!({"FOUNDRY_AGENT_TENANT_ID": ""})));
    assert!(!is_local_run(
        &json!({"AGENT_DIGITAL_WORKER": "", "FOUNDRY_AGENT_TENANT_ID": "tenant"})
    ));
}

#[test]
fn bearer_and_agentic_identity_shapes_match_python() {
    assert_eq!(bearer("abc123"), "Bearer abc123");
    assert_eq!(
        agentic_identity_from_env(&json!({
            "FOUNDRY_AGENT_TENANT_ID": "tenant",
            "FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID": "blueprint",
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "instance",
            "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID": "default",
        })),
        Some(json!({
            "tenant_id": "tenant",
            "blueprint_client_id": "blueprint",
            "instance_client_id": "instance",
        }))
    );
    assert_eq!(
        agentic_identity_from_env(&json!({
            "FOUNDRY_AGENT_TENANT_ID": "tenant",
            "FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID": "blueprint",
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "",
            "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID": "default",
        })),
        Some(json!({
            "tenant_id": "tenant",
            "blueprint_client_id": "blueprint",
            "instance_client_id": "default",
        }))
    );
    assert_eq!(
        agentic_identity_from_env(
            &json!({"FOUNDRY_AGENT_TENANT_ID": "tenant", "FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID": ""})
        ),
        None
    );
}

#[test]
fn token_request_shapes_match_credentials_chain() {
    assert_eq!(
        tenant_token_endpoint("contoso"),
        "https://login.microsoftonline.com/contoso/oauth2/v2.0/token"
    );
    assert_eq!(
        instance_token_request("instance", "assertion"),
        json!({
            "client_id": "instance",
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": "assertion",
            "scope": "api://AzureAdTokenExchange/.default",
        })
    );
    assert_eq!(
        user_fic_token_request(
            "instance",
            "assertion",
            "instance-token",
            "user",
            "https://graph.microsoft.com/.default"
        )
        .unwrap(),
        json!({
            "client_id": "instance",
            "grant_type": "user_fic",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": "assertion",
            "user_federated_identity_credential": "instance-token",
            "user_id": "user",
            "scope": "https://graph.microsoft.com/.default",
        })
    );
    assert_eq!(
        user_fic_token_request("instance", "assertion", "instance-token", "", "scope")
            .unwrap_err()
            .to_string(),
        "agentic_user_id is required"
    );
}

#[test]
fn bot_connector_credential_prefers_default_instance_identity() {
    assert_eq!(
        bot_connector_credential(&json!({
            "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID": "default",
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "instance",
        })),
        json!({
            "credential": "managed_identity",
            "client_id": "default",
            "scope": "https://api.botframework.com/.default",
        })
    );
    assert_eq!(
        bot_connector_credential(&json!({})),
        json!({
            "credential": "default",
            "client_id": null,
            "scope": "https://api.botframework.com/.default",
        })
    );
    assert_eq!(
        bot_connector_credential(&json!({
            "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID": "",
            "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "instance",
        })),
        json!({
            "credential": "managed_identity",
            "client_id": "instance",
            "scope": "https://api.botframework.com/.default",
        })
    );
    assert_eq!(
        hosting_scopes(),
        json!({
            "botframework": "https://api.botframework.com/.default",
            "graph": "https://graph.microsoft.com/.default",
            "apxProduction": "5a807f24-c9de-44ee-a3a7-329e88a00ffc/.default",
        })
    );
}

#[test]
fn token_response_access_token_matches_python_errors() {
    assert_eq!(
        token_response_access_token(200, &json!({"access_token": "token"}), "{}").unwrap(),
        "token"
    );
    assert_eq!(
        token_response_access_token(403, &json!({"error": "forbidden"}), "forbidden")
            .unwrap_err()
            .to_string(),
        "token endpoint returned 403: forbidden"
    );
    assert_eq!(
        token_response_access_token(200, &json!({"token_type": "Bearer"}), "{}")
            .unwrap_err()
            .to_string(),
        "token endpoint response had no access_token"
    );
}

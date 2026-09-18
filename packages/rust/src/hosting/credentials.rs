use crate::model::HostingCredentialsRuntime;
use serde_json::{json, Value};

const TOKEN_EXCHANGE_SCOPE: &str = "api://AzureAdTokenExchange/.default";
const JWT_BEARER: &str = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer";
pub const BOTFRAMEWORK_SCOPE: &str = "https://api.botframework.com/.default";
pub const GRAPH_SCOPE: &str = "https://graph.microsoft.com/.default";
pub const APX_PRODUCTION_SCOPE: &str = "5a807f24-c9de-44ee-a3a7-329e88a00ffc/.default";

#[derive(Debug, Clone)]
pub struct CredentialError(String);

impl std::fmt::Display for CredentialError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for CredentialError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaHostingCredentialsRuntime;

#[async_trait::async_trait]
impl HostingCredentialsRuntime for CastiaHostingCredentialsRuntime {
    fn agentic_identity_from_env(&self, env: &Value) -> Value {
        agentic_identity_from_env(env).unwrap_or(Value::Null)
    }

    fn bearer(&self, token: &String) -> String {
        bearer(token)
    }

    fn bot_connector_credential(&self, env: &Value) -> Value {
        bot_connector_credential(env)
    }

    fn instance_token_request(
        &self,
        instance_client_id: &String,
        agent_assertion: &String,
    ) -> Value {
        instance_token_request(instance_client_id, agent_assertion)
    }

    fn hosting_scopes(&self) -> Value {
        hosting_scopes()
    }

    fn is_local_run(&self, env: &Value) -> bool {
        is_local_run(env)
    }

    fn tenant_token_endpoint(&self, tenant_id: &String) -> String {
        tenant_token_endpoint(tenant_id)
    }

    fn token_response_access_token(&self, status: &i32, body: &Value, text: &String) -> String {
        token_response_access_token(*status, body, text)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn user_fic_token_request(
        &self,
        instance_client_id: &String,
        agent_assertion: &String,
        instance_token: &String,
        agentic_user_id: &String,
        scope: &String,
    ) -> Value {
        user_fic_token_request(
            instance_client_id,
            agent_assertion,
            instance_token,
            agentic_user_id,
            scope,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn bearer(token: &str) -> String {
    format!("Bearer {token}")
}

pub fn is_local_run(env: &Value) -> bool {
    let env = env.as_object();
    if non_empty(env, "AGENT_DIGITAL_WORKER").is_some() {
        return true;
    }
    env.and_then(|env| env.get("FOUNDRY_AGENT_TENANT_ID"))
        .is_none()
}

pub fn agentic_identity_from_env(env: &Value) -> Option<Value> {
    let env = env.as_object();
    let tenant_id = non_empty(env, "FOUNDRY_AGENT_TENANT_ID")?;
    let blueprint_client_id = non_empty(env, "FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID")?;
    let instance_client_id = non_empty(env, "FOUNDRY_AGENT_INSTANCE_CLIENT_ID")
        .or_else(|| non_empty(env, "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID"))?;
    Some(json!({
        "tenant_id": tenant_id,
        "blueprint_client_id": blueprint_client_id,
        "instance_client_id": instance_client_id,
    }))
}

pub fn tenant_token_endpoint(tenant_id: &str) -> String {
    format!("https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token")
}

pub fn instance_token_request(instance_client_id: &str, agent_assertion: &str) -> Value {
    json!({
        "client_id": instance_client_id,
        "grant_type": "client_credentials",
        "client_assertion_type": JWT_BEARER,
        "client_assertion": agent_assertion,
        "scope": TOKEN_EXCHANGE_SCOPE,
    })
}

pub fn user_fic_token_request(
    instance_client_id: &str,
    agent_assertion: &str,
    instance_token: &str,
    agentic_user_id: &str,
    scope: &str,
) -> Result<Value, CredentialError> {
    if agentic_user_id.is_empty() {
        return Err(CredentialError("agentic_user_id is required".to_string()));
    }
    Ok(json!({
        "client_id": instance_client_id,
        "grant_type": "user_fic",
        "client_assertion_type": JWT_BEARER,
        "client_assertion": agent_assertion,
        "user_federated_identity_credential": instance_token,
        "user_id": agentic_user_id,
        "scope": scope,
    }))
}

pub fn bot_connector_credential(env: &Value) -> Value {
    let env = env.as_object();
    if let Some(client_id) = non_empty(env, "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID")
        .or_else(|| non_empty(env, "FOUNDRY_AGENT_INSTANCE_CLIENT_ID"))
    {
        json!({
            "credential": "managed_identity",
            "client_id": client_id,
            "scope": BOTFRAMEWORK_SCOPE,
        })
    } else {
        json!({
            "credential": "default",
            "client_id": null,
            "scope": BOTFRAMEWORK_SCOPE,
        })
    }
}

pub fn hosting_scopes() -> Value {
    json!({
        "botframework": BOTFRAMEWORK_SCOPE,
        "graph": GRAPH_SCOPE,
        "apxProduction": APX_PRODUCTION_SCOPE,
    })
}

pub fn token_response_access_token(
    status: i32,
    body: &Value,
    text: &str,
) -> Result<String, CredentialError> {
    if status != 200 {
        return Err(CredentialError(format!(
            "token endpoint returned {status}: {}",
            truncate_chars(text, 500)
        )));
    }
    body.get("access_token")
        .and_then(Value::as_str)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
        .ok_or_else(|| CredentialError("token endpoint response had no access_token".to_string()))
}

fn non_empty<'a>(env: Option<&'a serde_json::Map<String, Value>>, key: &str) -> Option<&'a str> {
    env.and_then(|env| env.get(key))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn truncate_chars(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

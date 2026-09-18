use crate::model::DeliveryAzdRuntime;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;
use url::Url;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct DeliveryError(String);

impl std::fmt::Display for DeliveryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DeliveryError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaDeliveryAzdRuntime;

#[async_trait::async_trait]
impl DeliveryAzdRuntime for CastiaDeliveryAzdRuntime {
    fn command_error_message(
        &self,
        command: &Vec<String>,
        return_code: &i32,
        stdout: &String,
        stderr: &String,
    ) -> String {
        command_error_message(command, *return_code, stdout, stderr)
    }

    fn validate_deployment_input(
        &self,
        service: &String,
        environment: &Option<String>,
        project_endpoint: &String,
        project_resource_id: &String,
        timeout: &f64,
    ) -> Value {
        validate_deployment_input(
            service,
            environment.as_deref(),
            project_endpoint,
            project_resource_id,
            *timeout,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn validate_environment_values(
        &self,
        values: &Value,
        requested_environment: &Option<String>,
        project_endpoint: &String,
        project_resource_id: &String,
        subscription_id: &String,
    ) -> Value {
        Value::String(
            validate_environment_values(
                values,
                requested_environment.as_deref(),
                project_endpoint,
                project_resource_id,
                subscription_id,
            )
            .unwrap_or_else(|error| panic!("{}", error.0)),
        )
    }

    fn verify_payload(
        &self,
        payload: &Value,
        service: &String,
        project_endpoint: &String,
        project_resource_id: &String,
        expected_model: &Option<String>,
        expected_candidate: &Option<String>,
        expected_version: &Option<String>,
    ) -> Value {
        verify_payload(
            payload,
            service,
            project_endpoint,
            project_resource_id,
            expected_model.as_deref(),
            expected_candidate.as_deref(),
            expected_version.as_deref(),
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn validate_deployment_input(
    service: &str,
    environment: Option<&str>,
    project_endpoint: &str,
    project_resource_id: &str,
    timeout: f64,
) -> Result<Value, DeliveryError> {
    if !plain_azd_name(service) {
        return Err(DeliveryError(
            "service must be a plain azure.yaml service name".to_string(),
        ));
    }
    if environment.is_some_and(|value| !plain_azd_name(value)) {
        return Err(DeliveryError(
            "environment must be a plain azd environment name".to_string(),
        ));
    }
    validate_endpoint(project_endpoint)?;
    let (subscription_id, project_name) = parse_resource(project_resource_id)?;
    if !timeout.is_finite() || timeout <= 0.0 {
        return Err(DeliveryError(
            "timeout must be finite and positive".to_string(),
        ));
    }
    let endpoint_project = endpoint_project(project_endpoint)?;
    if endpoint_project.to_casefold() != project_name.to_casefold() {
        return Err(DeliveryError(
            "project endpoint and ARM resource must name the same project".to_string(),
        ));
    }
    Ok(json!({
        "service": service,
        "environment": environment,
        "project_endpoint": project_endpoint.trim_end_matches('/'),
        "project_resource_id": project_resource_id.trim_end_matches('/'),
        "subscription_id": subscription_id,
        "timeout": timeout,
    }))
}

pub fn validate_environment_values(
    values: &Value,
    requested_environment: Option<&str>,
    project_endpoint: &str,
    project_resource_id: &str,
    subscription_id: &str,
) -> Result<String, DeliveryError> {
    let values = values.as_object().ok_or_else(|| {
        DeliveryError("azd environment inspection returned non-object JSON".to_string())
    })?;
    let name = values
        .get("AZURE_ENV_NAME")
        .and_then(Value::as_str)
        .filter(|value| plain_azd_name(value))
        .ok_or_else(|| {
            DeliveryError("azd environment must have a valid AZURE_ENV_NAME".to_string())
        })?;
    if requested_environment.is_some_and(|requested| requested != name) {
        return Err(DeliveryError(
            "azd resolved a different environment from the requested environment".to_string(),
        ));
    }
    for key in [
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_AIPROJECT_ENDPOINT",
    ] {
        let value = values.get(key);
        if key != "FOUNDRY_PROJECT_ENDPOINT" && matches!(value, None | Some(Value::Null)) {
            continue;
        }
        if value.and_then(Value::as_str).is_none_or(|value| {
            value.trim_end_matches('/') != project_endpoint.trim_end_matches('/')
        }) {
            return Err(DeliveryError(format!(
                "stored azd {key} must match the approved project endpoint"
            )));
        }
    }
    for key in [
        "AZURE_AI_PROJECT_ID",
        "FOUNDRY_PROJECT_RESOURCE_ID",
        "AZURE_AI_PROJECT_RESOURCE_ID",
    ] {
        let value = values.get(key);
        if key != "AZURE_AI_PROJECT_ID" && matches!(value, None | Some(Value::Null)) {
            continue;
        }
        if value.and_then(Value::as_str).is_none_or(|value| {
            value.trim_end_matches('/').to_casefold()
                != project_resource_id.trim_end_matches('/').to_casefold()
        }) {
            return Err(DeliveryError(format!(
                "stored azd {key} must match the approved project ARM resource"
            )));
        }
    }
    if values
        .get("AZURE_SUBSCRIPTION_ID")
        .and_then(Value::as_str)
        .is_none_or(|value| value.to_casefold() != subscription_id.to_casefold())
    {
        return Err(DeliveryError(
            "stored azd AZURE_SUBSCRIPTION_ID must match the approved subscription".to_string(),
        ));
    }
    Ok(name.to_string())
}

pub fn verify_payload(
    payload: &Value,
    service: &str,
    project_endpoint: &str,
    project_resource_id: &str,
    expected_model: Option<&str>,
    expected_candidate: Option<&str>,
    expected_version: Option<&str>,
) -> Result<Value, DeliveryError> {
    let payload = payload
        .as_object()
        .ok_or_else(|| DeliveryError("azd agent show returned non-object JSON".to_string()))?;
    if payload.get("name").and_then(Value::as_str) != Some(service) {
        return Err(DeliveryError(
            "azd resolved a different agent from the requested service".to_string(),
        ));
    }
    let version = payload
        .get("version")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| DeliveryError("deployed agent has no version".to_string()))?;
    if payload.get("status").and_then(Value::as_str) != Some("active") {
        return Err(DeliveryError(format!(
            "agent version is not active: {:?}",
            payload.get("status").cloned().unwrap_or(Value::Null)
        )));
    }
    if expected_version.is_some_and(|expected| expected != version) {
        return Err(DeliveryError(
            "deployed agent version differs from the expected version".to_string(),
        ));
    }
    let definition = payload
        .get("definition")
        .and_then(Value::as_object)
        .ok_or_else(|| DeliveryError("deployed agent is missing its definition".to_string()))?;
    if definition.get("kind").and_then(Value::as_str) != Some("hosted") {
        return Err(DeliveryError(
            "deployment verification requires a hosted agent".to_string(),
        ));
    }
    let env = match definition.get("environment_variables") {
        None => Map::new(),
        Some(value) => value.as_object().cloned().ok_or_else(|| {
            DeliveryError("deployed environment_variables must be an object".to_string())
        })?,
    };
    let model = optional_string(env.get("AZURE_AI_MODEL_DEPLOYMENT_NAME"), "model")?;
    let candidate = optional_string(env.get("OPTIMIZATION_CANDIDATE_ID"), "candidate")?;
    if expected_model.is_some_and(|expected| model.as_deref() != Some(expected)) {
        return Err(DeliveryError(
            "deployed model differs from the expected model".to_string(),
        ));
    }
    if expected_candidate.is_some_and(|expected| candidate.as_deref() != Some(expected)) {
        return Err(DeliveryError(
            "deployed candidate differs from the expected candidate".to_string(),
        ));
    }
    let code = definition
        .get("code_configuration")
        .and_then(Value::as_object);
    let content_hash = optional_string(
        code.and_then(|code| code.get("content_hash")),
        "content hash",
    )?;
    Ok(json!({
        "project_endpoint": project_endpoint,
        "project_resource_id": project_resource_id,
        "agent_name": service,
        "agent_version": version,
        "model": model,
        "candidate_id": candidate,
        "content_hash": content_hash,
    }))
}

pub fn command_error_message(
    command: &[String],
    return_code: i32,
    _stdout: &str,
    _stderr: &str,
) -> String {
    let rendered = command
        .iter()
        .take(3)
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
    format!(
        "azd {rendered} exited {return_code}; inspect deployment logs and verify remote state before retrying"
    )
}

fn plain_azd_name(value: &str) -> bool {
    static NAME: OnceLock<Regex> = OnceLock::new();
    NAME.get_or_init(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9_-]*$").expect("valid regex"))
        .is_match(value)
}

fn validate_endpoint(endpoint: &str) -> Result<(), DeliveryError> {
    let url = Url::parse(endpoint).map_err(|_| {
        DeliveryError("project_endpoint must be an explicit HTTPS Foundry project URL".to_string())
    })?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !Regex::new(r"^/api/projects/[^/]+/?$")
            .expect("valid regex")
            .is_match(url.path())
    {
        return Err(DeliveryError(
            "project_endpoint must be an explicit HTTPS Foundry project URL".to_string(),
        ));
    }
    if url.as_str().trim_end_matches('/') != endpoint.trim_end_matches('/') {
        return Err(DeliveryError(
            "project_endpoint must be an explicit HTTPS Foundry project URL".to_string(),
        ));
    }
    Ok(())
}

fn endpoint_project(endpoint: &str) -> Result<String, DeliveryError> {
    let url = Url::parse(endpoint).map_err(|_| {
        DeliveryError("project_endpoint must be an explicit HTTPS Foundry project URL".to_string())
    })?;
    Ok(url
        .path()
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .to_string())
}

fn parse_resource(resource: &str) -> Result<(String, String), DeliveryError> {
    static RESOURCE: OnceLock<Regex> = OnceLock::new();
    let captures = RESOURCE
        .get_or_init(|| {
            Regex::new(
                r"(?i)^/subscriptions/([^/]+)/resourceGroups/[^/?#\\]+/providers/Microsoft\.CognitiveServices/accounts/[^/?#\\]+/projects/([^/?#\\]+)/?$",
            )
            .expect("valid regex")
        })
        .captures(resource.trim_end_matches('/'))
        .ok_or_else(|| {
            DeliveryError("project_resource_id must identify a Foundry project ARM resource".to_string())
        })?;
    let subscription = captures
        .get(1)
        .map(|value| value.as_str())
        .unwrap_or_default();
    let subscription = Uuid::parse_str(subscription)
        .map_err(|_| {
            DeliveryError("project_resource_id must contain a subscription UUID".to_string())
        })?
        .to_string();
    Ok((
        subscription,
        captures
            .get(2)
            .map(|value| value.as_str())
            .unwrap_or_default()
            .to_string(),
    ))
}

fn optional_string(value: Option<&Value>, name: &str) -> Result<Option<String>, DeliveryError> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(value)) => Ok(Some(value.clone())),
        _ => Err(DeliveryError(format!("deployed {name} must be a string"))),
    }
}

trait CaseFold {
    fn to_casefold(&self) -> String;
}

impl CaseFold for str {
    fn to_casefold(&self) -> String {
        self.to_ascii_lowercase()
    }
}

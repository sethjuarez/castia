//! Agent identity for hosted Responses: `agent_reference` and `agent_session_id`.
//!
//! Foundry storage refuses to create a response whose snapshot lacks
//! `agent_reference` with a `name` and `version`. The agentserver Responses SDK
//! copies the validated request `agent_reference` onto every snapshot and output
//! item, and resolves `agent_session_id` from the request, then
//! `FOUNDRY_AGENT_SESSION_ID`, then a value derived from the conversation.
//!
//! When a request omits `agent_reference` (or its `version`), Castia falls back
//! to the container's platform-injected `FOUNDRY_AGENT_NAME` and
//! `FOUNDRY_AGENT_VERSION`; it never guesses a version for another agent name.

use crate::protocols::responses_runtime::extract_partition_key;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;

pub const AGENT_NAME_ENV: &str = "FOUNDRY_AGENT_NAME";
pub const AGENT_VERSION_ENV: &str = "FOUNDRY_AGENT_VERSION";
pub const AGENT_SESSION_ID_ENV: &str = "FOUNDRY_AGENT_SESSION_ID";
const REFERENCE_TYPE: &str = "agent_reference";
const DEFAULT_AGENT_NAME: &str = "server-default-agent";
const SESSION_ID_LENGTH: usize = 63;

/// The request `agent_reference` is malformed; `param` names the bad field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidAgentReference {
    pub param: &'static str,
    pub message: &'static str,
}

fn env_value<'a>(env: &'a HashMap<String, String>, name: &str) -> &'a str {
    env.get(name).map(|value| value.trim()).unwrap_or_default()
}

fn non_empty(value: Option<&Value>) -> Option<&str> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
}

/// agentserver `_normalize_agent_reference`; `Ok(None)` when absent.
fn normalize(value: Option<&Value>) -> Result<Option<Value>, InvalidAgentReference> {
    let Some(value) = value.filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    let Some(object) = value.as_object() else {
        return Err(InvalidAgentReference {
            param: "agent_reference",
            message: "agent_reference must be an object",
        });
    };
    let mut reference = object.clone();
    let kind = reference
        .entry("type")
        .or_insert_with(|| json!(REFERENCE_TYPE));
    if kind != REFERENCE_TYPE {
        return Err(InvalidAgentReference {
            param: "agent_reference.type",
            message: "agent_reference.type must be 'agent_reference'",
        });
    }
    let Some(name) = non_empty(reference.get("name")).map(str::to_string) else {
        return Err(InvalidAgentReference {
            param: "agent_reference.name",
            message: "agent_reference.name must be a non-empty string",
        });
    };
    reference.insert("name".into(), json!(name));
    Ok(Some(Value::Object(reference)))
}

/// The request `agent_reference`, completed from the hosted agent's identity.
pub fn resolve_agent_reference(
    value: Option<&Value>,
    env: &HashMap<String, String>,
) -> Result<Option<Value>, InvalidAgentReference> {
    let name = env_value(env, AGENT_NAME_ENV);
    let version = env_value(env, AGENT_VERSION_ENV);
    let Some(mut reference) = normalize(value)? else {
        if !name.is_empty() && !version.is_empty() {
            return Ok(Some(
                json!({"type": REFERENCE_TYPE, "name": name, "version": version}),
            ));
        }
        return Ok(None);
    };
    if non_empty(reference.get("version")).is_none()
        && !version.is_empty()
        && reference.get("name").and_then(Value::as_str) == Some(name)
    {
        reference["version"] = json!(version);
    }
    Ok(Some(reference))
}

/// True when `value` has type `agent_reference` plus a name and version.
pub fn is_complete_agent_reference(value: Option<&Value>) -> bool {
    let Some(value) = value else {
        return false;
    };
    value.get("type").and_then(Value::as_str) == Some(REFERENCE_TYPE)
        && non_empty(value.get("name")).is_some()
        && non_empty(value.get("version")).is_some()
}

/// agentserver `_resolve_session_id`: request, then env, then derived.
pub fn resolve_agent_session_id(
    body: &Value,
    agent_reference: Option<&Value>,
    previous_response_id: Option<&str>,
    conversation_id: Option<&str>,
    env: &HashMap<String, String>,
) -> String {
    if let Some(requested) = non_empty(body.get("agent_session_id")) {
        return requested.to_string();
    }
    let platform = env_value(env, AGENT_SESSION_ID_ENV);
    if !platform.is_empty() {
        return platform.to_string();
    }
    let source = conversation_id
        .filter(|value| !value.is_empty())
        .or(previous_response_id.filter(|value| !value.is_empty()));
    let Some(source) = source else {
        let random = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        return random[..SESSION_ID_LENGTH].to_string();
    };
    let hint = extract_partition_key(source).unwrap_or(source);
    let field = |key: &str| {
        agent_reference
            .and_then(|reference| reference.get(key))
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
    };
    let name = field("name").unwrap_or(DEFAULT_AGENT_NAME);
    let version = field("version").unwrap_or_default();
    let digest = Sha256::digest(format!("{name}:{version}:{hint}").as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    hex[..SESSION_ID_LENGTH].to_string()
}

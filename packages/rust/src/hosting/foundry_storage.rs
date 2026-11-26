//! Foundry response storage wire contract (agentserver `FoundryStorageProvider`).
//!
//! The platform reads hosted Responses from `{project}/storage`, so a hosted
//! agent with no explicit store must write each response there. This module is
//! the transport-free seam: it builds the exact requests and maps the errors;
//! callers send them with their HTTP client and an `https://ai.azure.com/.default`
//! bearer token.

use crate::hosting::agent_identity::is_complete_agent_reference;
use crate::protocols::item_id;
use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde_json::{json, Map, Value};
use std::collections::HashMap;

pub const FOUNDRY_STORAGE_SCOPE: &str = "https://ai.azure.com/.default";
pub const FOUNDRY_CALL_ID_HEADER: &str = "x-agent-foundry-call-id";
pub const FOUNDRY_STORAGE_RESPONSE_ID_PREFIX: &str = "caresp";
/// `history/item_ids` limit meaning "all items" (agentserver's default).
pub const HISTORY_LIMIT_ALL: i64 = -1;
const API_VERSION: &str = "v1";
const JSON_CONTENT_TYPE: &str = "application/json; charset=utf-8";
const HOSTING_ENV: &str = "FOUNDRY_HOSTING_ENVIRONMENT";
const ENDPOINT_ENV: &str = "FOUNDRY_PROJECT_ENDPOINT";
const CONFLICT_CODES: &[&str] = &["conflict", "already_exists", "duplicate"];
const COMPLETED_STATUS_TYPES: &[&str] = &[
    "message",
    "function_call",
    "function_call_output",
    "computer_call",
    "computer_call_output",
    "file_search_call",
    "web_search_call",
    "image_generation_call",
    "code_interpreter_call",
    "local_shell_call",
    "local_shell_call_output",
    "shell_call",
    "shell_call_output",
    "mcp_call",
    "memory_search_call",
    "reasoning",
];
const PRESERVE_STATUS_TYPES: &[&str] = &[
    "output_message",
    "apply_patch_call",
    "apply_patch_call_output",
    "tool_search_call",
    "tool_search_output",
];

/// One storage request: send it with any HTTP client.
#[derive(Debug, Clone, PartialEq)]
pub struct StorageRequest {
    pub method: &'static str,
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FoundryStorageErrorKind {
    AlreadyExists,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FoundryStorageError {
    pub kind: FoundryStorageErrorKind,
    pub status_code: Option<u16>,
    pub code: Option<String>,
    pub message: String,
}

/// True when the process runs as a Foundry hosted agent.
pub fn foundry_storage_enabled(env: &HashMap<String, String>) -> bool {
    env.get(HOSTING_ENV)
        .is_some_and(|value| !value.trim().is_empty())
}

/// Retry 408, 429, and 5xx (transport errors are retried by the caller too).
pub fn is_retryable_status(status: u16) -> bool {
    matches!(status, 408 | 429 | 500 | 502 | 503 | 504)
}

/// Map a non-success storage reply to an explicit error.
pub fn storage_error(status: u16, body: Option<&Value>) -> FoundryStorageError {
    let error = body.and_then(|body| body.get("error"));
    let message = error
        .and_then(|error| error.get("message"))
        .and_then(Value::as_str)
        .filter(|message| !message.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| format!("Foundry storage request failed with HTTP {status}."));
    let code = error
        .and_then(|error| error.get("code"))
        .and_then(Value::as_str)
        .map(str::to_string);
    let conflict = matches!(status, 400 | 409)
        && (message.to_lowercase().contains("already exists")
            || code
                .as_deref()
                .is_some_and(|code| CONFLICT_CODES.contains(&code.to_lowercase().as_str())));
    FoundryStorageError {
        kind: if conflict {
            FoundryStorageErrorKind::AlreadyExists
        } else {
            FoundryStorageErrorKind::Other
        },
        status_code: Some(status),
        code,
        message,
    }
}

/// agentserver `strip_nulls`: drop `null` values, recursing into objects only.
fn strip_nulls(value: &Map<String, Value>) -> Map<String, Value> {
    value
        .iter()
        .filter(|(_, item)| !item.is_null())
        .map(|(key, item)| {
            let item = match item {
                Value::Object(object) => Value::Object(strip_nulls(object)),
                other => other.clone(),
            };
            (key.clone(), item)
        })
        .collect()
}

/// The response snapshot agentserver writes to Foundry storage.
///
/// Mirrors agentserver 2.4.0: `null` fields dropped, `response_id` stamped on
/// the response and each output item, `agent_reference` copied onto each
/// output item, `background` and `completed_at` filled in, and the client-only
/// `output_text` removed. An incomplete `agent_reference` is an error because
/// Foundry storage rejects it with HTTP 400.
pub fn storage_snapshot(response: &Value) -> Result<Value, FoundryStorageError> {
    let invalid = |message: &str| FoundryStorageError {
        kind: FoundryStorageErrorKind::Other,
        status_code: None,
        code: Some("invalid_request".into()),
        message: message.into(),
    };
    let Some(object) = response.as_object() else {
        return Err(invalid("A Foundry storage response must be a JSON object."));
    };
    let mut snapshot = strip_nulls(object);
    snapshot.remove("output_text");
    let reference = snapshot.get("agent_reference").cloned();
    if !is_complete_agent_reference(reference.as_ref()) {
        return Err(invalid(
            "agent_reference with name and version is required to store a response \
             in Foundry storage. Send agent_reference in the request or set \
             FOUNDRY_AGENT_NAME and FOUNDRY_AGENT_VERSION.",
        ));
    }
    let id = snapshot.get("id").cloned().unwrap_or(Value::Null);
    snapshot.entry("response_id").or_insert_with(|| id.clone());
    snapshot
        .entry("object")
        .or_insert_with(|| json!("response"));
    snapshot.entry("output").or_insert_with(|| json!([]));
    snapshot.entry("background").or_insert(json!(false));
    if snapshot.get("status").and_then(Value::as_str) == Some("completed") {
        snapshot.entry("completed_at").or_insert_with(|| {
            json!(std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |elapsed| elapsed.as_secs()))
        });
    }
    if let Some(Value::Array(items)) = snapshot.get_mut("output") {
        for item in items.iter_mut().filter_map(Value::as_object_mut) {
            if item.get("response_id").is_none_or(Value::is_null) {
                item.insert("response_id".into(), id.clone());
            }
            if item.get("agent_reference").is_none_or(Value::is_null) {
                item.insert(
                    "agent_reference".into(),
                    reference.clone().unwrap_or_default(),
                );
            }
        }
    }
    Ok(Value::Object(snapshot))
}

/// Request input items normalized the way agentserver stores them.
///
/// Returns `(items, references)`: `None` marks each `item_reference` slot to
/// fill from `items/batch/retrieve`. Unknown item types are dropped.
pub fn stored_input_items(
    items: &[Value],
    response_id: &str,
) -> Result<(Vec<Option<Value>>, Vec<String>), FoundryStorageError> {
    let mut stored = Vec::new();
    let mut references = Vec::new();
    for item in items {
        let Some(object) = item.as_object() else {
            continue;
        };
        let mut data = object.clone();
        if !data.contains_key("type") {
            if data.contains_key("role") || data.contains_key("content") {
                data.insert("type".into(), json!("message"));
            } else if data.get("id").is_some_and(Value::is_string) {
                data.insert("type".into(), json!("item_reference"));
            }
        }
        let item_type = data
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if item_type == "item_reference" {
            match data.get("id").and_then(Value::as_str) {
                Some(reference) if !reference.is_empty() => {
                    references.push(reference.to_string());
                    stored.push(None);
                    continue;
                }
                _ => {
                    return Err(FoundryStorageError {
                        kind: FoundryStorageErrorKind::Other,
                        status_code: None,
                        code: None,
                        message: "item_reference input is missing its id.".into(),
                    })
                }
            }
        }
        let Some(new_id) = item_id(&Value::Object(data.clone()), Some(response_id)) else {
            continue;
        };
        data.insert("id".into(), json!(new_id));
        if item_type == "message" {
            if let Some(Value::String(text)) = data.get("content") {
                let text = text.clone();
                data.insert(
                    "content".into(),
                    json!([{"type": "input_text", "text": text}]),
                );
            }
        }
        if COMPLETED_STATUS_TYPES.contains(&item_type.as_str()) {
            data.insert("status".into(), json!("completed"));
        } else if PRESERVE_STATUS_TYPES.contains(&item_type.as_str()) {
            data.entry("status").or_insert(json!("completed"));
        } else {
            data.remove("status");
        }
        stored.push(Some(Value::Object(data)));
    }
    Ok((stored, references))
}

/// Builds Foundry storage requests for one project endpoint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FoundryStorage {
    base_url: String,
}

/// RFC 3986 unreserved characters stay literal, like Python `quote(value, safe="")`.
const PATH_SEGMENT: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'_')
    .remove(b'.')
    .remove(b'~');

fn encode(value: &str) -> String {
    utf8_percent_encode(value, PATH_SEGMENT).to_string()
}

impl FoundryStorage {
    pub fn new(endpoint: &str) -> Result<Self, String> {
        let endpoint = endpoint.trim();
        if endpoint.is_empty() {
            return Err(format!(
                "{ENDPOINT_ENV} is required to store hosted Responses in Foundry storage."
            ));
        }
        let parsed = url::Url::parse(endpoint).map_err(|_| {
            format!("Foundry project endpoint must be an absolute http(s) URL, got '{endpoint}'.")
        })?;
        if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
            return Err(format!(
                "Foundry project endpoint must be an absolute http(s) URL, got '{endpoint}'."
            ));
        }
        Ok(Self {
            base_url: format!("{}/storage/", endpoint.trim_end_matches('/')),
        })
    }

    pub fn from_env(env: &HashMap<String, String>) -> Result<Self, String> {
        Self::new(
            env.get(ENDPOINT_ENV)
                .map(String::as_str)
                .unwrap_or_default(),
        )
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn url(&self, path: &str, params: &[(&str, &str)]) -> String {
        let mut query = vec![format!("api-version={API_VERSION}")];
        query.extend(
            params
                .iter()
                .map(|(key, value)| format!("{}={}", encode(key), encode(value))),
        );
        format!("{}{path}?{}", self.base_url, query.join("&"))
    }

    fn request(
        &self,
        method: &'static str,
        path: &str,
        params: &[(&str, &str)],
        body: Option<Value>,
        token: &str,
        call_id: Option<&str>,
    ) -> StorageRequest {
        let mut headers = vec![("Authorization".to_string(), format!("Bearer {token}"))];
        if let Some(call_id) = call_id.filter(|value| !value.is_empty()) {
            headers.push((FOUNDRY_CALL_ID_HEADER.to_string(), call_id.to_string()));
        }
        if body.is_some() {
            headers.push(("Content-Type".to_string(), JSON_CONTENT_TYPE.to_string()));
        }
        StorageRequest {
            method,
            url: self.url(path, params),
            headers,
            body,
        }
    }

    /// `POST responses` with the agentserver create envelope.
    ///
    /// `response` is normalized with [`storage_snapshot`]; an incomplete
    /// `agent_reference` fails here instead of as a storage HTTP 400.
    pub fn create_response(
        &self,
        response: &Value,
        input_items: Vec<Value>,
        history_item_ids: Vec<String>,
        token: &str,
        call_id: Option<&str>,
    ) -> Result<StorageRequest, FoundryStorageError> {
        let body = json!({
            "response": storage_snapshot(response)?,
            "input_items": input_items,
            "history_item_ids": history_item_ids,
        });
        Ok(self.request("POST", "responses", &[], Some(body), token, call_id))
    }

    /// `POST responses/{id}`: overwrite the stored snapshot.
    pub fn update_response(
        &self,
        response: &Value,
        token: &str,
        call_id: Option<&str>,
    ) -> Result<StorageRequest, FoundryStorageError> {
        let snapshot = storage_snapshot(response)?;
        let id = snapshot
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let path = format!("responses/{}", encode(id));
        Ok(self.request("POST", &path, &[], Some(snapshot), token, call_id))
    }

    pub fn get_response(&self, id: &str, token: &str, call_id: Option<&str>) -> StorageRequest {
        let path = format!("responses/{}", encode(id));
        self.request("GET", &path, &[], None, token, call_id)
    }

    pub fn delete_response(&self, id: &str, token: &str, call_id: Option<&str>) -> StorageRequest {
        let path = format!("responses/{}", encode(id));
        self.request("DELETE", &path, &[], None, token, call_id)
    }

    pub fn input_items(
        &self,
        id: &str,
        after: Option<&str>,
        token: &str,
        call_id: Option<&str>,
    ) -> StorageRequest {
        let path = format!("responses/{}/input_items", encode(id));
        let mut params = vec![("limit", "100"), ("order", "asc")];
        if let Some(after) = after {
            params.push(("after", after));
        }
        self.request("GET", &path, &params, None, token, call_id)
    }

    pub fn batch_retrieve(
        &self,
        item_ids: &[String],
        token: &str,
        call_id: Option<&str>,
    ) -> StorageRequest {
        let body = json!({"item_ids": item_ids});
        self.request(
            "POST",
            "items/batch/retrieve",
            &[],
            Some(body),
            token,
            call_id,
        )
    }

    /// `GET history/item_ids`; pass [`HISTORY_LIMIT_ALL`] for every item, as
    /// agentserver's default `default_fetch_history_count` does.
    pub fn history_item_ids(
        &self,
        previous_response_id: Option<&str>,
        conversation_id: Option<&str>,
        limit: i64,
        token: &str,
        call_id: Option<&str>,
    ) -> StorageRequest {
        let limit = limit.to_string();
        let mut params = vec![("limit", limit.as_str())];
        if let Some(previous) = previous_response_id {
            params.push(("previous_response_id", previous));
        }
        if let Some(conversation) = conversation_id {
            params.push(("conversation_id", conversation));
        }
        self.request("GET", "history/item_ids", &params, None, token, call_id)
    }
}

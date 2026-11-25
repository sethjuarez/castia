use crate::model::ResponsesRuntime;
use serde_json::Value;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaResponsesRuntime;

#[async_trait::async_trait]
impl ResponsesRuntime for CastiaResponsesRuntime {
    fn input_text(&self, value: &Value) -> String {
        responses_input(value)
    }

    fn output_body(&self, text: &String) -> Value {
        responses_body(text)
    }
}

pub fn responses_input(value: &Value) -> String {
    if let Some(text) = value.as_str() {
        return text.to_string();
    }
    let Some(items) = value.as_array() else {
        return String::new();
    };

    let mut fallback = String::new();
    for item in items.iter().rev() {
        if let Some(text) = item.as_str() {
            if fallback.is_empty() {
                fallback = text.to_string();
            }
            continue;
        }

        let Some(object) = item.as_object() else {
            continue;
        };
        let text = content_text(object.get("content"));
        if object.get("role").and_then(Value::as_str) == Some("user") && !text.is_empty() {
            return text;
        }
        if !text.is_empty() && fallback.is_empty() {
            fallback = text;
        }
    }
    fallback
}

const PLATFORM_PARTITION_SUFFIX: &str = "00";
const PLATFORM_PARTITION_LENGTH: usize = 18;
const PLATFORM_BODY_LENGTH: usize = PLATFORM_PARTITION_LENGTH + 32;
const LEGACY_BODY_LENGTH: usize = 48;
const LEGACY_PARTITION_LENGTH: usize = 16;

/// Gateway header carrying the platform-allocated response id.
pub const RESPONSE_ID_HEADER: &str = "x-agent-response-id";

/// Item `type` -> id prefix, as in the agentserver `IdGenerator.new_item_id`.
const ITEM_PREFIXES: &[(&str, &str)] = &[
    ("message", "msg"),
    ("output_message", "om"),
    ("function_call", "fc"),
    ("function_call_output", "fco"),
    ("custom_tool_call", "ctc"),
    ("custom_tool_call_output", "ctco"),
    ("computer_call", "cu"),
    ("computer_call_output", "cuo"),
    ("file_search_call", "fs"),
    ("web_search_call", "ws"),
    ("image_generation_call", "ig"),
    ("code_interpreter_call", "ci"),
    ("local_shell_call", "lsh"),
    ("local_shell_call_output", "lsho"),
    ("shell_call", "lsh"),
    ("shell_call_output", "lsho"),
    ("apply_patch_call", "ap"),
    ("apply_patch_call_output", "apo"),
    ("mcp_list_tools", "mcpl"),
    ("mcp_call", "mcp"),
    ("mcp_approval_request", "mcpr"),
    ("mcp_approval_response", "mcpa"),
    ("reasoning", "rs"),
    ("program", "prog"),
    ("program_output", "progo"),
    ("compaction", "cmp"),
    ("compaction_summary", "cmp"),
    ("structured_outputs", "fco"),
    ("tool_search_call", "ts"),
    ("tool_search_output", "tso"),
    ("additional_tools", "adt"),
    ("oauth_consent_request", "oauth"),
    ("memory_search_call", "mem"),
    ("workflow_action", "wfa"),
    ("a2a_preview_call", "a2a"),
    ("a2a_preview_call_output", "a2ao"),
    ("bing_grounding_call", "bg"),
    ("bing_grounding_call_output", "bgo"),
    ("sharepoint_grounding_preview_call", "sp"),
    ("sharepoint_grounding_preview_call_output", "spo"),
    ("azure_ai_search_call", "ais"),
    ("azure_ai_search_call_output", "aiso"),
    ("bing_custom_search_preview_call", "bcs"),
    ("bing_custom_search_preview_call_output", "bcso"),
    ("openapi_call", "oa"),
    ("openapi_call_output", "oao"),
    ("browser_automation_preview_call", "ba"),
    ("browser_automation_preview_call_output", "bao"),
    ("fabric_dataagent_preview_call", "fda"),
    ("fabric_dataagent_preview_call_output", "fdao"),
    ("azure_function_call", "azf"),
    ("azure_function_call_output", "azfo"),
];

fn partition_key(like: Option<&str>) -> Option<String> {
    let (_, body) = like?.split_once('_')?;
    match body.len() {
        PLATFORM_BODY_LENGTH => body.get(..PLATFORM_PARTITION_LENGTH).map(str::to_string),
        LEGACY_BODY_LENGTH => body
            .get(LEGACY_BODY_LENGTH - LEGACY_PARTITION_LENGTH..)
            .map(|key| format!("{key}{PLATFORM_PARTITION_SUFFIX}")),
        _ => None,
    }
}

/// Mint `{prefix}_{16 hex}00{32 hex}`, the Foundry Responses id format.
///
/// Foundry rejects other shapes as `400 Malformed identifier` when fetching a
/// response by id. `like` reuses an existing id's partition key (current or
/// legacy format) so items are co-located with their response, as the
/// agentserver `IdGenerator` does.
pub fn platform_id(prefix: &str, like: Option<&str>) -> String {
    let partition = partition_key(like).unwrap_or_else(|| {
        let random: String = uuid::Uuid::new_v4().as_bytes()[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        format!("{random}{PLATFORM_PARTITION_SUFFIX}")
    });
    format!("{prefix}_{partition}{}", uuid::Uuid::new_v4().simple())
}

/// Why `value` is not a Foundry id, or `None` when it is (agentserver `IdGenerator.is_valid`).
pub fn validate_response_id(value: &str) -> Option<String> {
    if value.is_empty() {
        return Some("ID must not be null or empty.".to_string());
    }
    let Some((prefix, body)) = value.split_once('_') else {
        return Some(format!("ID '{value}' has no '_' delimiter."));
    };
    if prefix.is_empty() {
        return Some("ID has an empty prefix.".to_string());
    }
    if body.len() != PLATFORM_BODY_LENGTH && body.len() != LEGACY_BODY_LENGTH {
        return Some(format!(
            "ID '{value}' has unexpected body length {} (expected {PLATFORM_BODY_LENGTH} or {LEGACY_BODY_LENGTH}).",
            body.len()
        ));
    }
    None
}

/// Pick the response id: gateway header, then request `response_id`, then mint one.
///
/// A supplied id that is not a Foundry id is an `Err` with the
/// `Malformed identifier` message the server returns as `400`.
pub fn resolve_response_id(
    header_value: Option<&str>,
    body_value: Option<&Value>,
    prefix: &str,
    like: Option<&str>,
) -> Result<String, String> {
    let header = header_value.map(str::trim).unwrap_or_default();
    let candidate = if header.is_empty() {
        body_value
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default()
    } else {
        header
    };
    if candidate.is_empty() {
        return Ok(platform_id(prefix, like));
    }
    match validate_response_id(candidate) {
        Some(problem) => Err(format!("Malformed identifier '{candidate}': {problem}")),
        None => Ok(candidate.to_string()),
    }
}

/// A typed item id co-located with `like`; `None` for references and unknown types.
pub fn item_id(item: &Value, like: Option<&str>) -> Option<String> {
    let item_type = match item.get("type").and_then(Value::as_str) {
        Some(item_type) => item_type,
        None if item.get("role").is_some() || item.get("content").is_some() => "message",
        None => return None,
    };
    ITEM_PREFIXES
        .iter()
        .find(|(name, _)| *name == item_type)
        .map(|(_, prefix)| platform_id(prefix, like))
}

pub fn responses_body(text: &str) -> Value {
    serde_json::json!({
        "id": platform_id("resp", None),
        "object": "response",
        "status": "completed",
        "output_text": text,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}]
            }
        ]
    })
}

fn content_text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(parts)) => parts
            .iter()
            .filter_map(|part| {
                let object = part.as_object()?;
                let kind = object.get("type").and_then(Value::as_str)?;
                if matches!(kind, "input_text" | "output_text" | "text") {
                    object.get("text").and_then(Value::as_str)
                } else {
                    None
                }
            })
            .collect(),
        _ => String::new(),
    }
}

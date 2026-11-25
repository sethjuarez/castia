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

/// Mint `{prefix}_{16 hex}00{32 hex}`, the Foundry Responses id format.
///
/// Foundry rejects other shapes as `400 Malformed identifier` when fetching a
/// response by id. `like` reuses an existing id's partition key so items are
/// co-located with their response, as the agentserver `IdGenerator` does.
pub fn platform_id(prefix: &str, like: Option<&str>) -> String {
    let partition = like
        .and_then(|id| id.split_once('_').map(|(_, body)| body))
        .filter(|body| {
            body.len() == PLATFORM_BODY_LENGTH
                && body.get(16..PLATFORM_PARTITION_LENGTH) == Some(PLATFORM_PARTITION_SUFFIX)
        })
        .map(|body| body[..PLATFORM_PARTITION_LENGTH].to_string())
        .unwrap_or_else(|| {
            let random: String = uuid::Uuid::new_v4().as_bytes()[..8]
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect();
            format!("{random}{PLATFORM_PARTITION_SUFFIX}")
        });
    format!("{prefix}_{partition}{}", uuid::Uuid::new_v4().simple())
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

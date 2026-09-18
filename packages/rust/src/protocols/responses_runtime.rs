use crate::model::ResponsesRuntime;
use serde_json::Value;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaResponsesRuntime;

#[async_trait::async_trait]
impl ResponsesRuntime for CastiaResponsesRuntime {
    fn input_text(&self, value: &Value) -> String {
        responses_input(value)
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

pub fn responses_body(text: &str) -> Value {
    serde_json::json!({
        "id": format!("resp_{}", uuid::Uuid::new_v4().simple()),
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

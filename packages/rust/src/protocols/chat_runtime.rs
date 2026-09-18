use crate::model::ChatRuntime;
use serde_json::Value;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaChatRuntime;

#[async_trait::async_trait]
impl ChatRuntime for CastiaChatRuntime {
    fn body(&self, text: &String) -> Value {
        chat_body(text)
    }

    fn last_user_text(&self, messages: &Value) -> String {
        last_user_text(messages)
    }
}

pub fn last_user_text(messages: &Value) -> String {
    let Some(messages) = messages.as_array() else {
        return String::new();
    };

    for message in messages.iter().rev() {
        let Some(message) = message.as_object() else {
            continue;
        };
        if message.get("role").and_then(Value::as_str) == Some("user") {
            return message
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
        }
    }
    String::new()
}

pub fn chat_body(text: &str) -> Value {
    serde_json::json!({
        "id": format!("chatcmpl_{}", uuid::Uuid::new_v4().simple()),
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop"
            }
        ]
    })
}

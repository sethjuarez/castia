use crate::model::InvocationsRuntime;
use serde_json::Value;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaInvocationsRuntime;

#[async_trait::async_trait]
impl InvocationsRuntime for CastiaInvocationsRuntime {
    fn body(&self, text: &String) -> Value {
        invocations_body(text)
    }

    fn input_text(&self, body: &Value) -> String {
        invocations_input(body)
    }
}

pub fn invocations_input(body: &Value) -> String {
    if let Some(body) = body.as_object() {
        return body
            .get("message")
            .or_else(|| body.get("input"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
    }
    body.as_str().unwrap_or_default().to_string()
}

pub fn invocations_body(text: &str) -> Value {
    serde_json::json!({ "output": text })
}

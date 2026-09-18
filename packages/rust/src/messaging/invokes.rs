use crate::messaging::cards::ADAPTIVE_CARD_CONTENT_TYPE;
use crate::model::{Activity, InvokesRuntime};
use serde_json::{json, Value};

pub const INVOKE_MESSAGE_TYPE: &str = "application/vnd.microsoft.activity.message";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaInvokesRuntime;

#[async_trait::async_trait]
impl InvokesRuntime for CastiaInvokesRuntime {
    fn card_action(&self, activity: &Activity) -> Value {
        card_action(activity)
    }

    fn card_invoke_response(&self, card: &Value) -> Value {
        card_invoke_response(card)
    }

    fn feedback_payload(&self, activity: &Activity) -> Value {
        feedback_payload(activity)
    }

    fn message_invoke_response(&self, text: &String) -> Value {
        message_invoke_response(text)
    }
}

pub fn card_invoke_response(card: &Value) -> Value {
    json!({
        "statusCode": 200,
        "type": ADAPTIVE_CARD_CONTENT_TYPE,
        "value": card.get("content").cloned().unwrap_or_else(|| card.clone()),
    })
}

pub fn message_invoke_response(text: &str) -> Value {
    json!({
        "statusCode": 200,
        "type": INVOKE_MESSAGE_TYPE,
        "value": text,
    })
}

pub fn feedback_payload(activity: &Activity) -> Value {
    let action_value = activity
        .value
        .as_ref()
        .and_then(|value| value.get("actionValue"))
        .unwrap_or(&Value::Null);
    json!({
        "reaction": action_value.get("reaction").cloned().unwrap_or(Value::Null),
        "feedback": action_value.get("feedback").cloned().unwrap_or(Value::Null),
    })
}

pub fn card_action(activity: &Activity) -> Value {
    let action = activity
        .value
        .as_ref()
        .and_then(|value| value.get("action"))
        .unwrap_or(&Value::Null);
    json!({
        "verb": action.get("verb").cloned().unwrap_or(Value::Null),
        "data": action
            .get("data")
            .filter(|data| is_truthy(data))
            .cloned()
            .unwrap_or_else(|| json!({})),
    })
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(_) => true,
    }
}

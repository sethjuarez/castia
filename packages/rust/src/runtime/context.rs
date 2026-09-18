use crate::messaging::{citation, feedback_channel_data, message_entity};
use crate::model::RuntimeContextRuntime;
use serde_json::{json, Map, Value};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRuntimeContextRuntime;

#[async_trait::async_trait]
impl RuntimeContextRuntime for CastiaRuntimeContextRuntime {
    fn decorate_message(
        &self,
        payload: &Value,
        turn: &Value,
        ai_generated: &bool,
        citations: &Value,
        sensitivity: &Value,
        feedback: &Option<String>,
        importance: &Option<String>,
    ) -> Value {
        decorate_message(
            payload,
            turn,
            *ai_generated,
            citations,
            sensitivity,
            feedback.as_deref(),
            importance.as_deref(),
        )
    }

    fn turn_cite(
        &self,
        citations: &Value,
        name: &String,
        url: &String,
        abstract_text: &String,
        keywords: &Value,
        icon: &String,
    ) -> Value {
        turn_cite(citations, name, url, abstract_text, keywords, icon)
    }
}

pub fn turn_cite(
    citations: &Value,
    name: &str,
    url: &str,
    abstract_text: &str,
    keywords: &Value,
    icon: &str,
) -> Value {
    let mut items = citations.as_array().cloned().unwrap_or_default();
    let position = items.len() as i32 + 1;
    items.push(citation(position, name, url, abstract_text, keywords, icon));
    json!({ "position": position, "citations": items })
}

pub fn decorate_message(
    payload: &Value,
    turn: &Value,
    ai_generated: bool,
    citations: &Value,
    sensitivity: &Value,
    feedback: Option<&str>,
    importance: Option<&str>,
) -> Value {
    let mut payload = payload.as_object().cloned().unwrap_or_default();
    let turn_obj = turn.as_object();

    let label = ai_generated
        || turn_obj
            .and_then(|turn| turn.get("aiGenerated").or_else(|| turn.get("ai_generated")))
            .and_then(Value::as_bool)
            .unwrap_or(false);

    let mut merged_citations = Vec::new();
    if let Some(turn_citations) = turn_obj
        .and_then(|turn| turn.get("citations"))
        .and_then(Value::as_array)
        .filter(|items| !items.is_empty())
    {
        merged_citations.extend(turn_citations.iter().cloned());
    }
    if let Some(override_citations) = citations.as_array().filter(|items| !items.is_empty()) {
        merged_citations.extend(override_citations.iter().cloned());
    }
    let citations_value = if merged_citations.is_empty() {
        Value::Null
    } else {
        Value::Array(merged_citations)
    };

    let banner = if !sensitivity.is_null() {
        sensitivity.clone()
    } else {
        turn_obj
            .and_then(|turn| turn.get("sensitivity"))
            .cloned()
            .unwrap_or(Value::Null)
    };
    let root = message_entity(label, &citations_value, &banner);
    if !root.is_null() {
        if !payload.get("entities").is_some_and(Value::is_array) {
            payload.insert("entities".to_string(), Value::Array(Vec::new()));
        }
        if let Some(entities) = payload.get_mut("entities").and_then(Value::as_array_mut) {
            entities.push(root);
        }
    }

    let loop_kind = feedback.map(str::to_string).or_else(|| {
        turn_obj
            .and_then(|turn| turn.get("feedback"))
            .and_then(Value::as_str)
            .map(str::to_string)
    });
    if let Some(loop_kind) = loop_kind.filter(|kind| !kind.is_empty()) {
        if !payload.get("channelData").is_some_and(Value::is_object) {
            payload.insert("channelData".to_string(), Value::Object(Map::new()));
        }
        if let Some(channel_data) = payload
            .get_mut("channelData")
            .and_then(Value::as_object_mut)
        {
            if let Some(feedback_loop) = feedback_channel_data(&loop_kind)
                .as_object()
                .and_then(|value| value.get("feedbackLoop"))
            {
                channel_data.insert("feedbackLoop".to_string(), feedback_loop.clone());
            }
        }
    }

    let weight = importance.map(str::to_string).or_else(|| {
        turn_obj
            .and_then(|turn| turn.get("importance"))
            .and_then(Value::as_str)
            .map(str::to_string)
    });
    if let Some(weight) = weight.filter(|weight| !weight.is_empty()) {
        payload.insert("importance".to_string(), Value::String(weight));
    }

    Value::Object(payload)
}

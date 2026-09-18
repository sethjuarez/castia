use crate::model::EntitiesRuntime;
use serde_json::{json, Map, Value};

pub const MAX_CITATIONS: usize = 20;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaEntitiesRuntime;

#[async_trait::async_trait]
impl EntitiesRuntime for CastiaEntitiesRuntime {
    fn citation(
        &self,
        position: &i32,
        name: &String,
        url: &String,
        abstract_: &String,
        keywords: &Value,
        icon: &String,
    ) -> Value {
        citation(*position, name, url, abstract_, keywords, icon)
    }

    fn feedback_channel_data(&self, kind: &String) -> Value {
        feedback_channel_data(kind)
    }

    fn mention_entity(&self, account_id: &String, name: &String) -> Value {
        mention_entity(account_id, name)
    }

    fn message_entity(&self, ai_generated: &bool, citations: &Value, sensitivity: &Value) -> Value {
        message_entity(*ai_generated, citations, sensitivity)
    }

    fn sensitivity_label(&self, name: &String, description: &String) -> Value {
        sensitivity_label(name, description)
    }
}

pub fn message_entity(ai_generated: bool, citations: &Value, sensitivity: &Value) -> Value {
    if !ai_generated && !is_truthy(citations) && !is_truthy(sensitivity) {
        return Value::Null;
    }

    let mut entity = Map::from_iter([
        ("type".to_string(), json!("https://schema.org/Message")),
        ("@type".to_string(), json!("Message")),
        ("@context".to_string(), json!("https://schema.org")),
        ("@id".to_string(), json!("")),
    ]);
    if ai_generated {
        entity.insert("additionalType".to_string(), json!(["AIGeneratedContent"]));
    }
    if is_truthy(citations) {
        let capped = citations
            .as_array()
            .map(|items| Value::Array(items.iter().take(MAX_CITATIONS).cloned().collect()))
            .unwrap_or_else(|| citations.clone());
        entity.insert("citation".to_string(), capped);
    }
    if is_truthy(sensitivity) {
        entity.insert("usageInfo".to_string(), sensitivity.clone());
    }
    Value::Object(entity)
}

pub fn citation(
    position: i32,
    name: &str,
    url: &str,
    abstract_: &str,
    keywords: &Value,
    icon: &str,
) -> Value {
    let mut appearance = Map::from_iter([
        ("@type".to_string(), json!("DigitalDocument")),
        ("name".to_string(), json!(name)),
    ]);
    if !url.is_empty() {
        appearance.insert("url".to_string(), json!(url));
    }
    if !abstract_.is_empty() {
        appearance.insert("abstract".to_string(), json!(abstract_));
    }
    if let Some(items) = keywords.as_array().filter(|items| !items.is_empty()) {
        appearance.insert(
            "keywords".to_string(),
            Value::Array(items.iter().take(3).cloned().collect()),
        );
    }
    if !icon.is_empty() {
        appearance.insert(
            "image".to_string(),
            json!({ "@type": "ImageObject", "name": icon }),
        );
    }
    json!({ "@type": "Claim", "position": position, "appearance": appearance })
}

pub fn sensitivity_label(name: &str, description: &str) -> Value {
    let mut info = Map::from_iter([
        ("@type".to_string(), json!("CreativeWork")),
        ("name".to_string(), json!(name)),
    ]);
    if !description.is_empty() {
        info.insert("description".to_string(), json!(description));
    }
    Value::Object(info)
}

pub fn feedback_channel_data(kind: &str) -> Value {
    json!({ "feedbackLoop": { "type": kind } })
}

pub fn mention_entity(account_id: &str, name: &str) -> Value {
    json!({
        "type": "mention",
        "mentioned": { "id": account_id, "name": name },
        "text": format!("<at>{name}</at>"),
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

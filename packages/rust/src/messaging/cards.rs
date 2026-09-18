use crate::model::CardsRuntime;
use serde_json::{json, Map, Value};

pub const ADAPTIVE_CARD_CONTENT_TYPE: &str = "application/vnd.microsoft.card.adaptive";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaCardsRuntime;

#[async_trait::async_trait]
impl CardsRuntime for CastiaCardsRuntime {
    fn action_chips(&self, actions: &Value, prompt: &String) -> Value {
        action_chips(actions, prompt)
    }

    fn adaptive_card(&self, body: &Value, card: &Value) -> Value {
        adaptive_card(body, card)
    }

    fn decision_card(&self, actions: &Value, prompt: &String, card: &Value) -> Value {
        decision_card_with_options(actions, prompt, card)
    }

    fn suggested_actions(&self, actions: &Value) -> Value {
        suggested_actions(actions)
    }
}

pub fn adaptive_card(body: &Value, card: &Value) -> Value {
    let mut content = Map::from_iter([
        ("type".to_string(), json!("AdaptiveCard")),
        (
            "$schema".to_string(),
            json!("http://adaptivecards.io/schemas/adaptive-card.json"),
        ),
        (
            "version".to_string(),
            card.get("version").cloned().unwrap_or_else(|| json!("1.5")),
        ),
        ("body".to_string(), body.clone()),
    ]);

    if let Some(extra) = card.as_object() {
        for (key, value) in extra {
            if key != "version" {
                content.insert(key.clone(), value.clone());
            }
        }
    }

    json!({
        "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
        "content": Value::Object(content),
    })
}

pub fn suggested_actions(actions: &Value) -> Value {
    json!({
        "actions": action_values(actions, action),
        "to": [],
    })
}

pub fn action_chips(actions: &Value, prompt: &str) -> Value {
    let mut body = Vec::new();
    if !prompt.is_empty() {
        body.push(json!({ "type": "TextBlock", "text": prompt, "wrap": true }));
    }
    adaptive_card(
        &Value::Array(body),
        &json!({ "actions": action_values(actions, submit) }),
    )
}

pub fn decision_card(actions: &Value, prompt: &str) -> Value {
    decision_card_with_options(actions, prompt, &Value::Null)
}

pub fn decision_card_with_options(actions: &Value, prompt: &str, card: &Value) -> Value {
    let mut body = Vec::new();
    if !prompt.is_empty() {
        body.push(json!({
            "type": "TextBlock",
            "text": prompt,
            "wrap": true,
            "weight": "Bolder",
        }));
    }
    let mut card = card.as_object().cloned().unwrap_or_default();
    card.insert(
        "actions".to_string(),
        Value::Array(action_values(actions, execute)),
    );
    adaptive_card(&Value::Array(body), &Value::Object(card))
}

fn action_values(actions: &Value, builder: fn(&Value) -> Value) -> Vec<Value> {
    actions
        .as_array()
        .map(|items| items.iter().map(builder).collect())
        .unwrap_or_default()
}

fn action(action: &Value) -> Value {
    if let Some(text) = action.as_str() {
        return json!({ "type": "imBack", "title": text, "value": text });
    }
    let title = truthy_property(action, "title")
        .or_else(|| truthy_property(action, "value"))
        .cloned()
        .unwrap_or_else(|| json!(""));
    json!({
        "type": action.get("type").cloned().unwrap_or_else(|| json!("imBack")),
        "title": title,
        "value": action.get("value").cloned().unwrap_or(title),
    })
}

fn submit(action: &Value) -> Value {
    let (title, value) = if let Some(text) = action.as_str() {
        (json!(text), json!(text))
    } else {
        let title = truthy_property(action, "title")
            .or_else(|| truthy_property(action, "value"))
            .cloned()
            .unwrap_or_else(|| json!(""));
        let value = action
            .get("value")
            .cloned()
            .unwrap_or_else(|| title.clone());
        (title, value)
    };

    json!({
        "type": "Action.Submit",
        "title": title,
        "data": {
            "msteams": { "type": "imBack", "value": value },
            "choice": value,
        },
    })
}

fn execute(action: &Value) -> Value {
    if let Some(text) = action.as_str() {
        return json!({ "type": "Action.Execute", "title": text, "verb": text, "data": {} });
    }

    let title = truthy_property(action, "title")
        .or_else(|| truthy_property(action, "verb"))
        .cloned()
        .unwrap_or_else(|| json!(""));
    let verb = truthy_property(action, "verb")
        .cloned()
        .unwrap_or_else(|| title.clone());
    json!({
        "type": "Action.Execute",
        "title": title,
        "verb": verb,
        "data": truthy_property(action, "data").cloned().unwrap_or_else(|| json!({})),
    })
}

fn truthy_property<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value.get(key).filter(|candidate| is_truthy(candidate))
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

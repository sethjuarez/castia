use crate::model::{Activity, ChannelAccount, ConnectorRuntime, SaveContext};
use serde_json::{json, Value};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaConnectorRuntime;

#[async_trait::async_trait]
impl ConnectorRuntime for CastiaConnectorRuntime {
    fn connector_endpoint(&self, activity: &Activity) -> Value {
        connector_endpoint(activity).unwrap_or(Value::Null)
    }

    fn connector_envelope(&self, activity: &Activity) -> Value {
        connector_envelope(activity)
    }

    fn connector_ok(&self, response: &Value) -> bool {
        connector_ok(response)
    }

    fn created_activity_id(&self, response: &Value) -> Option<String> {
        created_activity_id(response)
    }

    fn delete_activity_request(&self, activity: &Activity, activity_id: &String) -> Value {
        delete_activity_request(activity, activity_id).unwrap_or(Value::Null)
    }

    fn post_activity_request(&self, activity: &Activity, payload: &Value) -> Value {
        post_activity_request(activity, payload).unwrap_or(Value::Null)
    }

    fn reaction_request(
        &self,
        activity: &Activity,
        reaction_type: &String,
        method: &String,
    ) -> Value {
        reaction_request(activity, reaction_type, method).unwrap_or(Value::Null)
    }

    fn typing_request(&self, activity: &Activity) -> Value {
        typing_request(activity).unwrap_or(Value::Null)
    }

    fn update_activity_request(
        &self,
        activity: &Activity,
        activity_id: &String,
        payload: &Value,
    ) -> Value {
        update_activity_request(activity, activity_id, payload).unwrap_or(Value::Null)
    }
}

pub fn connector_endpoint(activity: &Activity) -> Option<Value> {
    let service_url = activity.service_url.as_deref()?.trim_end_matches('/');
    let conversation_id = activity.conversation.as_ref()?.id.as_deref()?;
    if service_url.is_empty() || conversation_id.is_empty() {
        return None;
    }
    Some(json!({
        "service_url": service_url,
        "conversation_id": conversation_id,
    }))
}

pub fn connector_envelope(activity: &Activity) -> Value {
    let conversation_id = activity
        .conversation
        .as_ref()
        .and_then(|conversation| conversation.id.as_deref())
        .unwrap_or_default();
    json!({
        "from": account_json(activity.recipient.as_ref()),
        "recipient": account_json(activity.from.as_ref()),
        "conversation": { "id": conversation_id },
        "replyToId": activity.id.as_deref(),
    })
}

pub fn post_activity_request(activity: &Activity, payload: &Value) -> Option<Value> {
    let endpoint = connector_endpoint(activity)?;
    let service_url = endpoint.get("service_url")?.as_str()?;
    let conversation_id = endpoint.get("conversation_id")?.as_str()?;
    let mut body = connector_envelope(activity).as_object()?.clone();
    if let Some(payload) = payload.as_object() {
        for (key, value) in payload {
            body.insert(key.clone(), value.clone());
        }
    }
    Some(json!({
        "method": "POST",
        "url": format!("{service_url}/v3/conversations/{conversation_id}/activities"),
        "json": Value::Object(body),
    }))
}

pub fn typing_request(activity: &Activity) -> Option<Value> {
    post_activity_request(activity, &json!({ "type": "typing" }))
}

pub fn reaction_request(activity: &Activity, reaction_type: &str, method: &str) -> Option<Value> {
    let endpoint = connector_endpoint(activity)?;
    let activity_id = activity.id.as_deref()?;
    if activity_id.is_empty() || reaction_type.is_empty() {
        return None;
    }
    let service_url = endpoint.get("service_url")?.as_str()?;
    let conversation_id = endpoint.get("conversation_id")?.as_str()?;
    Some(json!({
        "method": method,
        "url": format!(
            "{service_url}/v3/conversations/{conversation_id}/activities/{}/reactions/{}",
            quote(activity_id),
            quote(reaction_type)
        ),
        "json": Value::Null,
    }))
}

pub fn update_activity_request(
    activity: &Activity,
    activity_id: &str,
    payload: &Value,
) -> Option<Value> {
    let endpoint = connector_endpoint(activity)?;
    if activity_id.is_empty() {
        return None;
    }
    let service_url = endpoint.get("service_url")?.as_str()?;
    let conversation_id = endpoint.get("conversation_id")?.as_str()?;
    let mut body = connector_envelope(activity).as_object()?.clone();
    body.insert("id".to_string(), Value::String(activity_id.to_string()));
    if let Some(payload) = payload.as_object() {
        for (key, value) in payload {
            body.insert(key.clone(), value.clone());
        }
    }
    Some(json!({
        "method": "PUT",
        "url": format!(
            "{service_url}/v3/conversations/{conversation_id}/activities/{}",
            quote(activity_id)
        ),
        "json": Value::Object(body),
    }))
}

pub fn delete_activity_request(activity: &Activity, activity_id: &str) -> Option<Value> {
    let endpoint = connector_endpoint(activity)?;
    if activity_id.is_empty() {
        return None;
    }
    let service_url = endpoint.get("service_url")?.as_str()?;
    let conversation_id = endpoint.get("conversation_id")?.as_str()?;
    Some(json!({
        "method": "DELETE",
        "url": format!(
            "{service_url}/v3/conversations/{conversation_id}/activities/{}",
            quote(activity_id)
        ),
        "json": Value::Null,
    }))
}

pub fn created_activity_id(response: &Value) -> Option<String> {
    if response
        .get("statusCode")
        .and_then(Value::as_i64)
        .is_some_and(|status| status >= 400)
    {
        return None;
    }
    response
        .get("body")
        .and_then(|body| body.get("id"))
        .and_then(Value::as_str)
        .map(str::to_string)
}

pub fn connector_ok(response: &Value) -> bool {
    response
        .get("statusCode")
        .and_then(Value::as_i64)
        .is_some_and(|status| status < 400)
}

fn account_json(account: Option<&ChannelAccount>) -> Value {
    account
        .map(|account| account.to_value(&SaveContext::default()))
        .unwrap_or_else(|| json!({}))
}

fn quote(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(byte as char);
            }
            _ => encoded.push_str(&format!("%{byte:02X}")),
        }
    }
    encoded
}

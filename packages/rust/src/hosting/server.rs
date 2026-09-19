use crate::model::HostingServerRuntime;
use serde_json::{Map, Value};
use std::collections::HashSet;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaHostingServerRuntime;

#[async_trait::async_trait]
impl HostingServerRuntime for CastiaHostingServerRuntime {
    fn readiness_body(&self) -> String {
        readiness_body()
    }

    fn sse_event(&self, event_type: &String, payload: &Value) -> String {
        sse_event(event_type, payload)
    }

    fn wire_endpoints(&self, wire_protocols: &Vec<String>) -> Value {
        wire_endpoints(wire_protocols)
    }
}

pub fn readiness_body() -> String {
    "{\"status\":\"ok\"}".to_string()
}

pub fn sse_event(event_type: &str, payload: &Value) -> String {
    format!("event: {event_type}\ndata: {}\n\n", compact_json(payload))
}

pub fn wire_endpoints(wire_protocols: &[String]) -> Value {
    let protocols: HashSet<&str> = wire_protocols.iter().map(String::as_str).collect();
    let mut endpoints = Vec::new();
    if protocols.contains("responses") {
        endpoints.push(endpoint(
            "responses",
            "/responses",
            Some(protocols.contains("responses_stream")),
        ));
    }
    if protocols.contains("chat") {
        endpoints.push(endpoint("chat", "/chat/completions", None));
    }
    if protocols.contains("invocations") {
        endpoints.push(endpoint("invocations", "/invocations", None));
    }
    Value::Array(endpoints)
}

fn endpoint(protocol: &str, path: &str, streaming: Option<bool>) -> Value {
    let mut endpoint = Map::from_iter([
        ("protocol".to_string(), Value::String(protocol.to_string())),
        ("method".to_string(), Value::String("POST".to_string())),
        ("path".to_string(), Value::String(path.to_string())),
    ]);
    if let Some(streaming) = streaming {
        endpoint.insert("streaming".to_string(), Value::Bool(streaming));
    }
    Value::Object(endpoint)
}

fn compact_json(value: &Value) -> String {
    match value {
        Value::Object(object) => {
            let mut fields = Vec::new();
            let mut emitted = HashSet::new();
            for key in preferred_keys(object) {
                if let Some(value) = object.get(key) {
                    fields.push(format!("{}:{}", quote(key), compact_json(value)));
                    emitted.insert(key);
                }
            }
            for (key, value) in object {
                if emitted.contains(key.as_str()) {
                    continue;
                }
                fields.push(format!("{}:{}", quote(key), compact_json(value)));
            }
            format!("{{{}}}", fields.join(","))
        }
        Value::Array(values) => format!(
            "[{}]",
            values
                .iter()
                .map(compact_json)
                .collect::<Vec<_>>()
                .join(",")
        ),
        Value::String(value) => quote(value),
        _ => serde_json::to_string(value).unwrap_or_else(|_| "null".to_string()),
    }
}

fn preferred_keys(object: &Map<String, Value>) -> Vec<&'static str> {
    if object.contains_key("id")
        && object.get("object").and_then(Value::as_str) == Some("response")
        && object.contains_key("output_text")
    {
        return vec!["id", "object", "status", "output_text", "output"];
    }
    if object.get("type").and_then(Value::as_str) == Some("message") && object.contains_key("role")
    {
        return vec!["type", "role", "content"];
    }
    if object.contains_key("type") && object.contains_key("text") {
        return vec!["type", "text"];
    }
    if object.contains_key("type") && object.contains_key("delta") {
        return vec!["type", "delta"];
    }
    Vec::new()
}

fn quote(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ch if ch < ' ' => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch if (ch as u32) <= 0x7f => out.push(ch),
            ch if (ch as u32) <= 0xffff => out.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => {
                let code = ch as u32 - 0x1_0000;
                let high = 0xd800 + ((code >> 10) & 0x3ff);
                let low = 0xdc00 + (code & 0x3ff);
                out.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
            }
        }
    }
    out.push('"');
    out
}

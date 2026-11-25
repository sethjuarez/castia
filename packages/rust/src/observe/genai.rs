//! Pure OpenTelemetry GenAI semantic-convention shapes, mirroring
//! `castia.observe.genai` in the Python SDK.
//!
//! The behavior is pinned by `spec/conformance/observe/genai_semconv.json`.

use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

/// GenAI semantic-convention version castia's spans and events follow.
pub const GENAI_SEMCONV_VERSION: &str = "1.42.0";
/// Schema URL attached to castia's tracer and logger.
pub const GENAI_SEMCONV_SCHEMA_URL: &str = "https://opentelemetry.io/schemas/1.42.0";
/// `gen_ai.provider.name` / `gen_ai.system` value for Foundry-hosted models.
pub const GENAI_PROVIDER: &str = "microsoft.foundry";
/// Event name for evaluation results, human or automated.
pub const EVALUATION_RESULT_EVENT: &str = "gen_ai.evaluation.result";

/// Responses `output` item types the provider executes server-side.
pub const SERVER_TOOL_TYPES: &[&str] = &[
    "mcp_call",
    "web_search_call",
    "file_search_call",
    "code_interpreter_call",
    "image_generation_call",
    "computer_call",
    "local_shell_call",
    "custom_tool_call",
];

const MAX_ERROR_MESSAGE: usize = 500;
const REQUEST_PARAMETERS: &[(&str, &str)] = &[
    ("temperature", "gen_ai.request.temperature"),
    ("top_p", "gen_ai.request.top_p"),
    ("top_k", "gen_ai.request.top_k"),
    ("max_output_tokens", "gen_ai.request.max_tokens"),
    ("max_tokens", "gen_ai.request.max_tokens"),
    ("frequency_penalty", "gen_ai.request.frequency_penalty"),
    ("presence_penalty", "gen_ai.request.presence_penalty"),
    ("seed", "gen_ai.request.seed"),
    ("choice_count", "gen_ai.request.choice.count"),
];

/// One `gen_ai.input.messages` / `gen_ai.output.messages` entry in the parts schema.
pub fn text_messages(role: &str, text: &str, finish_reason: Option<&str>) -> Value {
    let mut message = json!({"role": role, "parts": [{"type": "text", "content": text}]});
    if let Some(reason) = finish_reason.filter(|reason| !reason.is_empty()) {
        message["finish_reason"] = json!(reason);
    }
    json!([message])
}

/// The `gen_ai.system_instructions` value for plain-text instructions.
pub fn system_instructions(text: &str) -> Value {
    json!([{"type": "text", "content": text}])
}

/// Map request parameters to `gen_ai.request.*`, keeping zero values.
pub fn request_parameter_attributes(params: &Map<String, Value>) -> Map<String, Value> {
    let mut attributes = Map::new();
    for (key, attribute) in REQUEST_PARAMETERS {
        if let Some(value @ Value::Number(_)) = params.get(*key) {
            attributes
                .entry(attribute.to_string())
                .or_insert_with(|| value.clone());
        }
    }
    match params.get("stop") {
        Some(Value::String(stop)) => {
            attributes.insert("gen_ai.request.stop_sequences".into(), json!([stop]));
        }
        Some(Value::Array(items)) if items.iter().all(Value::is_string) => {
            attributes.insert(
                "gen_ai.request.stop_sequences".into(),
                Value::Array(items.clone()),
            );
        }
        _ => {}
    }
    attributes
}

/// Normalize provider-executed tool items from a Responses `output` list.
pub fn server_tool_calls(output: &[Value]) -> Vec<Value> {
    output
        .iter()
        .filter_map(|item| {
            let item_type = item.get("type").and_then(Value::as_str).unwrap_or("");
            if !SERVER_TOOL_TYPES.contains(&item_type) {
                return None;
            }
            let error = item.get("error").cloned().unwrap_or(Value::Null);
            let item_status = item.get("status").and_then(Value::as_str);
            let failed = truthy(&error) || matches!(item_status, Some("failed" | "incomplete"));
            let is_mcp = item_type == "mcp_call";
            let field = |name: &str| item.get(name).cloned().unwrap_or(Value::Null);
            Some(json!({
                "name": text(item, "name").or_else(|| text(item, "server_label")).unwrap_or(item_type),
                "call_id": text(item, "call_id").or_else(|| text(item, "id")),
                "item_type": item_type,
                "tool_type": if item_type == "file_search_call" { "datastore" } else { "extension" },
                "server_label": text(item, "server_label"),
                "status": if failed { "error" } else { "ok" },
                "error_type": if failed { Some(error_type(&error, item_status)) } else { None },
                "error_message": if failed { error_message(&error) } else { None },
                "arguments": if is_mcp { field("arguments") } else { Value::Null },
                "result": if is_mcp { field("output") } else { Value::Null },
            }))
        })
        .collect()
}

/// The `execute_tool` span shape for one normalized server tool call.
pub fn server_tool_span(
    call: &Value,
    system: Option<&str>,
    iteration: Option<i64>,
    include_content: bool,
) -> Value {
    let provider = system.unwrap_or(GENAI_PROVIDER);
    let name = call.get("name").and_then(Value::as_str).unwrap_or("tool");
    let status = call.get("status").and_then(Value::as_str).unwrap_or("ok");
    let mut attributes = Map::new();
    let mut set = |key: &str, value: Value| {
        attributes.insert(key.to_string(), value);
    };
    set("gen_ai.operation.name", json!("execute_tool"));
    set("gen_ai.system", json!(provider));
    set("gen_ai.provider.name", json!(provider));
    set("gen_ai.tool.name", json!(name));
    set(
        "gen_ai.tool.type",
        call.get("tool_type")
            .filter(|v| truthy(v))
            .cloned()
            .unwrap_or(json!("extension")),
    );
    set("castia.telemetry.source", json!("castia"));
    set("castia.tool.source", json!("server"));
    set("castia.tool.synthesized", json!(true));
    set(
        "castia.tool.item_type",
        call.get("item_type").cloned().unwrap_or(Value::Null),
    );
    set("castia.tool.status", json!(status));
    for (key, attribute) in [
        ("call_id", "gen_ai.tool.call.id"),
        ("server_label", "castia.mcp.server_label"),
        ("error_type", "error.type"),
    ] {
        if let Some(value) = call.get(key).filter(|v| truthy(v)) {
            set(attribute, value.clone());
        }
    }
    if let Some(iteration) = iteration {
        set("castia.model.iteration", json!(iteration));
    }
    if include_content {
        for (key, attribute) in [
            ("arguments", "gen_ai.tool.call.arguments"),
            ("result", "gen_ai.tool.call.result"),
        ] {
            match call.get(key) {
                None | Some(Value::Null) => {}
                Some(Value::String(value)) => set(attribute, json!(value)),
                Some(value) => set(attribute, json!(sorted_json(value))),
            }
        }
    }
    let failed = status == "error";
    json!({
        "span_name": format!("execute_tool {name}"),
        "status": if failed { "error" } else { "ok" },
        "status_description": if failed { call.get("error_message").cloned().unwrap_or(Value::Null) } else { Value::Null },
        "attributes": attributes,
    })
}

/// Errors from building a human evaluation result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EvaluationError {
    EmptyName,
    MissingScoreOrLabel,
}

/// A human review of one agent response.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct HumanEvaluation {
    pub name: String,
    pub score: Option<f64>,
    pub label: Option<String>,
    pub explanation: Option<String>,
    pub response_id: Option<String>,
    pub agent_id: Option<String>,
    pub reviewer: Option<String>,
}

/// `gen_ai.evaluation.result` attributes for a human review.
pub fn human_evaluation_attributes(
    evaluation: &HumanEvaluation,
) -> Result<Map<String, Value>, EvaluationError> {
    let name = evaluation.name.trim();
    if name.is_empty() {
        return Err(EvaluationError::EmptyName);
    }
    let label = evaluation
        .label
        .as_deref()
        .filter(|label| !label.is_empty());
    if evaluation.score.is_none() && label.is_none() {
        return Err(EvaluationError::MissingScoreOrLabel);
    }
    let mut attributes = Map::new();
    attributes.insert("gen_ai.evaluation.name".into(), json!(name));
    attributes.insert("castia.evaluation.source".into(), json!("human"));
    if let Some(score) = evaluation.score {
        attributes.insert("gen_ai.evaluation.score.value".into(), json!(score));
    }
    for (value, key) in [
        (label, "gen_ai.evaluation.score.label"),
        (
            evaluation.explanation.as_deref(),
            "gen_ai.evaluation.explanation",
        ),
        (evaluation.response_id.as_deref(), "gen_ai.response.id"),
        (evaluation.agent_id.as_deref(), "gen_ai.agent.id"),
        (evaluation.reviewer.as_deref(), "castia.evaluation.reviewer"),
    ] {
        if let Some(value) = value.filter(|value| !value.is_empty()) {
            attributes.insert(key.into(), json!(value));
        }
    }
    Ok(attributes)
}

/// `(trace_id, span_id, sampled)` from a W3C `traceparent`.
pub fn parse_traceparent(value: &str) -> Option<(String, String, bool)> {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    let pattern = PATTERN.get_or_init(|| {
        Regex::new(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
            .expect("valid traceparent regex")
    });
    let lowered = value.trim().to_ascii_lowercase();
    let captures = pattern.captures(&lowered)?;
    if &captures[1] == "ff" {
        return None;
    }
    let (trace_id, span_id) = (captures[2].to_string(), captures[3].to_string());
    if trace_id.chars().all(|c| c == '0') || span_id.chars().all(|c| c == '0') {
        return None;
    }
    let flags = u8::from_str_radix(&captures[4], 16).ok()?;
    Some((trace_id, span_id, flags & 1 == 1))
}

fn text<'a>(item: &'a Value, name: &str) -> Option<&'a str> {
    item.get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
        Value::Number(value) => value.as_f64() != Some(0.0),
    }
}

fn error_type(error: &Value, item_status: Option<&str>) -> String {
    static HTTP: OnceLock<Regex> = OnceLock::new();
    match error {
        Value::String(message) if !message.is_empty() => {
            let http = HTTP.get_or_init(|| Regex::new(r"\b([45]\d\d)\b").expect("valid regex"));
            http.captures(message)
                .map(|captures| format!("http_{}", &captures[1]))
                .unwrap_or_else(|| "server_tool_error".into())
        }
        Value::Object(fields) => ["type", "code"]
            .iter()
            .find_map(|key| match fields.get(*key) {
                None | Some(Value::Null) => None,
                Some(Value::String(value)) if value.is_empty() => None,
                Some(Value::String(value)) => Some(value.clone()),
                Some(value) => Some(value.to_string()),
            })
            .unwrap_or_else(|| "server_tool_error".into()),
        value if truthy(value) => "server_tool_error".into(),
        _ => item_status
            .filter(|status| !status.is_empty())
            .map(|status| format!("server_tool_{status}"))
            .unwrap_or_else(|| "server_tool_error".into()),
    }
}

fn error_message(error: &Value) -> Option<String> {
    let message = match error {
        Value::String(message) => message.as_str(),
        Value::Object(fields) => fields.get("message")?.as_str()?,
        _ => return None,
    };
    Some(message.chars().take(MAX_ERROR_MESSAGE).collect())
}

fn sorted_json(value: &Value) -> String {
    fn sort(value: &Value) -> Value {
        match value {
            Value::Object(fields) => {
                let mut keys: Vec<_> = fields.keys().collect();
                keys.sort();
                let mut sorted = Map::new();
                for key in keys {
                    sorted.insert(key.clone(), sort(&fields[key]));
                }
                Value::Object(sorted)
            }
            Value::Array(items) => Value::Array(items.iter().map(sort).collect()),
            other => other.clone(),
        }
    }
    sort(value).to_string()
}

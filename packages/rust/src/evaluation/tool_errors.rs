//! Tool-error gate for evaluation runs, mirroring
//! `castia.evaluation.tool_errors` in the Python SDK.
//!
//! An eval run whose tools are failing measures the outage, not the agent.
//! The behavior is pinned by `spec/conformance/observe/genai_semconv.json`.

use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

pub const DEFAULT_MAX_ERROR_RATE: f64 = 0.1;

/// Tool error counts for one eval run and whether the run is valid.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolErrorGate {
    pub total: u64,
    pub errors: u64,
    pub error_rate: f64,
    pub max_error_rate: f64,
    pub min_calls: u64,
    pub valid: bool,
    pub reason: Option<&'static str>,
    pub by_type: BTreeMap<String, u64>,
    pub by_tool: BTreeMap<String, u64>,
}

impl ToolErrorGate {
    pub fn to_value(&self) -> Value {
        json!({
            "schema": "castia.tool_error_gate/v1",
            "total": self.total,
            "errors": self.errors,
            "error_rate": self.error_rate,
            "max_error_rate": self.max_error_rate,
            "min_calls": self.min_calls,
            "valid": self.valid,
            "reason": self.reason,
            "by_type": self.by_type,
            "by_tool": self.by_tool,
        })
    }
}

/// Count tool errors in `records` and decide whether the run is valid.
///
/// Panics if `max_error_rate` is outside `0..=1`.
pub fn tool_error_gate(records: &[Value], max_error_rate: f64, min_calls: u64) -> ToolErrorGate {
    assert!(
        (0.0..=1.0).contains(&max_error_rate),
        "max_error_rate must be between 0 and 1"
    );
    let mut total = 0u64;
    let mut by_type = BTreeMap::new();
    let mut by_tool = BTreeMap::new();
    for record in records {
        for (name, error_type) in tool_outcomes(record) {
            total += 1;
            if let Some(error_type) = error_type {
                *by_type.entry(error_type).or_insert(0) += 1;
                *by_tool.entry(name).or_insert(0) += 1;
            }
        }
    }
    let errors: u64 = by_type.values().sum();
    let error_rate = if total == 0 {
        0.0
    } else {
        errors as f64 / total as f64
    };
    let reason = if total < min_calls {
        Some("insufficient_tool_calls")
    } else if error_rate > max_error_rate {
        Some("tool_error_rate_exceeded")
    } else {
        None
    };
    ToolErrorGate {
        total,
        errors,
        error_rate,
        max_error_rate,
        min_calls,
        valid: reason.is_none(),
        reason,
        by_type,
        by_tool,
    }
}

fn tool_outcomes(record: &Value) -> Vec<(String, Option<String>)> {
    let Some(fields) = record.as_object() else {
        return Vec::new();
    };
    if let Some(Value::Array(calls)) = fields.get("tool_calls") {
        return calls
            .iter()
            .filter_map(Value::as_object)
            .map(capture_outcome)
            .collect();
    }
    if let Some(Value::Object(attributes)) = fields.get("attributes") {
        if attributes
            .get("gen_ai.operation.name")
            .and_then(Value::as_str)
            == Some("execute_tool")
        {
            return vec![span_outcome(fields, attributes)];
        }
        return Vec::new();
    }
    if fields.contains_key("status")
        && (fields.contains_key("item_type") || fields.contains_key("error_type"))
    {
        let name = string(fields, "name").unwrap_or("tool").to_string();
        let failed = string(fields, "status") == Some("error");
        let error_type = failed.then(|| {
            string(fields, "error_type")
                .unwrap_or("tool_error")
                .to_string()
        });
        return vec![(name, error_type)];
    }
    Vec::new()
}

fn capture_outcome(call: &Map<String, Value>) -> (String, Option<String>) {
    let name = string(call, "name").unwrap_or("tool").to_string();
    match string(call, "status") {
        Some(status @ ("error" | "incomplete")) => {
            let error_type = call
                .get("error")
                .and_then(Value::as_object)
                .and_then(|error| string(error, "type"))
                .map(str::to_string)
                .unwrap_or_else(|| format!("tool_{status}"));
            (name, Some(error_type))
        }
        _ if call.get("tool_reported_ok") == Some(&Value::Bool(false)) => {
            (name, Some("tool_reported_error".into()))
        }
        _ => (name, None),
    }
}

fn span_outcome(
    record: &Map<String, Value>,
    attributes: &Map<String, Value>,
) -> (String, Option<String>) {
    let name = string(attributes, "gen_ai.tool.name")
        .or_else(|| string(record, "name"))
        .unwrap_or("tool")
        .to_string();
    let status = match record.get("status") {
        Some(Value::Object(status)) => {
            string(status, "status_code").or_else(|| string(status, "code"))
        }
        Some(Value::String(status)) => Some(status.as_str()),
        _ => None,
    }
    .unwrap_or("")
    .to_ascii_lowercase();
    let failed = status == "error"
        || status == "statuscode.error"
        || string(attributes, "castia.tool.status") == Some("error")
        || string(attributes, "error.type").is_some()
        || string(record, "error_type").is_some();
    if !failed {
        return (name, None);
    }
    let error_type = string(attributes, "error.type")
        .or_else(|| string(record, "error_type"))
        .unwrap_or("tool_error")
        .to_string();
    (name, Some(error_type))
}

fn string<'a>(fields: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    fields
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

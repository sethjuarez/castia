use crate::model::ObserveRecordsRuntime;
use chrono::{DateTime, FixedOffset, SecondsFormat, Utc};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaObserveRecordsRuntime;

#[async_trait::async_trait]
impl ObserveRecordsRuntime for CastiaObserveRecordsRuntime {
    fn normalize_record(&self, row: &Value, include_content: &bool) -> Value {
        normalize_record(row, *include_content).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn summarize(&self, records: &Value) -> Value {
        let records = records
            .as_array()
            .unwrap_or_else(|| panic!("records must be an array"));
        summarize(records).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct ObserveRecordsError(String);

impl std::fmt::Display for ObserveRecordsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ObserveRecordsError {}

pub fn normalize_record(row: &Value, include_content: bool) -> Result<Value, ObserveRecordsError> {
    let row = row
        .as_object()
        .ok_or_else(|| ObserveRecordsError("row must be a mapping".to_string()))?;
    let mut dimensions = mapping(
        truthy_source(row.get("customDimensions")).or_else(|| truthy_source(row.get("Properties"))),
    );
    if let Some(attributes) = dimensions.get("attributes").cloned() {
        dimensions.extend(mapping(Some(&attributes)));
    }
    let measurements = mapping(
        truthy_source(row.get("customMeasurements"))
            .or_else(|| truthy_source(row.get("Measurements"))),
    );

    let pick = |names: &[&str]| -> Option<Value> {
        for source in [row, &dimensions, &measurements] {
            for name in names {
                if let Some(value) = source.get(*name) {
                    if !value.is_null() && value.as_str() != Some("") {
                        return Some(value.clone());
                    }
                }
            }
        }
        None
    };

    let success = pick(&["success", "Success"]);
    let status = pick(&["status", "otel.status_code", "status.code"]);
    let error = pick(&["error.type"]);
    let normalized_status = if boolish(&success) == Some(false)
        || status.as_ref().and_then(text).is_some_and(|value| {
            matches!(
                value.to_ascii_lowercase().as_str(),
                "error" | "failed" | "failure" | "2"
            )
        })
        || error.as_ref().is_some_and(is_truthy)
    {
        "error"
    } else if boolish(&success) == Some(true)
        || status.as_ref().and_then(text).is_some_and(|value| {
            matches!(
                value.to_ascii_lowercase().as_str(),
                "ok" | "success" | "succeeded" | "1"
            )
        })
    {
        "success"
    } else {
        "unknown"
    };

    let input_tokens = number(
        pick(&[
            "input_tokens",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.prompt_tokens",
        ])
        .as_ref(),
        true,
    );
    let output_tokens = number(
        pick(&[
            "output_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.completion_tokens",
        ])
        .as_ref(),
        true,
    );
    let mut total_tokens = number(
        pick(&["total_tokens", "gen_ai.usage.total_tokens"]).as_ref(),
        true,
    );
    if total_tokens.is_none() {
        if let (Some(input), Some(output)) = (input_tokens, output_tokens) {
            total_tokens = Some(input + output);
        }
    }

    let identity = pick(&["gen_ai.agent.id"]).as_ref().and_then(text);
    let mut agent_name = pick(&[
        "agent_name",
        "azure.ai.agentserver.agent_name",
        "gen_ai.agent.name",
        "service.name",
        "cloud_RoleName",
    ])
    .as_ref()
    .and_then(text);
    let explicit_version = row.contains_key("agent_version");
    let mut agent_version = if explicit_version {
        row.get("agent_version").and_then(text)
    } else {
        pick(&[
            "agent_version",
            "azure.ai.agentserver.agent_version",
            "gen_ai.agent.version",
            "service.version",
        ])
        .as_ref()
        .and_then(text)
    };
    if let Some(identity) = identity {
        if let Some((name, version)) = identity.rsplit_once(':') {
            if agent_name.is_none() {
                agent_name = Some(name.to_string());
            }
            if !explicit_version && agent_version.is_none() {
                agent_version = Some(version.to_string());
            }
        }
    }

    let mut record = Map::from_iter([
        (
            "timestamp".to_string(),
            optional_string(timestamp(pick(&["timestamp", "TimeGenerated"]).as_ref())),
        ),
        ("agent_name".to_string(), optional_string(agent_name)),
        ("agent_version".to_string(), optional_string(agent_version)),
        (
            "trace_id".to_string(),
            optional_string(
                pick(&["trace_id", "operation_Id", "OperationId"])
                    .as_ref()
                    .and_then(text),
            ),
        ),
        (
            "span_id".to_string(),
            optional_string(pick(&["span_id", "id", "Id"]).as_ref().and_then(text)),
        ),
        (
            "parent_id".to_string(),
            optional_string(
                pick(&["parent_id", "operation_ParentId", "ParentId"])
                    .as_ref()
                    .and_then(text),
            ),
        ),
        (
            "source".to_string(),
            optional_string(
                pick(&["source", "itemType", "Type"])
                    .as_ref()
                    .and_then(text),
            ),
        ),
        (
            "operation".to_string(),
            optional_string(pick(&["operation", "name", "Name"]).as_ref().and_then(text)),
        ),
        (
            "status".to_string(),
            Value::String(normalized_status.to_string()),
        ),
        (
            "latency_ms".to_string(),
            optional_number(number(
                pick(&["latency_ms", "duration_ms", "duration", "DurationMs"]).as_ref(),
                false,
            )),
        ),
        ("input_tokens".to_string(), optional_integer(input_tokens)),
        ("output_tokens".to_string(), optional_integer(output_tokens)),
        ("total_tokens".to_string(), optional_integer(total_tokens)),
        (
            "tool_name".to_string(),
            optional_string(
                pick(&["tool_name", "gen_ai.tool.name"])
                    .as_ref()
                    .and_then(text),
            ),
        ),
        (
            "probe_tag".to_string(),
            optional_string(
                pick(&[
                    "probe_tag",
                    "castia.probe_tag",
                    "castia.probe_id",
                    "probe_id",
                ])
                .as_ref()
                .and_then(text),
            ),
        ),
        ("cost".to_string(), Value::Null),
    ]);

    if include_content {
        let content = [
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.prompt",
            "gen_ai.completion",
        ]
        .into_iter()
        .filter_map(|key| {
            dimensions
                .get(key)
                .cloned()
                .map(|value| (key.to_string(), value))
        })
        .collect::<Map<_, _>>();
        if !content.is_empty() {
            record.insert("content".to_string(), Value::Object(content));
        }
    }
    validate_record(&Value::Object(record.clone()))?;
    Ok(Value::Object(record))
}

pub fn summarize(records: &[Value]) -> Result<Value, ObserveRecordsError> {
    let rows = records
        .iter()
        .map(|record| validate_record(record).map(|_| record))
        .collect::<Result<Vec<_>, _>>()?;
    let known_status = rows
        .iter()
        .filter(|row| status_of(row) != "unknown")
        .count();
    let errors = rows.iter().filter(|row| status_of(row) == "error").count();
    let mut latencies = rows
        .iter()
        .filter_map(|row| row.get("latency_ms").and_then(Value::as_f64))
        .collect::<Vec<_>>();
    latencies.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let tools = rows
        .iter()
        .filter_map(|row| row.get("tool_name").and_then(Value::as_str))
        .collect::<BTreeSet<_>>();
    let tool_errors = tools
        .iter()
        .map(|tool| {
            let known = rows.iter().any(|row| {
                row.get("tool_name").and_then(Value::as_str) == Some(*tool)
                    && status_of(row) != "unknown"
            });
            let errors = rows
                .iter()
                .filter(|row| {
                    row.get("tool_name").and_then(Value::as_str) == Some(*tool)
                        && status_of(row) == "error"
                })
                .count();
            (
                (*tool).to_string(),
                if known { json!(errors) } else { Value::Null },
            )
        })
        .collect::<Map<_, _>>();
    let tool_status_counts = tools
        .iter()
        .map(|tool| {
            let known = rows
                .iter()
                .filter(|row| {
                    row.get("tool_name").and_then(Value::as_str) == Some(*tool)
                        && status_of(row) != "unknown"
                })
                .count();
            let unknown = rows
                .iter()
                .filter(|row| {
                    row.get("tool_name").and_then(Value::as_str) == Some(*tool)
                        && status_of(row) == "unknown"
                })
                .count();
            (
                (*tool).to_string(),
                json!({"known": known, "unknown": unknown}),
            )
        })
        .collect::<Map<_, _>>();

    Ok(json!({
        "record_count": rows.len(),
        "error_count": if known_status == 0 { Value::Null } else { json!(errors) },
        "observed_error_count": errors,
        "known_status_count": known_status,
        "unknown_status_count": rows.len() - known_status,
        "error_rate": if known_status == 0 { Value::Null } else { json!(errors as f64 / known_status as f64) },
        "error_rate_denominator": "known_status_records",
        "latency_ms": {
            "count": latencies.len(),
            "unknown_count": rows.len() - latencies.len(),
            "min": optional_number(latencies.first().copied()),
            "max": optional_number(latencies.last().copied()),
            "p50": optional_number(percentile(&latencies, 0.50)),
            "p95": optional_number(percentile(&latencies, 0.95)),
            "p99": optional_number(percentile(&latencies, 0.99)),
        },
        "tool_errors": tool_errors,
        "tool_status_counts": tool_status_counts,
        "usage": {
            "input_tokens": usage(rows.as_slice(), "input_tokens"),
            "output_tokens": usage(rows.as_slice(), "output_tokens"),
            "total_tokens": usage(rows.as_slice(), "total_tokens"),
        },
        "cost": null,
        "cost_status": "unknown",
        "scope": "observed_spans_may_be_sampled_or_duplicate_usage",
    }))
}

fn validate_record(record: &Value) -> Result<(), ObserveRecordsError> {
    let object = record
        .as_object()
        .ok_or_else(|| ObserveRecordsError("record must be a mapping".to_string()))?;
    if object.contains_key("status")
        && !matches!(
            object.get("status").and_then(Value::as_str),
            Some("unknown" | "success" | "error")
        )
    {
        return Err(ObserveRecordsError(
            "execution status must be unknown, success, or error".to_string(),
        ));
    }
    for name in ["input_tokens", "output_tokens", "total_tokens"] {
        if let Some(value) = object.get(name) {
            if !value.is_null()
                && (value.as_i64().is_none() || value.as_i64().is_some_and(|n| n < 0))
            {
                return Err(ObserveRecordsError(format!(
                    "{name} must be a nonnegative integer or null"
                )));
            }
        }
    }
    for name in ["latency_ms", "cost"] {
        if let Some(value) = object.get(name) {
            if !value.is_null()
                && (value.as_f64().is_none()
                    || value.as_f64().is_some_and(|n| !n.is_finite() || n < 0.0))
            {
                return Err(ObserveRecordsError(format!(
                    "{name} must be a nonnegative finite number or null"
                )));
            }
        }
    }
    Ok(())
}

fn mapping(value: Option<&Value>) -> Map<String, Value> {
    match value {
        Some(Value::Object(object)) => object.clone(),
        Some(Value::String(raw)) => serde_json::from_str::<Value>(raw)
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default(),
        _ => Map::new(),
    }
}

fn text(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::String(value) if value.is_empty() => None,
        Value::String(value) => Some(value.clone()),
        other => Some(other.to_string().trim_matches('"').to_string()),
    }
}

fn boolish(value: &Option<Value>) -> Option<bool> {
    match value {
        Some(Value::Bool(value)) => Some(*value),
        Some(Value::String(value)) if value.eq_ignore_ascii_case("true") => Some(true),
        Some(Value::String(value)) if value.eq_ignore_ascii_case("false") => Some(false),
        _ => None,
    }
}

fn number(value: Option<&Value>, integer: bool) -> Option<f64> {
    let number = match value? {
        Value::Number(number) => number.as_f64()?,
        Value::String(value) if !value.is_empty() => value.trim().parse::<f64>().ok()?,
        _ => return None,
    };
    if !number.is_finite() || number < 0.0 || (integer && number.fract() != 0.0) {
        return None;
    }
    Some(number)
}

fn timestamp(value: Option<&Value>) -> Option<String> {
    let mut raw = text(value?)?;
    if raw.contains(' ') && !raw.contains('T') {
        raw = raw.replacen(' ', "T", 1);
    }
    if raw.len() > 5 {
        let suffix_start = raw.len() - 5;
        if let Some(offset) = raw.get(suffix_start..) {
            if matches!(offset.as_bytes()[0], b'+' | b'-')
                && offset[1..].chars().all(|c| c.is_ascii_digit())
            {
                raw.insert(raw.len() - 2, ':');
            }
        }
    }
    DateTime::parse_from_rfc3339(&raw)
        .map(|dt: DateTime<FixedOffset>| {
            dt.with_timezone(&Utc).to_rfc3339_opts(
                if dt.timestamp_subsec_nanos() == 0 {
                    SecondsFormat::Secs
                } else {
                    SecondsFormat::Micros
                },
                true,
            )
        })
        .ok()
}

fn optional_string(value: Option<String>) -> Value {
    value.map(Value::String).unwrap_or(Value::Null)
}

fn optional_number(value: Option<f64>) -> Value {
    value
        .and_then(serde_json::Number::from_f64)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

fn optional_integer(value: Option<f64>) -> Value {
    value
        .map(|value| Value::Number(serde_json::Number::from(value as i64)))
        .unwrap_or(Value::Null)
}

fn percentile(values: &[f64], fraction: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let position = (values.len() - 1) as f64 * fraction;
    let lo = position.floor() as usize;
    let hi = position.ceil() as usize;
    Some(values[lo] + (values[hi] - values[lo]) * (position - lo as f64))
}

fn usage(rows: &[&Value], field: &str) -> Value {
    let values = rows
        .iter()
        .filter_map(|row| row.get(field).and_then(Value::as_i64))
        .collect::<Vec<_>>();
    json!({
        "observed_sum": if values.is_empty() { Value::Null } else { json!(values.iter().sum::<i64>()) },
        "known_records": values.len(),
        "unknown_records": rows.len() - values.len(),
    })
}

fn status_of(row: &Value) -> &str {
    row.get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
}

fn truthy_source(value: Option<&Value>) -> Option<&Value> {
    match value {
        Some(Value::Object(object)) if !object.is_empty() => value,
        Some(Value::String(text)) if !text.is_empty() => value,
        _ => None,
    }
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

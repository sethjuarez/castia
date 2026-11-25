//! Payload builders for scheduled (`timerTrigger`) evaluation rules and windowed
//! trace evaluation runs. Mirrors `castia.evaluation.schedule` in the Python SDK.

use crate::model::EvaluationScheduleRuntime;
use serde_json::{json, Map, Value};
use std::error::Error;
use std::fmt;

pub const TRACE_SOURCE_TYPE: &str = "azure_ai_trace_data_source";
pub const FILTER_STRATEGIES: [&str; 2] = ["random_sampling", "smart_filtering"];
pub const SOURCE_KINDS: [&str; 2] = ["traces", "dataset"];
pub const DEFAULT_QUERY_TEMPLATE: &str = "{{item.query}}";

#[derive(Debug, Clone)]
pub struct EvaluationScheduleError(pub String);

impl fmt::Display for EvaluationScheduleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl Error for EvaluationScheduleError {}

type ScheduleResult<T> = Result<T, EvaluationScheduleError>;

fn fail<T>(message: impl Into<String>) -> ScheduleResult<T> {
    Err(EvaluationScheduleError(message.into()))
}

fn text<'a>(request: &'a Value, key: &str) -> Option<&'a str> {
    request.get(key).and_then(Value::as_str)
}

fn non_empty<'a>(request: &'a Value, key: &str) -> Option<&'a str> {
    text(request, key).filter(|value| !value.is_empty())
}

fn optional_positive(request: &Value, key: &str) -> ScheduleResult<Option<i64>> {
    match request.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(value) => match value.as_i64() {
            Some(number) if number > 0 => Ok(Some(number)),
            _ => fail(format!("{key} must be greater than zero")),
        },
    }
}

/// Parse a trace window such as `30m`, `24h` or `7d` into seconds.
pub fn parse_window(text: &str) -> ScheduleResult<u64> {
    let invalid = || EvaluationScheduleError("window must look like 30m, 24h, or 7d".into());
    let trimmed = text.trim();
    let unit = trimmed.chars().last().ok_or_else(invalid)?;
    let multiplier = match unit.to_ascii_lowercase() {
        'm' => 60,
        'h' => 3600,
        'd' => 86400,
        _ => return Err(invalid()),
    };
    let digits = trimmed[..trimmed.len() - unit.len_utf8()].trim_end();
    if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
        return Err(invalid());
    }
    let amount: u64 = digits.parse().map_err(|_| invalid())?;
    let seconds = amount.checked_mul(multiplier).ok_or_else(invalid)?;
    if seconds == 0 {
        return fail("window must be greater than zero");
    }
    Ok(seconds)
}

/// Build a `TraceSourceEvalRunDataSource` over an agent's recent traces.
pub fn trace_window_source(request: &Value) -> ScheduleResult<Value> {
    let Some(agent_name) = non_empty(request, "agent_name") else {
        return fail("agent_name is required");
    };
    let Some(window) = optional_positive(request, "window_seconds")? else {
        return fail("window_seconds must be greater than zero");
    };
    let max_traces = optional_positive(request, "max_traces")?;
    let filter_strategy = match request.get("filter_strategy") {
        None | Some(Value::Null) => None,
        Some(value) => match value.as_str() {
            Some(strategy) if FILTER_STRATEGIES.contains(&strategy) => Some(strategy),
            _ => {
                return fail(format!(
                    "filter_strategy must be one of {}",
                    FILTER_STRATEGIES.join(", ")
                ))
            }
        },
    };
    let end_time = request.get("end_time").and_then(Value::as_i64).unwrap_or(0);
    let mut source = Map::new();
    source.insert("type".into(), json!("agent_filter"));
    source.insert("agent_name".into(), json!(agent_name));
    source.insert("start_time".into(), json!(end_time - window));
    source.insert("end_time".into(), json!(end_time));
    if let Some(version) = non_empty(request, "agent_version") {
        source.insert("agent_version".into(), json!(version));
    }
    if let Some(max) = max_traces {
        source.insert("max_traces".into(), json!(max));
    }
    if let Some(strategy) = filter_strategy {
        source.insert("filter_strategy".into(), json!(strategy));
    }
    Ok(json!({ "type": TRACE_SOURCE_TYPE, "trace_source": Value::Object(source) }))
}

/// Build the timer trigger for a `timerTrigger` evaluation rule.
pub fn schedule_trigger(request: &Value) -> ScheduleResult<Value> {
    let cron = request.get("cron").filter(|v| !v.is_null());
    let every_hours = request.get("every_hours").filter(|v| !v.is_null());
    let daily_hours = request
        .get("daily_hours")
        .filter(|v| !v.is_null() && v.as_array().map_or(true, |items| !items.is_empty()));
    let chosen = [cron, every_hours, daily_hours]
        .iter()
        .filter(|value| value.is_some())
        .count();
    if chosen != 1 {
        return fail("set exactly one of cron, every_hours, or daily_hours");
    }
    let mut trigger = if let Some(cron) = cron {
        let fields: Vec<&str> = cron
            .as_str()
            .unwrap_or_default()
            .split_whitespace()
            .collect();
        if fields.len() != 5 {
            return fail("cron must have five fields: minute hour day month weekday");
        }
        json!({ "type": "Cron", "expression": fields.join(" ") })
    } else if every_hours.is_some() {
        let hours = optional_positive(request, "every_hours")?.unwrap_or_default();
        json!({ "type": "Recurrence", "interval": hours, "schedule": { "type": "Hourly" } })
    } else {
        let mut hours: Vec<i64> = Vec::new();
        for value in daily_hours.and_then(Value::as_array).into_iter().flatten() {
            match value.as_i64() {
                Some(hour) if (0..=23).contains(&hour) => hours.push(hour),
                _ => return fail("daily_hours must be between 0 and 23"),
            }
        }
        hours.sort_unstable();
        hours.dedup();
        json!({
            "type": "Recurrence",
            "interval": 1,
            "schedule": { "type": "Daily", "hours": hours },
        })
    };
    if let Some(zone) = non_empty(request, "time_zone") {
        trigger["timeZone"] = json!(zone);
    }
    Ok(trigger)
}

/// Build a scheduled (`timerTrigger`) dataset or trace evaluation rule.
pub fn evaluation_rule(request: &Value) -> ScheduleResult<Value> {
    let Some(eval_id) = non_empty(request, "eval_id") else {
        return fail("eval_id is required");
    };
    let Some(agent_name) = non_empty(request, "agent_name") else {
        return fail("agent_name is required");
    };
    let max_traces = optional_positive(request, "max_traces")?;
    let dataset_file_id = non_empty(request, "dataset_file_id");
    let action = match text(request, "source_kind") {
        Some("traces") => {
            if dataset_file_id.is_some() {
                return fail("dataset_file_id is only valid for dataset rules");
            }
            let mut source = json!({ "type": "azure_ai_traces", "agent_name": agent_name });
            if let Some(max) = max_traces {
                source["max_traces"] = json!(max);
            }
            json!({ "type": "traceEvaluation", "evalId": eval_id, "source": source })
        }
        Some("dataset") => {
            let Some(file_id) = dataset_file_id else {
                return fail("dataset rules require dataset_file_id");
            };
            if max_traces.is_some() {
                return fail("max_traces is only valid for trace rules");
            }
            let mut target = json!({ "type": "azure_ai_agent", "name": agent_name });
            if let Some(version) = non_empty(request, "agent_version") {
                target["version"] = json!(version);
            }
            let template = non_empty(request, "query_template").unwrap_or(DEFAULT_QUERY_TEMPLATE);
            json!({
                "type": "datasetEvaluation",
                "evalId": eval_id,
                "source": {
                    "type": "azure_ai_target_completions",
                    "source": { "type": "file_id", "id": file_id },
                    "input_messages": {
                        "type": "template",
                        "template": [{
                            "type": "message",
                            "role": "user",
                            "content": { "type": "input_text", "text": template },
                        }],
                    },
                    "target": target,
                },
            })
        }
        _ => {
            return fail(format!(
                "source_kind must be one of {}",
                SOURCE_KINDS.join(", ")
            ))
        }
    };
    let enabled = request
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let mut rule = json!({
        "action": action,
        "eventType": "timerTrigger",
        "schedule": request.get("trigger").cloned().unwrap_or(Value::Null),
        "enabled": enabled,
    });
    if let Some(name) = non_empty(request, "display_name") {
        rule["displayName"] = json!(name);
    }
    Ok(rule)
}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaEvaluationScheduleRuntime;

#[async_trait::async_trait]
impl EvaluationScheduleRuntime for CastiaEvaluationScheduleRuntime {
    fn evaluation_rule(&self, request: &Value) -> Value {
        evaluation_rule(request).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn parse_window(&self, text: &String) -> Value {
        json!(parse_window(text).unwrap_or_else(|error| panic!("{}", error.0)))
    }

    fn schedule_trigger(&self, request: &Value) -> Value {
        schedule_trigger(request).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn trace_window_source(&self, request: &Value) -> Value {
        trace_window_source(request).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

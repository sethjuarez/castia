use crate::model::ObserveTelemetryRuntime;
use chrono::{DateTime, SecondsFormat, Utc};
use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct ObserveError {
    pub category: String,
    pub message: String,
    pub status_code: Option<i64>,
}

impl ObserveError {
    fn new(category: &str, message: &str, status_code: Option<i64>) -> Self {
        Self {
            category: category.to_string(),
            message: message.to_string(),
            status_code,
        }
    }

    pub fn to_value(&self) -> Value {
        json!({"category": self.category, "message": self.message, "status_code": self.status_code})
    }
}

impl std::fmt::Display for ObserveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ObserveError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaObserveTelemetryRuntime;

#[async_trait::async_trait]
impl ObserveTelemetryRuntime for CastiaObserveTelemetryRuntime {
    fn trace_query_kql(&self, query: &Value) -> Value {
        Value::String(trace_query_kql(query).unwrap_or_else(|error| panic!("{}", error.message)))
    }

    fn http_error(&self, status: &i32) -> Value {
        http_error(*status as i64).to_value()
    }

    fn verify_probe(
        &self,
        attempts: &Value,
        probe_tag: &String,
        timeout_seconds: &f64,
        poll_seconds: &f64,
        max_attempts: &i32,
    ) -> Value {
        verify_probe(
            attempts,
            probe_tag,
            *timeout_seconds,
            *poll_seconds,
            *max_attempts as i64,
        )
        .unwrap_or_else(|error| panic!("{}", error.message))
    }

    fn telemetry_probe_plan(
        &self,
        query: &Value,
        ingestion_status: &String,
        ingestion_diagnostic: &String,
        ingestion_evidence: &Value,
    ) -> Value {
        telemetry_probe_plan(
            query,
            ingestion_status,
            ingestion_diagnostic,
            ingestion_evidence,
        )
    }
}

pub fn http_error(status: i64) -> ObserveError {
    let category = match status {
        401 => "authentication",
        403 => "authorization",
        404 => "not_found",
        408 => "timeout",
        429 => "throttled",
        value if value >= 500 => "service",
        _ => "request",
    };
    ObserveError::new(
        category,
        &format!("Service returned HTTP {status}."),
        Some(status),
    )
}

pub fn trace_query_kql(query: &Value) -> Result<String, ObserveError> {
    let object = query
        .as_object()
        .ok_or_else(|| invalid("query must be an object"))?;
    let agent_name = required_text(object.get("agent_name"), "agent_name")?;
    let start = parse_time(object.get("start"), "start")?;
    let end = parse_time(object.get("end"), "end")?;
    if start >= end {
        return Err(invalid("start must precede end"));
    }
    let limit = positive_i64(object.get("limit").unwrap_or(&json!(100)), "limit")?;
    if limit > 10_000 {
        return Err(invalid("limit must not exceed 10000"));
    }
    let include_content = match object.get("include_content").unwrap_or(&Value::Bool(false)) {
        Value::Bool(value) => *value,
        _ => return Err(invalid("include_content must be a boolean")),
    };
    let anchor = match object.get("anchor") {
        Some(Value::String(value)) => value.as_str(),
        Some(Value::Null) | None => "requests",
        _ => return Err(invalid("anchor must be requests or invoke_agent")),
    };
    if !matches!(anchor, "requests" | "invoke_agent") {
        return Err(invalid("anchor must be requests or invoke_agent"));
    }
    let agent_version = optional_nonempty(object.get("agent_version"), "agent_version")?;
    let probe_tag = optional_nonempty(object.get("probe_tag"), "probe_tag")?;
    let trace_id = optional_nonempty(object.get("trace_id"), "trace_id")?;
    let literal = ascii_json_literal;
    let start = isoformat_utc(start);
    let end = isoformat_utc(end);
    let source = if anchor == "requests" {
        "requests"
    } else {
        "union isfuzzy=true dependencies, customEvents"
    };
    let mut lines = vec![
        format!("let scoped_operations = {source}"),
        format!("| where timestamp >= datetime({start}) and timestamp < datetime({end})"),
    ];
    if anchor == "invoke_agent" {
        lines.push(
            "| where tostring(customDimensions[\"gen_ai.operation.name\"]) == \"invoke_agent\""
                .to_string(),
        );
    }
    if let Some(trace_id) = trace_id {
        lines.push(format!("| where operation_Id == {}", literal(trace_id)));
    }
    lines.extend([
        "| extend agent_name = tostring(customDimensions[\"gen_ai.agent.name\"]), hosted_agent_name = tostring(customDimensions[\"azure.ai.agentserver.agent_name\"]), agent_version = coalesce(tostring(customDimensions[\"gen_ai.agent.version\"]), tostring(customDimensions[\"azure.ai.agentserver.agent_version\"]), tostring(customDimensions[\"service.version\"])), agent_id = tostring(customDimensions[\"gen_ai.agent.id\"]), probe_tag = coalesce(tostring(customDimensions[\"castia.probe_tag\"]), tostring(customDimensions[\"castia.probe_id\"]), tostring(customDimensions[\"probe_id\"]))".to_string(),
        format!(
            "| where agent_name == {} or hosted_agent_name == {} or agent_id startswith {}",
            literal(agent_name),
            literal(agent_name),
            literal(&format!("{agent_name}:"))
        ),
    ]);
    if let Some(agent_version) = agent_version {
        lines.push(format!(
            "| where agent_version == {} or agent_id == {}",
            literal(agent_version),
            literal(&format!("{agent_name}:{agent_version}"))
        ));
    }
    lines.extend([
        "| where isnotempty(operation_Id)".to_string(),
        "| summarize arg_max(timestamp, agent_version, probe_tag) by operation_Id".to_string(),
        "| project operation_Id, root_agent_version=agent_version, root_probe_tag=probe_tag;".to_string(),
        "union isfuzzy=true withsource=castia_table_name requests, dependencies, customEvents".to_string(),
        format!("| where timestamp >= datetime({start}) and timestamp < datetime({end})"),
        "| join kind=inner (scoped_operations) on operation_Id".to_string(),
        format!(
            "| extend agent_name={}, agent_version=root_agent_version, probe_tag=coalesce(tostring(customDimensions[\"castia.probe_tag\"]), tostring(customDimensions[\"castia.probe_id\"]), tostring(customDimensions[\"probe_id\"]), root_probe_tag)",
            literal(agent_name)
        ),
    ]);
    if let Some(probe_tag) = probe_tag {
        lines.push(format!("| where probe_tag == {}", literal(probe_tag)));
    }
    let mut keys = vec![
        "gen_ai.agent.name",
        "gen_ai.agent.version",
        "gen_ai.agent.id",
        "azure.ai.agentserver.agent_name",
        "azure.ai.agentserver.agent_version",
        "service.name",
        "service.version",
        "gen_ai.tool.name",
        "castia.probe_tag",
        "castia.probe_id",
        "probe_id",
        "gen_ai.operation.name",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.prompt_tokens",
        "gen_ai.usage.completion_tokens",
        "gen_ai.usage.total_tokens",
        "otel.status_code",
        "status.code",
        "error.type",
    ];
    if include_content {
        keys.extend([
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.prompt",
            "gen_ai.completion",
        ]);
    }
    let packed = keys
        .iter()
        .map(|key| format!("{}, customDimensions[{}]", literal(key), literal(key)))
        .collect::<Vec<_>>()
        .join(", ");
    lines.extend([
        format!("| project timestamp, source=castia_table_name, name, operation_Id, operation_ParentId, agent_name, agent_version, probe_tag, id=tostring(column_ifexists(\"id\", \"\")), success=tostring(column_ifexists(\"success\", \"\")), duration=todouble(column_ifexists(\"duration\", real(null))), customDimensions=bag_pack({packed}), customMeasurements=bag_pack(\"gen_ai.usage.input_tokens\", customMeasurements[\"gen_ai.usage.input_tokens\"], \"gen_ai.usage.output_tokens\", customMeasurements[\"gen_ai.usage.output_tokens\"], \"gen_ai.usage.total_tokens\", customMeasurements[\"gen_ai.usage.total_tokens\"])"),
        "| order by timestamp desc".to_string(),
        format!("| take {limit}"),
    ]);
    Ok(lines.join("\n"))
}

pub fn verify_probe(
    attempts: &Value,
    probe_tag: &str,
    timeout_seconds: f64,
    poll_seconds: f64,
    max_attempts: i64,
) -> Result<Value, ObserveError> {
    if probe_tag.trim().is_empty() {
        return Err(invalid("probe_tag is required"));
    }
    positive(timeout_seconds, "timeout_seconds")?;
    positive(poll_seconds, "poll_seconds")?;
    if max_attempts <= 0 {
        return Err(invalid("max_attempts must be a positive integer"));
    }
    let empty = Vec::new();
    let attempts_data = attempts.as_array().unwrap_or(&empty);
    let mut elapsed = 0.0;
    let mut actual_attempts = 0;
    while actual_attempts < max_attempts && elapsed < timeout_seconds {
        let records = attempts_data
            .get(actual_attempts as usize)
            .and_then(Value::as_array)
            .unwrap_or(&empty);
        actual_attempts += 1;
        let matches = records
            .iter()
            .filter(|record| record.get("probe_tag").and_then(Value::as_str) == Some(probe_tag))
            .count();
        if matches > 0 && elapsed <= timeout_seconds {
            return Ok(json!({
                "status": "pass",
                "probe_tag": probe_tag,
                "attempts": actual_attempts,
                "matched_records": matches,
                "elapsed_seconds": elapsed,
                "diagnostic": "Tagged trace visible.",
            }));
        }
        if timeout_seconds - elapsed <= 0.0 || actual_attempts >= max_attempts {
            break;
        }
        elapsed += poll_seconds.min(timeout_seconds - elapsed);
    }
    Ok(json!({
        "status": "blocked",
        "probe_tag": probe_tag,
        "attempts": actual_attempts,
        "matched_records": 0,
        "elapsed_seconds": elapsed.max(0.0),
        "diagnostic": "Tagged trace not observed within the ingestion window; not proof of agent failure.",
    }))
}

pub fn telemetry_probe_plan(
    query: &Value,
    ingestion_status: &str,
    ingestion_diagnostic: &str,
    ingestion_evidence: &Value,
) -> Value {
    let mut safe_query = query.as_object().cloned().unwrap_or_default();
    safe_query.insert("includeContent".to_string(), Value::Bool(false));
    safe_query.insert("include_content".to_string(), Value::Bool(false));
    let has_probe_tag = safe_query
        .get("probeTag")
        .or_else(|| safe_query.get("probe_tag"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .is_some();
    let ingestion = if has_probe_tag {
        json!({
            "status": ingestion_status,
            "diagnostic": ingestion_diagnostic,
            "reason": if ingestion_status == "pass" { Value::Null } else { Value::String("ingestion_delay".to_string()) },
            "evidence": ingestion_evidence,
        })
    } else {
        json!({
            "status": "blocked",
            "diagnostic": "An exact tagged probe is required.",
            "reason": "prerequisite",
            "evidence": null,
        })
    };
    json!({
        "query": safe_query,
        "ingestion": ingestion,
    })
}

fn required_text<'a>(value: Option<&'a Value>, name: &str) -> Result<&'a str, ObserveError> {
    value
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| invalid(&format!("{name} is required")))
}

fn optional_nonempty<'a>(
    value: Option<&'a Value>,
    name: &str,
) -> Result<Option<&'a str>, ObserveError> {
    match value {
        Some(Value::Null) | None => Ok(None),
        Some(Value::String(value)) if !value.trim().is_empty() => Ok(Some(value)),
        _ => Err(invalid(&format!("{name} must be nonempty when supplied"))),
    }
}

fn parse_time(value: Option<&Value>, name: &str) -> Result<DateTime<Utc>, ObserveError> {
    let text = required_text(value, name)?;
    DateTime::parse_from_rfc3339(text)
        .map(|dt| dt.with_timezone(&Utc))
        .map_err(|_| invalid("start and end must be timezone-aware datetimes"))
}

fn positive_i64(value: &Value, name: &str) -> Result<i64, ObserveError> {
    match value.as_i64() {
        Some(value) if value > 0 => Ok(value),
        _ => Err(invalid(&format!("{name} must be a positive integer"))),
    }
}

fn positive(value: f64, name: &str) -> Result<(), ObserveError> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(invalid(&format!("{name} must be a positive finite number")))
    }
}

fn invalid(message: &str) -> ObserveError {
    ObserveError::new("invalid_response", message, None)
}

fn isoformat_utc(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(
        if value.timestamp_subsec_nanos() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    )
}

fn ascii_json_literal(value: &str) -> String {
    let mut output = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            ch if ch < ' ' => output.push_str(&format!("\\u{:04x}", ch as u32)),
            ch if ch.is_ascii() => output.push(ch),
            ch => {
                let mut units = [0_u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    output.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    output.push('"');
    output
}

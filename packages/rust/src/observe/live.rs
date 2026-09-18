use crate::model::ObserveLiveRuntime;
use serde_json::{json, Value};
use std::collections::BTreeSet;
use url::Url;

pub const AI_SCOPE: &str = "https://ai.azure.com/.default";

#[derive(Debug, Clone)]
pub struct LiveError(String);

impl std::fmt::Display for LiveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LiveError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaObserveLiveRuntime;

#[async_trait::async_trait]
impl ObserveLiveRuntime for CastiaObserveLiveRuntime {
    fn validate_limits(&self, limits: &Value) -> Value {
        validate_limits(limits).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn validate_live_config(&self, config: &Value) -> Value {
        validate_live_config(config).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn response_text_result(&self, response: &Value, expected: &Option<String>) -> Value {
        response_text_result(response, expected.as_deref())
    }

    fn stream_result(&self, events: &Value) -> Value {
        stream_result(events)
    }

    fn optimizer_status(&self, payload: &Value, expected_id: &Option<String>) -> Value {
        Value::String(
            optimizer_status(payload, expected_id.as_deref())
                .unwrap_or_else(|error| panic!("{}", error.0)),
        )
    }

    fn candidate_config(&self, payload: &Value) -> Value {
        candidate_config(payload).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn validate_limits(limits: &Value) -> Result<Value, LiveError> {
    let object = match limits {
        Value::Null => serde_json::Map::new(),
        Value::Object(object) => object.clone(),
        _ => {
            return Err(LiveError(
                "limits must be a configuration object".to_string(),
            ))
        }
    };
    let max_requests = positive_integer(object.get("max_requests"), "max_requests", 20)?;
    let max_seconds = positive_number(object.get("max_seconds"), "max_seconds", 120.0)?;
    let max_output_tokens =
        positive_integer(object.get("max_output_tokens"), "max_output_tokens", 128)?;
    let max_total_output_tokens = positive_integer(
        object.get("max_total_output_tokens"),
        "max_total_output_tokens",
        1024,
    )?;
    let max_response_bytes = positive_integer(
        object.get("max_response_bytes"),
        "max_response_bytes",
        2_000_000,
    )?;
    if max_output_tokens > max_total_output_tokens {
        return Err(LiveError(
            "max_output_tokens cannot exceed max_total_output_tokens".to_string(),
        ));
    }
    Ok(json!({
        "max_requests": max_requests,
        "max_seconds": max_seconds,
        "max_output_tokens": max_output_tokens,
        "max_total_output_tokens": max_total_output_tokens,
        "max_response_bytes": max_response_bytes,
    }))
}

pub fn validate_live_config(config: &Value) -> Result<Value, LiveError> {
    let object = config
        .as_object()
        .cloned()
        .ok_or_else(|| LiveError("live config must be a configuration object".to_string()))?;
    for name in [
        "project_endpoint",
        "project_read_url",
        "model_url",
        "hosted_responses_url",
        "hosted_invocations_url",
        "toolbox_url",
    ] {
        if let Some(value) = object.get(name).filter(|value| !value.is_null()) {
            let url = value.as_str().ok_or_else(|| {
                LiveError(format!(
                    "{name} must be an absolute HTTPS URL without credentials/fragments"
                ))
            })?;
            no_finetuning(url)?;
            let parsed = Url::parse(url).map_err(|_| {
                LiveError(format!(
                    "{name} must be an absolute HTTPS URL without credentials/fragments"
                ))
            })?;
            if parsed.scheme() != "https"
                || parsed.host_str().is_none()
                || !parsed.username().is_empty()
                || parsed.password().is_some()
                || parsed.fragment().is_some()
            {
                return Err(LiveError(format!(
                    "{name} must be an absolute HTTPS URL without credentials/fragments"
                )));
            }
            if name == "project_endpoint" && parsed.query().is_some() {
                return Err(LiveError(
                    "project_endpoint must not include a query".to_string(),
                ));
            }
        }
    }
    let limits = validate_limits(object.get("limits").unwrap_or(&Value::Null))?;
    let allow_optimizer_submit = match object.get("allow_optimizer_submit") {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        _ => {
            return Err(LiveError(
                "allow_optimizer_submit must be a boolean".to_string(),
            ))
        }
    };
    let optimizer_timeout_seconds = positive_number(
        object.get("optimizer_timeout_seconds"),
        "optimizer_timeout_seconds",
        60.0,
    )?;
    let optimizer_poll_seconds = positive_number(
        object.get("optimizer_poll_seconds"),
        "optimizer_poll_seconds",
        5.0,
    )?;
    let cleanup_timeout_seconds = positive_number(
        object.get("cleanup_timeout_seconds"),
        "cleanup_timeout_seconds",
        5.0,
    )?;
    let toolbox_tools = match object.get("toolbox_tools") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| item.as_str().filter(|value| !value.trim().is_empty()))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| {
                LiveError("toolbox_tools must contain explicit known tool names".to_string())
            })?,
        _ => {
            return Err(LiveError(
                "toolbox_tools must contain explicit known tool names".to_string(),
            ))
        }
    };
    if toolbox_tools.iter().collect::<BTreeSet<_>>().len() != toolbox_tools.len() {
        return Err(LiveError(
            "toolbox_tools must not contain duplicates".to_string(),
        ));
    }
    for name in ["project_read_scope", "hosted_scope"] {
        if let Some(value) = object.get(name).filter(|value| !value.is_null()) {
            if !value.as_str().is_some_and(|value| !value.trim().is_empty()) {
                return Err(LiveError(format!("{name} is required")));
            }
        }
    }
    if object
        .get("optimizer_request")
        .filter(|value| !value.is_null())
        .is_some_and(|value| !value.is_object())
    {
        return Err(LiveError(
            "optimizer_request must be a request mapping".to_string(),
        ));
    }
    if object
        .get("hosted_responses_body")
        .filter(|value| !value.is_null())
        .is_some_and(|value| !value.is_object())
    {
        return Err(LiveError(
            "hosted_responses_body must be an object".to_string(),
        ));
    }
    if object
        .get("hosted_responses_expected_text")
        .filter(|value| !value.is_null())
        .is_some_and(|value| !value.is_string())
    {
        return Err(LiveError(
            "hosted_responses_expected_text must be text".to_string(),
        ));
    }
    if allow_optimizer_submit {
        let request = object
            .get("optimizer_request")
            .and_then(Value::as_object)
            .filter(|request| !request.is_empty())
            .ok_or_else(|| {
                LiveError(
                    "optimizer submission requires project_endpoint and optimizer_request"
                        .to_string(),
                )
            })?;
        if object
            .get("project_endpoint")
            .and_then(Value::as_str)
            .is_none()
        {
            return Err(LiveError(
                "optimizer submission requires project_endpoint and optimizer_request".to_string(),
            ));
        }
        let maximum = request
            .get("options")
            .and_then(Value::as_object)
            .and_then(|options| options.get("max_candidates"));
        let maximum = positive_integer(maximum, "optimizer options.max_candidates", 0)?;
        if maximum > 3 {
            return Err(LiveError(
                "observe optimizer probes support at most 3 candidates".to_string(),
            ));
        }
        if limits
            .get("max_requests")
            .and_then(Value::as_i64)
            .unwrap_or_default()
            < 3
            || limits
                .get("max_seconds")
                .and_then(Value::as_f64)
                .unwrap_or_default()
                <= cleanup_timeout_seconds
        {
            return Err(LiveError(
                "optimizer submission needs at least 3 requests and reserved cleanup time"
                    .to_string(),
            ));
        }
    }
    Ok(json!({
        "project_read_scope": object.get("project_read_scope").and_then(Value::as_str).unwrap_or(AI_SCOPE),
        "hosted_scope": object.get("hosted_scope").and_then(Value::as_str).unwrap_or(AI_SCOPE),
        "allow_optimizer_submit": allow_optimizer_submit,
        "optimizer_timeout_seconds": optimizer_timeout_seconds,
        "optimizer_poll_seconds": optimizer_poll_seconds,
        "cleanup_timeout_seconds": cleanup_timeout_seconds,
        "limits": limits,
    }))
}

pub fn response_text_result(response: &Value, expected: Option<&str>) -> Value {
    let failed = response.get("error").is_some()
        || matches!(
            response.get("status").and_then(Value::as_str),
            Some("failed" | "incomplete" | "cancelled")
        );
    if failed {
        return probe_result(
            "fail",
            "Response did not complete successfully.",
            Some("service"),
            json!({}),
        );
    }
    let text = response_text(response);
    let passed = !text.trim().is_empty() && expected.is_none_or(|expected| text.trim() == expected);
    probe_result(
        if passed { "pass" } else { "fail" },
        if passed {
            "Response text assertion passed."
        } else {
            "Response text assertion failed."
        },
        if passed { None } else { Some("assertion") },
        json!({"usage": usage(response), "output_characters": text.chars().count(), "cost": null}),
    )
}

pub fn stream_result(events: &Value) -> Value {
    let empty = Vec::new();
    let events = events.as_array().unwrap_or(&empty);
    let deltas = events
        .iter()
        .filter(|event| {
            event.get("type").and_then(Value::as_str) == Some("response.output_text.delta")
        })
        .filter_map(|event| event.get("delta").and_then(Value::as_str))
        .collect::<Vec<_>>();
    let completed = events
        .iter()
        .filter(|event| event.get("type").and_then(Value::as_str) == Some("response.completed"))
        .collect::<Vec<_>>();
    let failed = events.iter().any(|event| {
        matches!(
            event.get("type").and_then(Value::as_str),
            Some("error" | "response.failed" | "response.incomplete")
        )
    });
    let passed = !completed.is_empty() && deltas.join("").trim() == "CASTIA_OK" && !failed;
    probe_result(
        if passed { "pass" } else { "fail" },
        if passed {
            "Streaming completion and delta assertions passed."
        } else {
            "Streaming completion/delta assertion failed."
        },
        if passed { None } else { Some("assertion") },
        json!({
            "delta_count": deltas.len(),
            "completed_count": completed.len(),
            "usage": completed.last().map(|event| usage(event.get("response").unwrap_or(&Value::Null))).unwrap_or_else(|| usage(&Value::Null)),
        }),
    )
}

pub fn optimizer_status(payload: &Value, expected_id: Option<&str>) -> Result<String, LiveError> {
    let object = payload
        .as_object()
        .ok_or_else(|| LiveError("Optimizer status must be an object.".to_string()))?;
    let status = object
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| LiveError("Optimizer status is missing or unrecognized.".to_string()))?;
    let lower = status.to_ascii_lowercase();
    let known = [
        "notstarted",
        "not_started",
        "queued",
        "pending",
        "running",
        "inprogress",
        "in_progress",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "canceled",
        "cancelling",
        "canceling",
    ];
    if !known.contains(&lower.as_str()) {
        return Err(LiveError(
            "Optimizer status is missing or unrecognized.".to_string(),
        ));
    }
    for key in ["operation_id", "operationId", "id", "job_id", "jobId"] {
        if let Some(value) = object.get(key) {
            let Some(value) = value.as_str().filter(|value| !value.is_empty()) else {
                return Err(LiveError(
                    "Optimizer returned an invalid or mismatched job identity.".to_string(),
                ));
            };
            if expected_id.is_some_and(|expected| expected != value) {
                return Err(LiveError(
                    "Optimizer returned an invalid or mismatched job identity.".to_string(),
                ));
            }
        }
    }
    if let Some(result) = object.get("result").filter(|value| !value.is_null()) {
        let result = result
            .as_object()
            .ok_or_else(|| LiveError("Optimizer result must be an object or null.".to_string()))?;
        for key in ["best", "best_candidate_id", "bestCandidateId"] {
            if result
                .get(key)
                .filter(|value| !value.is_null())
                .is_some_and(|value| !value.is_string())
            {
                return Err(LiveError(
                    "Optimizer best-candidate identity must be text.".to_string(),
                ));
            }
        }
        if result.get("candidates").is_some_and(|value| {
            !value
                .as_array()
                .is_some_and(|items| items.iter().all(Value::is_object))
        }) {
            return Err(LiveError(
                "Optimizer candidates must be an object list.".to_string(),
            ));
        }
    }
    Ok(lower)
}

pub fn candidate_config(payload: &Value) -> Result<Value, LiveError> {
    let object = payload
        .as_object()
        .ok_or_else(|| LiveError("Optimizer candidate config must be an object.".to_string()))?;
    let recognized = [
        "model",
        "instructions",
        "system_prompt",
        "tools",
        "skills",
        "temperature",
    ];
    if !recognized
        .iter()
        .any(|key| object.get(*key).is_some_and(|value| !value.is_null()))
    {
        return Err(LiveError(
            "Optimizer candidate config has no recognized configuration fields.".to_string(),
        ));
    }
    for key in ["model", "instructions", "system_prompt"] {
        if let Some(value) = object.get(key).filter(|value| !value.is_null()) {
            if !value
                .as_str()
                .is_some_and(|value| key != "model" || !value.trim().is_empty())
            {
                return Err(LiveError(
                    "Optimizer model/prompt fields must contain valid text.".to_string(),
                ));
            }
        }
    }
    for key in ["tools", "skills"] {
        if let Some(value) = object.get(key).filter(|value| !value.is_null()) {
            if !value
                .as_array()
                .is_some_and(|items| items.iter().all(Value::is_object))
            {
                return Err(LiveError(
                    "Optimizer tools/skills must be object lists.".to_string(),
                ));
            }
        }
    }
    if let Some(value) = object.get("temperature").filter(|value| !value.is_null()) {
        if value
            .as_f64()
            .is_none_or(|value| !(0.0..=2.0).contains(&value))
            || value.as_bool().is_some()
        {
            return Err(LiveError(
                "Optimizer temperature must be a finite number from 0 to 2.".to_string(),
            ));
        }
    }
    Ok(json!({"valid": true}))
}

fn positive_integer(value: Option<&Value>, name: &str, default: i64) -> Result<i64, LiveError> {
    match value {
        None | Some(Value::Null) if default > 0 => Ok(default),
        Some(Value::Number(number)) => number
            .as_i64()
            .filter(|value| *value > 0)
            .ok_or_else(|| LiveError(format!("{name} must be a positive integer"))),
        _ => Err(LiveError(format!("{name} must be a positive integer"))),
    }
}

fn positive_number(value: Option<&Value>, name: &str, default: f64) -> Result<f64, LiveError> {
    match value {
        None | Some(Value::Null) => Ok(default),
        Some(Value::Number(number)) => number
            .as_f64()
            .filter(|value| value.is_finite() && *value > 0.0)
            .ok_or_else(|| LiveError(format!("{name} must be a positive finite number"))),
        _ => Err(LiveError(format!(
            "{name} must be a positive finite number"
        ))),
    }
}

fn no_finetuning(url: &str) -> Result<(), LiveError> {
    let path = percent_encoding::percent_decode_str(
        Url::parse(url)
            .map(|url| url.path().to_string())
            .unwrap_or_default()
            .as_str(),
    )
    .decode_utf8_lossy()
    .to_ascii_lowercase()
    .replace(['-', '_'], "");
    if path.contains("/finetuning/jobs") || path.contains("/finetune/jobs") {
        return Err(LiveError(
            "Observation probes never submit or mutate fine-tuning jobs".to_string(),
        ));
    }
    Ok(())
}

fn response_text(data: &Value) -> String {
    if let Some(text) = data.get("output_text").and_then(Value::as_str) {
        return text.to_string();
    }
    data.get("output")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .flat_map(|output| {
            output
                .get("content")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter_map(Value::as_object)
        .filter(|item| item.get("type").and_then(Value::as_str) == Some("output_text"))
        .filter_map(|item| item.get("text").and_then(Value::as_str))
        .collect()
}

fn usage(data: &Value) -> Value {
    let usage = data.get("usage").and_then(Value::as_object);
    let get = |key: &str| -> Value {
        usage
            .and_then(|usage| usage.get(key))
            .and_then(Value::as_i64)
            .filter(|value| *value >= 0)
            .map(Value::from)
            .unwrap_or(Value::Null)
    };
    json!({
        "input_tokens": get("input_tokens"),
        "output_tokens": get("output_tokens"),
        "total_tokens": get("total_tokens"),
    })
}

fn probe_result(status: &str, diagnostic: &str, category: Option<&str>, evidence: Value) -> Value {
    json!({
        "status": status,
        "diagnostic": diagnostic,
        "category": category,
        "evidence": evidence,
    })
}

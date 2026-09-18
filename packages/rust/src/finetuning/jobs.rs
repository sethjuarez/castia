use crate::model::FinetuningJobsRuntime;
use serde_json::{json, Map, Value};
use url::Url;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaFinetuningJobsRuntime;

#[async_trait::async_trait]
impl FinetuningJobsRuntime for CastiaFinetuningJobsRuntime {
    fn deployment_handoff(&self, project_endpoint: &Option<String>, job: &Value) -> Value {
        deployment_handoff(project_endpoint.as_deref(), job)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn download_guard(&self, job: &Value, file_id: &String, max_bytes: &Value) -> Value {
        download_guard(job, file_id, max_bytes).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn download_limit_exceeded(&self, written: &Value, max_bytes: &Value) -> bool {
        download_limit_exceeded(written, max_bytes).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn job_reference(
        &self,
        project_endpoint: &String,
        job_id: &String,
        schema_version: &i32,
    ) -> Value {
        job_reference(project_endpoint, job_id, *schema_version)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn job_page_request(&self, job_id: &String, limit: &Value, after: &Option<String>) -> Value {
        job_page_request(job_id, limit, after.as_deref())
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn page_request(&self, limit: &Value, after: &Option<String>) -> Value {
        page_request(limit, after.as_deref()).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn page_slice(&self, data: &Value, limit: &Value) -> Value {
        page_slice(data, limit).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn parse_job_reference(&self, data: &Value) -> Value {
        parse_job_reference(data).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn result_files(&self, job: &Value) -> Vec<String> {
        result_files(job)
    }

    fn status_request(
        &self,
        job_id: &String,
        request_timeout: &f64,
        timeout: &Option<f64>,
    ) -> Value {
        status_request(job_id, *request_timeout, *timeout)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn terminal_status(&self, status: &String) -> bool {
        terminal_status(status)
    }

    fn validate_request_timeout(&self, request_timeout: &f64) -> Value {
        validate_request_timeout(*request_timeout).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn watch_poll_timeout(&self, request_timeout: &f64, remaining: &f64) -> Value {
        watch_poll_timeout(*request_timeout, *remaining)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn watch_request(&self, job_id: &String, timeout: &f64, poll_interval: &f64) -> Value {
        watch_request(job_id, *timeout, *poll_interval)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn watch_sleep_duration(&self, poll_interval: &f64, remaining: &f64) -> Value {
        watch_sleep_duration(*poll_interval, *remaining)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobManagementError(pub String);

impl std::fmt::Display for JobManagementError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for JobManagementError {}

pub fn job_reference(
    project_endpoint: &str,
    job_id: &str,
    schema_version: i32,
) -> Result<Value, JobManagementError> {
    validate_project_endpoint(project_endpoint)?;
    identifier(job_id, "job_id")?;
    if schema_version != 1 {
        return Err(JobManagementError(
            "unsupported job-reference schema_version".to_string(),
        ));
    }
    Ok(json!({
        "project_endpoint": project_endpoint,
        "job_id": job_id,
        "schema_version": 1,
    }))
}

pub fn parse_job_reference(data: &Value) -> Result<Value, JobManagementError> {
    let Some(object) = data.as_object() else {
        return Err(JobManagementError(
            "invalid fine-tuning job reference".to_string(),
        ));
    };
    let expected = ["project_endpoint", "job_id", "schema_version"];
    if object.len() != expected.len() || expected.iter().any(|key| !object.contains_key(*key)) {
        return Err(JobManagementError(
            "invalid fine-tuning job reference".to_string(),
        ));
    }
    let (Some(project_endpoint), Some(job_id)) = (
        object.get("project_endpoint").and_then(Value::as_str),
        object.get("job_id").and_then(Value::as_str),
    ) else {
        return Err(JobManagementError(
            "job reference endpoint and ID must be strings".to_string(),
        ));
    };
    let schema_version = object
        .get("schema_version")
        .and_then(Value::as_i64)
        .unwrap_or_default() as i32;
    job_reference(project_endpoint, job_id, schema_version)
}

pub fn validate_request_timeout(request_timeout: f64) -> Result<Value, JobManagementError> {
    positive(request_timeout, "request_timeout")?;
    Ok(json_number(request_timeout))
}

pub fn page_request(limit: &Value, after: Option<&str>) -> Result<Value, JobManagementError> {
    let limit = page_size(limit)?;
    let mut request = Map::new();
    request.insert("limit".to_string(), Value::Number(limit.into()));
    if let Some(after) = after {
        request.insert(
            "after".to_string(),
            Value::String(identifier(after, "after")?.to_string()),
        );
    }
    Ok(Value::Object(request))
}

pub fn job_page_request(
    job_id: &str,
    limit: &Value,
    after: Option<&str>,
) -> Result<Value, JobManagementError> {
    let page = page_request(limit, after)?;
    identifier(job_id, "job_id")?;
    let mut request = Map::new();
    request.insert("job_id".to_string(), Value::String(job_id.to_string()));
    if let Some(page) = page.as_object() {
        for (key, value) in page {
            request.insert(key.clone(), value.clone());
        }
    }
    Ok(Value::Object(request))
}

pub fn page_slice(data: &Value, limit: &Value) -> Result<Value, JobManagementError> {
    let limit = page_size(limit)? as usize;
    let Some(data) = data.as_array() else {
        return Err(JobManagementError("page data must be an array".to_string()));
    };
    Ok(Value::Array(data.iter().take(limit).cloned().collect()))
}

pub fn status_request(
    job_id: &str,
    request_timeout: f64,
    timeout: Option<f64>,
) -> Result<Value, JobManagementError> {
    identifier(job_id, "job_id")?;
    positive(request_timeout, "request_timeout")?;
    let timeout = timeout.unwrap_or(request_timeout);
    positive(timeout, "timeout")?;
    let mut request = Map::new();
    request.insert("job_id".to_string(), Value::String(job_id.to_string()));
    request.insert("timeout".to_string(), json_number(timeout));
    Ok(Value::Object(request))
}

pub fn watch_poll_timeout(
    request_timeout: f64,
    remaining: f64,
) -> Result<Value, JobManagementError> {
    positive(request_timeout, "request_timeout")?;
    positive(remaining, "remaining")?;
    Ok(json_number(request_timeout.min(remaining)))
}

pub fn watch_sleep_duration(
    poll_interval: f64,
    remaining: f64,
) -> Result<Value, JobManagementError> {
    positive(poll_interval, "poll_interval")?;
    Ok(json_number(poll_interval.min(remaining.max(0.0))))
}

pub fn watch_request(
    job_id: &str,
    timeout: f64,
    poll_interval: f64,
) -> Result<Value, JobManagementError> {
    identifier(job_id, "job_id")?;
    positive(timeout, "timeout")?;
    positive(poll_interval, "poll_interval")?;
    let mut request = Map::new();
    request.insert("job_id".to_string(), Value::String(job_id.to_string()));
    request.insert("timeout".to_string(), json_number(timeout));
    request.insert("poll_interval".to_string(), json_number(poll_interval));
    Ok(Value::Object(request))
}

pub fn terminal_status(status: &str) -> bool {
    matches!(status, "succeeded" | "failed" | "cancelled" | "canceled")
}

pub fn result_files(job: &Value) -> Vec<String> {
    job.get("result_files")
        .and_then(Value::as_array)
        .map(|files| {
            files
                .iter()
                .filter_map(Value::as_str)
                .map(ToString::to_string)
                .collect()
        })
        .unwrap_or_default()
}

pub fn download_guard(
    job: &Value,
    file_id: &str,
    max_bytes: &Value,
) -> Result<Value, JobManagementError> {
    identifier(file_id, "file_id")?;
    let max_bytes = positive_integer(max_bytes, "max_bytes")?;
    if !result_files(job).iter().any(|result| result == file_id) {
        return Err(JobManagementError(
            "file_id is not a result file of the specified job".to_string(),
        ));
    }
    Ok(json!({
        "file_id": file_id,
        "max_bytes": max_bytes,
    }))
}

pub fn download_limit_exceeded(
    written: &Value,
    max_bytes: &Value,
) -> Result<bool, JobManagementError> {
    let written = positive_integer(written, "written")?;
    let max_bytes = positive_integer(max_bytes, "max_bytes")?;
    Ok(written > max_bytes)
}

pub fn deployment_handoff(
    project_endpoint: Option<&str>,
    job: &Value,
) -> Result<Value, JobManagementError> {
    let status = job.get("status").and_then(Value::as_str);
    let fine_tuned_model = job.get("fine_tuned_model").and_then(Value::as_str);
    if status != Some("succeeded") || fine_tuned_model.is_none_or(str::is_empty) {
        return Err(JobManagementError(
            "deployment handoff requires a succeeded job with a model".to_string(),
        ));
    }
    let Some(job_id) = job.get("id").and_then(Value::as_str) else {
        return Err(JobManagementError(
            "deployment handoff requires a succeeded job with a model".to_string(),
        ));
    };
    identifier(job_id, "job_id").map_err(|_| {
        JobManagementError("deployment handoff requires a succeeded job with a model".to_string())
    })?;
    Ok(json!({
        "schema_version": 1,
        "job_id": job_id,
        "project_endpoint": project_endpoint,
        "fine_tuned_model": fine_tuned_model.unwrap(),
        "result_files": result_files(job),
        "deployment_status": "not_deployed",
        "next_step": "Deploy the selected model externally, then evaluate the deployment.",
    }))
}

fn validate_project_endpoint(project_endpoint: &str) -> Result<(), JobManagementError> {
    let parsed = Url::parse(project_endpoint).map_err(|_| {
        JobManagementError(
            "project_endpoint must be HTTPS without credentials or query".to_string(),
        )
    })?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some_and(|query| !query.is_empty())
        || parsed
            .fragment()
            .is_some_and(|fragment| !fragment.is_empty())
    {
        return Err(JobManagementError(
            "project_endpoint must be HTTPS without credentials or query".to_string(),
        ));
    }
    Ok(())
}

fn identifier<'a>(value: &'a str, name: &str) -> Result<&'a str, JobManagementError> {
    if value.is_empty()
        || value.trim() != value
        || value
            .chars()
            .any(|character| matches!(character, '/' | '\\' | '?' | '#' | '\r' | '\n'))
    {
        return Err(JobManagementError(format!(
            "{name} must be a nonempty service identifier"
        )));
    }
    Ok(value)
}

fn page_size(value: &Value) -> Result<i64, JobManagementError> {
    let Some(limit) = value.as_i64() else {
        return Err(JobManagementError(
            "limit must be an integer between 1 and 100".to_string(),
        ));
    };
    if !(1..=100).contains(&limit) {
        return Err(JobManagementError(
            "limit must be an integer between 1 and 100".to_string(),
        ));
    }
    Ok(limit)
}

fn positive(value: f64, name: &str) -> Result<(), JobManagementError> {
    if !value.is_finite() || value <= 0.0 {
        return Err(JobManagementError(format!(
            "{name} must be finite and positive"
        )));
    }
    Ok(())
}

fn positive_integer(value: &Value, name: &str) -> Result<i64, JobManagementError> {
    let Some(integer) = value.as_i64() else {
        return Err(JobManagementError(format!(
            "{name} must be a positive integer"
        )));
    };
    if integer <= 0 {
        return Err(JobManagementError(format!(
            "{name} must be a positive integer"
        )));
    }
    Ok(integer)
}

fn json_number(value: f64) -> Value {
    if value.fract() == 0.0 && value >= i64::MIN as f64 && value < i64::MAX as f64 {
        Value::Number((value as i64).into())
    } else {
        Value::Number(serde_json::Number::from_f64(value).expect("validated finite number"))
    }
}

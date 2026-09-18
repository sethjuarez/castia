use crate::model::LifecycleOperationsRuntime;
use futures::{stream::FuturesUnordered, StreamExt};
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::time::{Duration, Instant};
use tokio::time::timeout;

use super::records::{
    canonical_json, content_hash, example_id, normalize_record, record_id, REFERENCE_ORIGINS,
    SCHEMA_VERSION,
};

const REVIEW_APPROVAL: &str =
    "approved must be literal true and every trace requires explicit review approval";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleOperationsRuntime;

#[async_trait::async_trait]
impl LifecycleOperationsRuntime for CastiaLifecycleOperationsRuntime {
    fn curate_dataset(
        &self,
        traces: &Value,
        redaction_version: &String,
        heldout_fraction: &f64,
        seed: &String,
    ) -> Value {
        let traces = traces
            .as_array()
            .unwrap_or_else(|| panic!("{}", REVIEW_APPROVAL));
        curate_dataset(traces, redaction_version, *heldout_fraction, seed)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn dataset_jsonl(&self, dataset: &Value, split: &String) -> String {
        dataset_jsonl(dataset, split).unwrap_or_else(|error| panic!("{}", error.0))
    }

    async fn evaluate_outcomes(
        &self,
        agent: &Value,
        dataset: &Value,
        evaluator: &Value,
        outcomes: &Value,
        split: &String,
        repeats: &i32,
        concurrency: &i32,
        timeout_seconds: &f64,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        Ok(evaluate_outcomes(
            agent,
            dataset,
            evaluator,
            outcomes,
            split,
            i64::from(*repeats),
            i64::from(*concurrency),
            *timeout_seconds,
        )
        .await
        .map_err(|error| Box::new(error) as Box<dyn std::error::Error + Send + Sync>)?)
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleOperationsError(String);

impl LifecycleOperationsError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl std::fmt::Display for LifecycleOperationsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LifecycleOperationsError {}

pub fn curate_dataset(
    traces: &[Value],
    redaction_version: &str,
    heldout_fraction: f64,
    seed: &str,
) -> Result<Value, LifecycleOperationsError> {
    curate_dataset_with_redactor(
        traces,
        |trace| Ok(trace.clone()),
        redaction_version,
        heldout_fraction,
        seed,
    )
}

pub fn curate_dataset_with_redactor<F>(
    traces: &[Value],
    redact: F,
    redaction_version: &str,
    heldout_fraction: f64,
    seed: &str,
) -> Result<Value, LifecycleOperationsError>
where
    F: Fn(&Value) -> Result<Value, LifecycleOperationsError>,
{
    number(heldout_fraction, "heldout_fraction")?;
    if !(heldout_fraction > 0.0 && heldout_fraction < 1.0) {
        return Err(LifecycleOperationsError(
            "heldout_fraction must be between zero and one".to_string(),
        ));
    }
    text(seed, "seed")?;
    text(redaction_version, "redaction_version")?;

    let mut rows: HashMap<String, Vec<Value>> = HashMap::new();
    let mut row_order = Vec::new();
    let mut parents: HashMap<String, String> = HashMap::new();
    let mut seen_traces: HashMap<String, String> = HashMap::new();
    let mut original_groups: HashMap<String, String> = HashMap::new();

    for raw in traces {
        reviewed(raw)?;
        let cleaned = redact(raw)?;
        reviewed(&cleaned)?;
        let raw_object = trace_object(raw)?;
        let cleaned_object = trace_object(&cleaned)?;
        let raw_model_output = raw_object.get("model_output").and_then(Value::as_str);
        let cleaned_reference = string_field(cleaned_object, "reference", "reference")?;
        if raw_model_output.is_some_and(|output| cleaned_reference.trim() == output.trim()) {
            return Err(LifecycleOperationsError(
                "redaction cannot turn model output into gold".to_string(),
            ));
        }
        let raw_origin = reference_origin(raw_object)?;
        let cleaned_origin = reference_origin(cleaned_object)?;
        if cleaned_origin != raw_origin {
            return Err(LifecycleOperationsError(
                "redaction cannot change the reference's authority".to_string(),
            ));
        }

        let fingerprint =
            content_hash(&json!({"input": string_field(cleaned_object, "input", "input")?}))
                .map_err(record_error)?;
        let raw_group = string_field(raw_object, "group", "group")?;
        let cleaned_group = string_field(cleaned_object, "group", "group")?;
        if let Some(previous) = original_groups.get(&raw_group).cloned() {
            union(&mut parents, &previous, &cleaned_group);
        } else {
            original_groups.insert(raw_group, cleaned_group.clone());
        }

        let trace_id = string_field(cleaned_object, "trace_id", "trace_id")?;
        if let Some(previous) = seen_traces.get(&trace_id) {
            if previous != &fingerprint {
                return Err(LifecycleOperationsError(
                    "trace provenance reused for conflicting inputs".to_string(),
                ));
            }
        }
        seen_traces.insert(trace_id, fingerprint.clone());

        if !rows.contains_key(&fingerprint) {
            row_order.push(fingerprint.clone());
        }
        let previous = rows.entry(fingerprint).or_default();
        if let Some(first) = previous.first() {
            let first_object = trace_object(first)?;
            if string_field(first_object, "reference", "reference")? != cleaned_reference {
                return Err(LifecycleOperationsError(
                    "duplicate input has conflicting references".to_string(),
                ));
            }
            union(
                &mut parents,
                &string_field(first_object, "group", "group")?,
                &cleaned_group,
            );
        }
        find(&mut parents, &cleaned_group);
        previous.push(cleaned);
    }

    let mut examples = Vec::new();
    for fingerprint in row_order {
        let group = &rows[&fingerprint];
        let first = trace_object(&group[0])?;
        let root_group = find(&mut parents, &string_field(first, "group", "group")?);
        examples.push(json!({
            "input": string_field(first, "input", "input")?,
            "reference": string_field(first, "reference", "reference")?,
            "group": content_hash(&json!({"group": root_group})).map_err(record_error)?,
            "provenance": group
                .iter()
                .map(|trace| Ok(Value::String(string_field(trace_object(trace)?, "trace_id", "trace_id")?)))
                .collect::<Result<Vec<_>, LifecycleOperationsError>>()?,
            "reviewers": group
                .iter()
                .map(|trace| Ok(Value::String(string_field(trace_object(trace)?, "reviewer", "reviewer")?)))
                .collect::<Result<Vec<_>, LifecycleOperationsError>>()?,
            "reference_origins": group
                .iter()
                .map(|trace| Ok(Value::String(reference_origin(trace_object(trace)?)?.to_string())))
                .collect::<Result<Vec<_>, LifecycleOperationsError>>()?,
        }));
    }

    let groups = examples
        .iter()
        .map(|example| string_value(example, "group"))
        .collect::<Result<BTreeSet<_>, _>>()?
        .into_iter()
        .map(|group| {
            let key = content_hash(&json!({"seed": seed, "group": group})).map_err(record_error)?;
            Ok((key, group))
        })
        .collect::<Result<Vec<_>, LifecycleOperationsError>>()?;
    let mut groups = groups;
    groups.sort_by(|left, right| left.0.cmp(&right.0));
    let groups = groups
        .into_iter()
        .map(|(_, group)| group)
        .collect::<Vec<_>>();
    if groups.len() < 2 {
        return Err(LifecycleOperationsError(
            "curation requires at least two independent groups".to_string(),
        ));
    }
    let heldout_count = ((groups.len() as f64) * heldout_fraction).ceil() as usize;
    let heldout_count = heldout_count.clamp(1, groups.len() - 1);
    let heldout_groups = groups
        .iter()
        .take(heldout_count)
        .cloned()
        .collect::<HashSet<_>>();

    let mut train_ids = Vec::new();
    let mut heldout_ids = Vec::new();
    for example in &examples {
        let id = example_id(example).map_err(record_error)?;
        if heldout_groups.contains(&string_value(example, "group")?) {
            heldout_ids.push(Value::String(id));
        } else {
            train_ids.push(Value::String(id));
        }
    }

    normalize_record(&json!({
        "schema_version": SCHEMA_VERSION,
        "kind": "dataset",
        "examples": examples,
        "train_ids": train_ids,
        "heldout_ids": heldout_ids,
        "seed": seed,
        "redaction_version": redaction_version,
    }))
    .map_err(record_error)
}

pub fn dataset_jsonl(dataset: &Value, split: &str) -> Result<String, LifecycleOperationsError> {
    if !matches!(split, "train" | "heldout") {
        return Err(LifecycleOperationsError(
            "split must be train or heldout".to_string(),
        ));
    }
    let dataset = normalize_record(dataset).map_err(record_error)?;
    if dataset.get("kind").and_then(Value::as_str) != Some("dataset") {
        return Err(LifecycleOperationsError(
            "dataset record is required".to_string(),
        ));
    }
    let ids = dataset
        .get(if split == "train" {
            "train_ids"
        } else {
            "heldout_ids"
        })
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))?
        .iter()
        .filter_map(Value::as_str)
        .collect::<HashSet<_>>();
    let examples = dataset
        .get("examples")
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))?;
    let mut out = String::new();
    for example in examples {
        let id = example_id(example).map_err(record_error)?;
        if ids.contains(id.as_str()) {
            let row = json!({
                "messages": [{"role": "user", "content": string_value(example, "input")?}],
                "reference": string_value(example, "reference")?,
                "example_id": id,
                "provenance": example.get("provenance").cloned().unwrap_or(Value::Array(Vec::new())),
                "reference_origins": example.get("reference_origins").cloned().unwrap_or(Value::Array(Vec::new())),
            });
            out.push_str(&canonical_json(&row).map_err(record_error)?);
            out.push('\n');
        }
    }
    Ok(out)
}

pub async fn evaluate_with_callback<F, Fut>(
    agent: &Value,
    dataset: &Value,
    evaluator: &Value,
    callback: F,
    split: &str,
    repeats: i64,
    concurrency: i64,
    timeout_seconds: f64,
) -> Result<Value, LifecycleOperationsError>
where
    F: Fn(Value, Value, i64) -> Fut + Send + Sync,
    Fut: Future<Output = Result<Value, LifecycleOperationsError>> + Send,
{
    let repeats = positive_integer(repeats, "repeats")?;
    let concurrency = positive_integer(concurrency, "concurrency")?;
    number(timeout_seconds, "timeout")?;
    if timeout_seconds <= 0.0 {
        return Err(LifecycleOperationsError(
            "timeout must be positive".to_string(),
        ));
    }
    if !matches!(split, "train" | "heldout") {
        return Err(LifecycleOperationsError(
            "split must be train or heldout".to_string(),
        ));
    }

    let agent = normalize_record(agent).map_err(record_error)?;
    if agent.get("kind").and_then(Value::as_str) != Some("agent") {
        return Err(LifecycleOperationsError(
            "evaluation needs agent and dataset snapshots".to_string(),
        ));
    }
    let dataset = normalize_record(dataset).map_err(record_error)?;
    if dataset.get("kind").and_then(Value::as_str) != Some("dataset") {
        return Err(LifecycleOperationsError(
            "evaluation needs agent and dataset snapshots".to_string(),
        ));
    }
    let evaluator = normalize_evaluator(evaluator)?;

    let ids = dataset
        .get(if split == "heldout" {
            "heldout_ids"
        } else {
            "train_ids"
        })
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))?
        .iter()
        .map(|id| {
            id.as_str()
                .map(str::to_string)
                .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let examples = dataset
        .get("examples")
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))?
        .iter()
        .map(|example| Ok((example_id(example).map_err(record_error)?, example.clone())))
        .collect::<Result<HashMap<_, _>, LifecycleOperationsError>>()?;
    let mut jobs = ids
        .iter()
        .flat_map(|id| (0..repeats).map(move |repetition| (id.clone(), repetition)))
        .collect::<Vec<_>>()
        .into_iter();
    let job_count = (ids.len() as i64).saturating_mul(repeats);
    let worker_count = concurrency.min(job_count).max(0) as usize;
    let duration = Duration::try_from_secs_f64(timeout_seconds).unwrap_or(Duration::MAX);
    let mut active: FuturesUnordered<Pin<Box<dyn Future<Output = Value> + Send + '_>>> =
        FuturesUnordered::new();
    for _ in 0..worker_count {
        if let Some((example_id, repetition)) = jobs.next() {
            active.push(Box::pin(evaluate_one(
                &callback, &agent, &examples, example_id, repetition, duration,
            )));
        }
    }

    let mut results = Vec::new();
    while let Some(result) = active.next().await {
        results.push(result);
        if let Some((example_id, repetition)) = jobs.next() {
            active.push(Box::pin(evaluate_one(
                &callback, &agent, &examples, example_id, repetition, duration,
            )));
        }
    }
    results.sort_by_key(|result| {
        (
            string_value(result, "example_id").unwrap_or_default(),
            result
                .get("repetition")
                .and_then(Value::as_i64)
                .unwrap_or_default(),
        )
    });
    normalize_record(&json!({
        "schema_version": SCHEMA_VERSION,
        "kind": "run",
        "agent_id": record_id(&agent).map_err(record_error)?,
        "dataset_id": record_id(&dataset).map_err(record_error)?,
        "evaluator": evaluator,
        "split": split,
        "expected_ids": ids,
        "repeats": repeats,
        "results": results,
    }))
    .map_err(record_error)
}

/// Run one evaluation callback and convert callback failures into safe evidence.
///
/// A timeout is failed evidence, not proof that a remote job was cancelled.
/// Callbacks that launch background service jobs must handle cancellation,
/// cancel or poll only their owned jobs, and persist terminal status or
/// cancellation evidence in their own cleanup. This runner has no remote job
/// ownership. Rust cancellation drops in-flight callback futures; callbacks
/// that require asynchronous cleanup should use their own explicit cancellation
/// protocol before dropping the evaluator future. Requires a Tokio runtime with
/// the time driver enabled.
async fn evaluate_one<F, Fut>(
    callback: &F,
    agent: &Value,
    examples: &HashMap<String, Value>,
    example_id: String,
    repetition: i64,
    duration: Duration,
) -> Value
where
    F: Fn(Value, Value, i64) -> Fut + Send + Sync,
    Fut: Future<Output = Result<Value, LifecycleOperationsError>> + Send,
{
    let Some(example) = examples.get(&example_id).cloned() else {
        return evaluation_result(
            example_id,
            repetition,
            Value::Object(Map::new()),
            Some("callback_error"),
        );
    };
    let start = Instant::now();
    match timeout(duration, callback(agent.clone(), example, repetition)).await {
        Err(_) => evaluation_result(
            example_id,
            repetition,
            Value::Object(Map::new()),
            Some("timeout"),
        ),
        Ok(Err(_)) => evaluation_result(
            example_id,
            repetition,
            Value::Object(Map::new()),
            Some("callback_error"),
        ),
        Ok(Ok(metrics)) => {
            let metrics = finalize_metrics(metrics, start.elapsed().as_secs_f64())
                .unwrap_or_else(|_| Value::Object(Map::new()));
            let error = if metrics.as_object().is_some_and(|object| !object.is_empty()) {
                None
            } else {
                Some("invalid_metrics")
            };
            evaluation_result(example_id, repetition, metrics, error)
        }
    }
}

pub async fn evaluate_outcomes(
    agent: &Value,
    dataset: &Value,
    evaluator: &Value,
    outcomes: &Value,
    split: &str,
    repeats: i64,
    concurrency: i64,
    timeout_seconds: f64,
) -> Result<Value, LifecycleOperationsError> {
    let outcomes = outcomes
        .as_array()
        .ok_or_else(|| LifecycleOperationsError("outcomes must be an array".to_string()))?
        .iter()
        .map(|outcome| {
            let object = trace_object(outcome)?;
            Ok((
                (
                    string_field(object, "example_id", "example_id")?,
                    int_field(object, "repetition", "repetition")?,
                ),
                outcome.clone(),
            ))
        })
        .collect::<Result<HashMap<_, _>, LifecycleOperationsError>>()?;
    evaluate_with_callback(
        agent,
        dataset,
        evaluator,
        move |_, example, repetition| {
            let outcomes = outcomes.clone();
            async move {
                let id = example_id(&example).map_err(record_error)?;
                let Some(outcome) = outcomes.get(&(id, repetition)) else {
                    return Err(LifecycleOperationsError("missing outcome".to_string()));
                };
                match outcome
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                {
                    "callback_error" => Err(LifecycleOperationsError("callback_error".to_string())),
                    "timeout" => {
                        tokio::time::sleep(
                            Duration::try_from_secs_f64(timeout_seconds * 2.0)
                                .unwrap_or(Duration::MAX),
                        )
                        .await;
                        Ok(Value::Object(Map::new()))
                    }
                    "" => Ok(outcome.get("metrics").cloned().unwrap_or(Value::Null)),
                    _ => Ok(outcome.get("metrics").cloned().unwrap_or(Value::Null)),
                }
            }
        },
        split,
        repeats,
        concurrency,
        timeout_seconds,
    )
    .await
}

fn normalize_evaluator(value: &Value) -> Result<Value, LifecycleOperationsError> {
    let object = value.as_object().ok_or_else(|| {
        LifecycleOperationsError("evaluation needs a versioned evaluator".to_string())
    })?;
    require_keys(object, &["name", "version", "configuration"])?;
    let name = string_field(object, "name", "evaluator name")?;
    let version = string_field(object, "version", "evaluator version")?;
    let configuration = object
        .get("configuration")
        .and_then(Value::as_object)
        .ok_or_else(|| LifecycleOperationsError("configuration is required".to_string()))?
        .clone();
    Ok(json!({"name": name, "version": version, "configuration": configuration}))
}

fn finalize_metrics(
    metrics: Value,
    latency_seconds: f64,
) -> Result<Value, LifecycleOperationsError> {
    let mut metrics = metrics
        .as_object()
        .ok_or_else(|| LifecycleOperationsError("callback must return metric mapping".to_string()))?
        .clone();
    if !metrics.contains_key("latency_seconds") {
        metrics.insert("latency_seconds".to_string(), finite_json(latency_seconds)?);
    }
    for (key, value) in &metrics {
        text(key, "metric")?;
        let Some(value) = value.as_f64() else {
            return Err(LifecycleOperationsError(format!(
                "{key} must be a finite number"
            )));
        };
        if !value.is_finite() {
            return Err(LifecycleOperationsError(format!(
                "{key} must be a finite number"
            )));
        }
        if matches!(key.as_str(), "latency_seconds" | "cost") && value < 0.0 {
            return Err(LifecycleOperationsError(format!("{key} must be >= 0")));
        }
    }
    Ok(Value::Object(metrics))
}

fn evaluation_result(
    example_id: String,
    repetition: i64,
    metrics: Value,
    error: Option<&str>,
) -> Value {
    json!({
        "example_id": example_id,
        "repetition": repetition,
        "metrics": metrics,
        "error": error,
    })
}

fn reviewed(trace: &Value) -> Result<(), LifecycleOperationsError> {
    let object = trace_object(trace)?;
    if object.get("approved") != Some(&Value::Bool(true)) {
        return Err(LifecycleOperationsError(REVIEW_APPROVAL.to_string()));
    }
    let origin = reference_origin(object)?;
    if !REFERENCE_ORIGINS.contains(&origin) {
        return Err(LifecycleOperationsError(
            "reference_origin must be one of: human, authoritative, deterministic; model-generated and unknown origins are not accepted".to_string(),
        ));
    }
    for name in ["trace_id", "input", "reference", "reviewer", "group"] {
        text(&string_field(object, name, name)?, name)?;
    }
    if object
        .get("model_output")
        .and_then(Value::as_str)
        .is_some_and(|output| {
            string_field(object, "reference", "reference")
                .is_ok_and(|reference| reference.trim() == output.trim())
        })
    {
        return Err(LifecycleOperationsError(
            "model output cannot be reused as gold".to_string(),
        ));
    }
    Ok(())
}

fn trace_object(trace: &Value) -> Result<&Map<String, Value>, LifecycleOperationsError> {
    trace
        .as_object()
        .ok_or_else(|| LifecycleOperationsError(REVIEW_APPROVAL.to_string()))
}

fn require_keys(
    object: &Map<String, Value>,
    keys: &[&str],
) -> Result<(), LifecycleOperationsError> {
    let allowed = keys.iter().copied().collect::<HashSet<_>>();
    if object.len() != keys.len() || object.keys().any(|key| !allowed.contains(key.as_str())) {
        return Err(LifecycleOperationsError(
            "unexpected evidence fields".to_string(),
        ));
    }
    for key in keys {
        if !object.contains_key(*key) {
            return Err(LifecycleOperationsError(
                "unexpected evidence fields".to_string(),
            ));
        }
    }
    Ok(())
}

fn reference_origin(object: &Map<String, Value>) -> Result<&str, LifecycleOperationsError> {
    match object.get("reference_origin") {
        None => Ok("human"),
        Some(Value::String(value)) => Ok(value),
        _ => Err(LifecycleOperationsError(
            "reference_origin must be one of: human, authoritative, deterministic; model-generated and unknown origins are not accepted".to_string(),
        )),
    }
}

fn find(parents: &mut HashMap<String, String>, group: &str) -> String {
    parents
        .entry(group.to_string())
        .or_insert_with(|| group.to_string());
    let mut current = group.to_string();
    while parents[&current] != current {
        current = parents[&current].clone();
    }
    current
}

fn union(parents: &mut HashMap<String, String>, a: &str, b: &str) {
    let mut roots = [find(parents, a), find(parents, b)];
    roots.sort();
    parents.insert(roots[1].clone(), roots[0].clone());
}

fn string_field(
    object: &Map<String, Value>,
    field: &str,
    label: &str,
) -> Result<String, LifecycleOperationsError> {
    let value = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| LifecycleOperationsError(format!("{label} must be nonempty text")))?;
    text(value, label)?;
    Ok(value.to_string())
}

fn string_value(value: &Value, field: &str) -> Result<String, LifecycleOperationsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleOperationsError("malformed evidence record".to_string()))?;
    string_field(object, field, field)
}

fn text(value: &str, label: &str) -> Result<(), LifecycleOperationsError> {
    if value.trim().is_empty() {
        return Err(LifecycleOperationsError(format!(
            "{label} must be nonempty text"
        )));
    }
    Ok(())
}

fn number(value: f64, label: &str) -> Result<(), LifecycleOperationsError> {
    if !value.is_finite() {
        return Err(LifecycleOperationsError(format!("{label} must be finite")));
    }
    Ok(())
}

fn positive_integer(value: i64, label: &str) -> Result<i64, LifecycleOperationsError> {
    if value < 1 {
        return Err(LifecycleOperationsError(format!(
            "{label} must be an integer >= 1"
        )));
    }
    Ok(value)
}

fn int_field(
    object: &Map<String, Value>,
    field: &str,
    label: &str,
) -> Result<i64, LifecycleOperationsError> {
    let Some(value) = object.get(field).and_then(Value::as_i64) else {
        return Err(LifecycleOperationsError(format!(
            "{label} must be an integer"
        )));
    };
    Ok(value)
}

fn finite_json(value: f64) -> Result<Value, LifecycleOperationsError> {
    if !value.is_finite() {
        return Err(LifecycleOperationsError(
            "metric must be a finite number".to_string(),
        ));
    }
    serde_json::Number::from_f64(value)
        .map(Value::Number)
        .ok_or_else(|| LifecycleOperationsError("metric must be a finite number".to_string()))
}

fn record_error(error: impl std::fmt::Display) -> LifecycleOperationsError {
    LifecycleOperationsError(error.to_string())
}

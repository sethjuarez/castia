use crate::model::LifecycleOperationsRuntime;
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashMap, HashSet};

use super::records::{
    canonical_json, content_hash, example_id, normalize_record, REFERENCE_ORIGINS, SCHEMA_VERSION,
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
}

#[derive(Debug, Clone)]
pub struct LifecycleOperationsError(String);

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

fn record_error(error: impl std::fmt::Display) -> LifecycleOperationsError {
    LifecycleOperationsError(error.to_string())
}

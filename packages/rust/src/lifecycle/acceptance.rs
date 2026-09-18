use crate::model::LifecycleAcceptanceRuntime;
use serde_json::{json, Map, Number, Value};
use std::collections::{HashMap, HashSet};

use super::records::{content_hash, normalize_record, record_id};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleAcceptanceRuntime;

#[async_trait::async_trait]
impl LifecycleAcceptanceRuntime for CastiaLifecycleAcceptanceRuntime {
    fn default_gate(&self) -> Value {
        default_gate()
    }

    fn compare_runs(&self, baseline: &Value, candidate: &Value, gate: &Value) -> Value {
        compare_runs(baseline, candidate, gate).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleAcceptanceError(String);

impl std::fmt::Display for LifecycleAcceptanceError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LifecycleAcceptanceError {}

pub fn default_gate() -> Value {
    normalize_gate(&Value::Null).expect("default gate is valid")
}

pub fn compare_runs(
    baseline: &Value,
    candidate: &Value,
    gate: &Value,
) -> Result<Value, LifecycleAcceptanceError> {
    let baseline = normalize_record(baseline).map_err(record_error)?;
    let candidate = normalize_record(candidate).map_err(record_error)?;
    let gate = normalize_gate(gate)?;
    let mut reasons = Vec::new();

    let baseline_split = string_field(&baseline, "split")?;
    let candidate_split = string_field(&candidate, "split")?;
    if bool_field(&gate, "require_heldout")?
        && (baseline_split != "heldout" || candidate_split != "heldout")
    {
        reasons.push("acceptance requires heldout evidence".to_string());
    }
    if string_field(&baseline, "dataset_id")? != string_field(&candidate, "dataset_id")?
        || evaluator_id(&baseline)? != evaluator_id(&candidate)?
        || baseline_split != candidate_split
    {
        reasons.push("dataset, evaluator, and split must match".to_string());
    }
    if string_array_field(&baseline, "expected_ids")?
        != string_array_field(&candidate, "expected_ids")?
        || int_field(&baseline, "repeats")? != int_field(&candidate, "repeats")?
    {
        reasons.push("expected coverage and repeats must match".to_string());
    }

    let mut required = string_array_field(&gate, "required_metrics")?
        .into_iter()
        .collect::<HashSet<_>>();
    if !gate.get("maximum_cost").unwrap_or(&Value::Null).is_null() {
        required.insert("cost".to_string());
    }
    if !gate
        .get("maximum_latency_seconds")
        .unwrap_or(&Value::Null)
        .is_null()
    {
        required.insert("latency_seconds".to_string());
    }

    let mut aggregates = Map::new();
    let mut per_example = Vec::new();
    for (label, run) in [("baseline", &baseline), ("candidate", &candidate)] {
        let expected_ids = string_array_field(run, "expected_ids")?;
        let repeats = int_field(run, "repeats")?;
        let expected = expected_ids
            .iter()
            .flat_map(|id| (0..repeats).map(move |repetition| (id.clone(), repetition)))
            .collect::<HashSet<_>>();
        let results = array_field(run, "results")?;
        let actual = results
            .iter()
            .map(|result| {
                Ok((
                    string_field(result, "example_id")?,
                    int_field(result, "repetition")?,
                ))
            })
            .collect::<Result<HashSet<_>, LifecycleAcceptanceError>>()?;
        let complete = actual == expected
            && results
                .iter()
                .all(|result| result.get("error").unwrap_or(&Value::Null).is_null());
        if !complete {
            reasons.push(format!("{label}: incomplete or failed coverage"));
        }

        let available = available_metrics(results);
        if !required.is_subset(&available) {
            reasons.push(format!("{label}: missing required metrics"));
        }

        let mut means = Map::new();
        if complete {
            let mut metrics = available.into_iter().collect::<Vec<_>>();
            metrics.sort();
            for metric in metrics {
                let mean = precise_sum(
                    results
                        .iter()
                        .map(|result| {
                            metric_value(result, &metric).map(|value| value / results.len() as f64)
                        })
                        .collect::<Result<Vec<_>, _>>()?,
                );
                if !mean.is_finite() {
                    reasons.push(format!("{label}: nonfinite aggregate"));
                } else {
                    means.insert(metric, finite_json(mean)?);
                }
            }
        }
        aggregates.insert(label.to_string(), Value::Object(means));

        let mut quality = HashMap::new();
        if complete
            && results.iter().all(|result| {
                result
                    .get("metrics")
                    .and_then(Value::as_object)
                    .is_some_and(|metrics| metrics.contains_key("quality"))
            })
        {
            for example_id in &expected_ids {
                let mean = precise_sum(
                    results
                        .iter()
                        .filter(|result| {
                            string_field(result, "example_id").ok().as_deref() == Some(example_id)
                        })
                        .map(|result| {
                            metric_value(result, "quality").map(|value| value / repeats as f64)
                        })
                        .collect::<Result<Vec<_>, _>>()?,
                );
                if mean.is_finite() {
                    quality.insert(example_id.clone(), mean);
                } else {
                    reasons.push(format!("{label}: nonfinite example aggregate"));
                }
            }
        }
        per_example.push(quality);
    }

    let mut regressions = per_example[0]
        .keys()
        .filter(|example_id| {
            per_example[1]
                .get(*example_id)
                .is_some_and(|candidate| candidate < &per_example[0][*example_id])
        })
        .cloned()
        .collect::<Vec<_>>();
    regressions.sort();

    let left = object_field(&Value::Object(aggregates.clone()), "baseline")?.clone();
    let right = object_field(&Value::Object(aggregates.clone()), "candidate")?.clone();
    let mut deltas = Map::new();
    let mut shared = left
        .keys()
        .filter(|metric| right.contains_key(*metric))
        .cloned()
        .collect::<Vec<_>>();
    shared.sort();
    for metric in shared {
        let delta = right[&metric].as_f64().unwrap_or_default()
            - left[&metric].as_f64().unwrap_or_default();
        if !delta.is_finite() {
            reasons.push("nonfinite comparison delta".to_string());
        } else {
            deltas.insert(metric, finite_json(delta)?);
        }
    }
    aggregates.insert("delta".to_string(), Value::Object(deltas));

    if let Some(candidate_quality) = right.get("quality").and_then(Value::as_f64) {
        if candidate_quality < float_field(&gate, "minimum_quality")? {
            reasons.push("candidate quality below minimum".to_string());
        }
        if let Some(baseline_quality) = left.get("quality").and_then(Value::as_f64) {
            if candidate_quality < baseline_quality - float_field(&gate, "maximum_quality_drop")? {
                reasons.push("aggregate quality regressed beyond tolerance".to_string());
            }
        }
    }
    if regressions.len() > int_field(&gate, "maximum_example_regressions")? as usize {
        reasons.push("per-example regression limit exceeded".to_string());
    }
    for (metric, field) in [
        ("latency_seconds", "maximum_latency_seconds"),
        ("cost", "maximum_cost"),
    ] {
        if let (Some(limit), Some(actual)) = (
            gate.get(field).and_then(Value::as_f64),
            right.get(metric).and_then(Value::as_f64),
        ) {
            if actual > limit {
                reasons.push(format!("candidate {metric} exceeds limit"));
            }
        }
    }

    normalize_record(&json!({
        "schema_version": 1,
        "kind": "decision",
        "baseline_run_id": record_id(&baseline).map_err(record_error)?,
        "candidate_run_id": record_id(&candidate).map_err(record_error)?,
        "baseline_agent_id": string_field(&baseline, "agent_id")?,
        "candidate_agent_id": string_field(&candidate, "agent_id")?,
        "accepted": reasons.is_empty(),
        "reasons": reasons,
        "aggregates": aggregates,
        "regressions": regressions,
        "gate": gate,
    }))
    .map_err(record_error)
}

pub fn normalize_gate(gate: &Value) -> Result<Value, LifecycleAcceptanceError> {
    let empty = Map::new();
    let object = if gate.is_null() {
        &empty
    } else {
        gate.as_object()
            .ok_or_else(|| LifecycleAcceptanceError("gate must be a JSON object".to_string()))?
    };
    let allowed = [
        "minimum_quality",
        "maximum_quality_drop",
        "maximum_example_regressions",
        "maximum_latency_seconds",
        "maximum_cost",
        "required_metrics",
        "require_heldout",
    ];
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(LifecycleAcceptanceError(format!(
            "unexpected gate field: {key}"
        )));
    }
    let minimum_quality = optional_number(object, "minimum_quality", json!(0), None)?;
    let maximum_quality_drop =
        optional_number(object, "maximum_quality_drop", json!(0), Some(0.0))?;
    let maximum_example_regressions = optional_int(object, "maximum_example_regressions", 0, 0)?;
    let maximum_latency_seconds = optional_nullable_float(object, "maximum_latency_seconds")?;
    let maximum_cost = optional_nullable_float(object, "maximum_cost")?;
    let require_heldout = object
        .get("require_heldout")
        .map(|value| {
            value.as_bool().ok_or_else(|| {
                LifecycleAcceptanceError("require_heldout must be a boolean".to_string())
            })
        })
        .transpose()?
        .unwrap_or(true);
    let mut required_metrics = match object.get("required_metrics") {
        None => vec!["quality".to_string()],
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| {
                item.as_str()
                    .ok_or_else(|| {
                        LifecycleAcceptanceError(
                            "required_metrics must contain strings".to_string(),
                        )
                    })
                    .and_then(|metric| {
                        text(metric, "required metric")?;
                        Ok(metric.to_string())
                    })
            })
            .collect::<Result<Vec<_>, _>>()?,
        _ => {
            return Err(LifecycleAcceptanceError(
                "required_metrics must be an array".to_string(),
            ))
        }
    };
    required_metrics.push("quality".to_string());
    required_metrics.sort();
    required_metrics.dedup();

    Ok(json!({
        "minimum_quality": minimum_quality,
        "maximum_quality_drop": maximum_quality_drop,
        "maximum_example_regressions": maximum_example_regressions,
        "maximum_latency_seconds": maximum_latency_seconds,
        "maximum_cost": maximum_cost,
        "required_metrics": required_metrics,
        "require_heldout": require_heldout,
    }))
}

fn evaluator_id(run: &Value) -> Result<String, LifecycleAcceptanceError> {
    content_hash(
        run.get("evaluator")
            .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))?,
    )
    .map_err(record_error)
}

fn available_metrics(results: &[Value]) -> HashSet<String> {
    let Some(first) = results.first() else {
        return HashSet::new();
    };
    let mut available = first
        .get("metrics")
        .and_then(Value::as_object)
        .map(|metrics| metrics.keys().cloned().collect::<HashSet<_>>())
        .unwrap_or_default();
    for result in results.iter().skip(1) {
        let metrics = result
            .get("metrics")
            .and_then(Value::as_object)
            .map(|metrics| metrics.keys().cloned().collect::<HashSet<_>>())
            .unwrap_or_default();
        available = available.intersection(&metrics).cloned().collect();
    }
    available
}

fn metric_value(result: &Value, metric: &str) -> Result<f64, LifecycleAcceptanceError> {
    result
        .get("metrics")
        .and_then(Value::as_object)
        .and_then(|metrics| metrics.get(metric))
        .and_then(Value::as_f64)
        .ok_or_else(|| LifecycleAcceptanceError(format!("{metric} must be finite")))
}

fn string_field(value: &Value, field: &str) -> Result<String, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn string_array_field(value: &Value, field: &str) -> Result<Vec<String>, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))?
        .iter()
        .map(|item| {
            item.as_str()
                .map(str::to_string)
                .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
        })
        .collect()
}

fn array_field<'a>(value: &'a Value, field: &str) -> Result<&'a [Value], LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn object_field<'a>(
    value: &'a Value,
    field: &str,
) -> Result<&'a Map<String, Value>, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_object)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn int_field(value: &Value, field: &str) -> Result<i64, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_i64)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn bool_field(value: &Value, field: &str) -> Result<bool, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_bool)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn float_field(value: &Value, field: &str) -> Result<f64, LifecycleAcceptanceError> {
    value
        .get(field)
        .and_then(Value::as_f64)
        .ok_or_else(|| LifecycleAcceptanceError("malformed evidence record".to_string()))
}

fn optional_number(
    object: &Map<String, Value>,
    field: &str,
    default: Value,
    minimum: Option<f64>,
) -> Result<Value, LifecycleAcceptanceError> {
    let value = object.get(field).cloned().unwrap_or(default);
    let Some(number) = value.as_f64() else {
        return Err(LifecycleAcceptanceError(format!("{field} must be finite")));
    };
    if !number.is_finite() {
        return Err(LifecycleAcceptanceError(format!("{field} must be finite")));
    }
    if minimum.is_some_and(|minimum| number < minimum) {
        return Err(LifecycleAcceptanceError(format!("{field} must be >= 0")));
    }
    Ok(value)
}

fn optional_nullable_float(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Value, LifecycleAcceptanceError> {
    let Some(value) = object.get(field) else {
        return Ok(Value::Null);
    };
    if value.is_null() {
        return Ok(Value::Null);
    }
    let number = value
        .as_f64()
        .ok_or_else(|| LifecycleAcceptanceError(format!("{field} must be finite")))?;
    if !number.is_finite() {
        return Err(LifecycleAcceptanceError(format!("{field} must be finite")));
    }
    if number < 0.0 {
        return Err(LifecycleAcceptanceError(format!("{field} must be >= 0")));
    }
    Ok(value.clone())
}

fn optional_int(
    object: &Map<String, Value>,
    field: &str,
    default: i64,
    minimum: i64,
) -> Result<i64, LifecycleAcceptanceError> {
    let value = object
        .get(field)
        .map(|value| {
            value.as_i64().ok_or_else(|| {
                LifecycleAcceptanceError(format!("{field} must be an integer >= {minimum}"))
            })
        })
        .transpose()?
        .unwrap_or(default);
    if value < minimum {
        return Err(LifecycleAcceptanceError(format!(
            "{field} must be an integer >= {minimum}"
        )));
    }
    Ok(value)
}

fn text(value: &str, label: &str) -> Result<(), LifecycleAcceptanceError> {
    if value.trim().is_empty() {
        return Err(LifecycleAcceptanceError(format!(
            "{label} must be nonempty text"
        )));
    }
    Ok(())
}

fn finite_json(value: f64) -> Result<Value, LifecycleAcceptanceError> {
    Number::from_f64(value)
        .map(Value::Number)
        .ok_or_else(|| LifecycleAcceptanceError("nonfinite comparison delta".to_string()))
}

fn precise_sum(values: Vec<f64>) -> f64 {
    let mut partials: Vec<f64> = Vec::new();
    for mut x in values {
        let mut retained = 0;
        for i in 0..partials.len() {
            let mut y = partials[i];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let hi = x + y;
            let yr = hi - x;
            let lo = y - yr;
            if lo != 0.0 {
                partials[retained] = lo;
                retained += 1;
            }
            x = hi;
        }
        partials.truncate(retained);
        partials.push(x);
    }
    let mut n = partials.len();
    if n == 0 {
        return 0.0;
    }
    n -= 1;
    let mut hi = partials[n];
    let mut lo = 0.0;
    while n > 0 {
        let x = hi;
        n -= 1;
        let y = partials[n];
        hi = x + y;
        let yr = hi - x;
        lo = y - yr;
        if lo != 0.0 {
            break;
        }
    }
    if n > 0 && ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0)) {
        let y = lo * 2.0;
        let x = hi + y;
        let yr = x - hi;
        if y == yr {
            hi = x;
        }
    }
    hi
}

fn record_error(error: impl std::fmt::Display) -> LifecycleAcceptanceError {
    LifecycleAcceptanceError(error.to_string())
}

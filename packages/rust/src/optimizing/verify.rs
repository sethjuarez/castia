//! Post-optimize checks, mirroring `castia.optimizing.verify`.
//!
//! Thresholds are pinned in `spec/conformance/optimization/verify.json`.
//! Object keys are walked in `serde_json` map order (sorted), so when several
//! fields in one row leak, the report order may differ from Python's.

use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashSet;
use std::sync::OnceLock;

pub const OPTIMIZE_LEAK_SHINGLE_WORDS: usize = 6;
pub const OPTIMIZE_LEAK_MIN_WORDS: usize = 3;
pub const OPTIMIZE_STALL_MARKERS: &[&str] = &["maximum consecutive stall"];
pub const OPTIMIZE_STOPPED_EARLY_LABEL: &str = "stopped early (no improvement)";

#[derive(Debug, Clone, PartialEq)]
pub struct OptimizeLeak {
    pub row: usize,
    pub field: String,
    pub matched: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OptimizeVerdict {
    pub status: &'static str,
    pub baseline_mean: Option<f64>,
    pub candidate_mean: Option<f64>,
    pub delta: Option<f64>,
    pub reasons: Vec<String>,
}

fn word_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[a-z0-9]+(?:['_.:/-][a-z0-9]+)*").expect("valid regex"))
}

fn words(text: &str) -> Vec<String> {
    let lower = text.to_lowercase();
    word_re()
        .find_iter(&lower)
        .map(|m| m.as_str().to_string())
        .collect()
}

fn row_strings(value: &Value, prefix: &str, out: &mut Vec<(String, String)>) {
    match value {
        Value::String(s) => {
            let field = if prefix.is_empty() { "value" } else { prefix };
            out.push((field.to_string(), s.clone()));
        }
        Value::Object(map) => {
            for (key, v) in map {
                let path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                row_strings(v, &path, out);
            }
        }
        Value::Array(items) => {
            for (i, v) in items.iter().enumerate() {
                row_strings(v, &format!("{prefix}[{i}]"), out);
            }
        }
        _ => {}
    }
}

fn shingle_list(words: &[String], size: usize) -> Vec<String> {
    if words.len() < size {
        return Vec::new();
    }
    (0..=words.len() - size)
        .map(|i| words[i..i + size].join(" "))
        .collect()
}

/// Training text that appears verbatim in the candidate but not the baseline.
pub fn optimize_training_leaks(
    candidate_text: &str,
    rows: &[Value],
    baseline_text: &str,
) -> Vec<OptimizeLeak> {
    let cand = words(candidate_text);
    let base = words(baseline_text);
    let cand_joined = format!(" {} ", cand.join(" "));
    let base_joined = format!(" {} ", base.join(" "));
    let cand_sh: HashSet<String> = shingle_list(&cand, OPTIMIZE_LEAK_SHINGLE_WORDS)
        .into_iter()
        .collect();
    let base_sh: HashSet<String> = shingle_list(&base, OPTIMIZE_LEAK_SHINGLE_WORDS)
        .into_iter()
        .collect();
    let mut leaks = Vec::new();
    for (row, value) in rows.iter().enumerate() {
        let mut fields = Vec::new();
        row_strings(value, "", &mut fields);
        for (field, text) in fields {
            let w = words(&text);
            if w.len() >= OPTIMIZE_LEAK_SHINGLE_WORDS {
                if let Some(hit) = shingle_list(&w, OPTIMIZE_LEAK_SHINGLE_WORDS)
                    .into_iter()
                    .find(|s| cand_sh.contains(s) && !base_sh.contains(s))
                {
                    leaks.push(OptimizeLeak {
                        row,
                        field,
                        matched: hit,
                    });
                }
            } else if w.len() >= OPTIMIZE_LEAK_MIN_WORDS {
                let needle = format!(" {} ", w.join(" "));
                if cand_joined.contains(&needle) && !base_joined.contains(&needle) {
                    leaks.push(OptimizeLeak {
                        row,
                        field,
                        matched: needle.trim().to_string(),
                    });
                }
            }
        }
    }
    leaks
}

fn query_key(row: &Value, key: &str) -> Option<String> {
    let text = row
        .as_object()?
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("");
    Some(words(text).join(" "))
}

/// Indexes of held-out rows whose `key` text also appears in training.
pub fn optimize_heldout_overlap(training: &[Value], heldout: &[Value], key: &str) -> Vec<usize> {
    let seen: HashSet<String> = training
        .iter()
        .filter_map(|r| query_key(r, key))
        .filter(|s| !s.is_empty())
        .collect();
    heldout
        .iter()
        .enumerate()
        .filter(|(_, r)| query_key(r, key).is_some_and(|q| seen.contains(&q)))
        .map(|(i, _)| i)
        .collect()
}

/// Per-row numeric values for `metric` (booleans count as 1/0).
pub fn optimize_metric_values(rows: &[Value], metric: &str) -> Vec<f64> {
    let mut values = Vec::new();
    for row in rows {
        let Some(map) = row.as_object() else { continue };
        let mut value = map.get(metric).filter(|v| !v.is_null());
        for nest in ["scores", "metrics", "results"] {
            if value.is_none() {
                value = map
                    .get(nest)
                    .and_then(Value::as_object)
                    .and_then(|m| m.get(metric))
                    .filter(|v| !v.is_null());
            }
        }
        match value {
            Some(Value::Bool(b)) => values.push(if *b { 1.0 } else { 0.0 }),
            Some(Value::Number(n)) => values.extend(n.as_f64()),
            _ => {}
        }
    }
    values
}

fn round4(x: f64) -> f64 {
    (x * 10_000.0).round() / 10_000.0
}

fn mean(values: &[f64]) -> Option<f64> {
    (!values.is_empty()).then(|| round4(values.iter().sum::<f64>() / values.len() as f64))
}

/// `pass` when the candidate holds or beats baseline on held-out data.
pub fn optimize_verify_verdict(
    baseline: &[f64],
    candidate: &[f64],
    min_delta: f64,
    leaks: usize,
    overlap: usize,
) -> OptimizeVerdict {
    let mut reasons = Vec::new();
    let base_mean = mean(baseline);
    let cand_mean = mean(candidate);
    let mut delta = None;
    match (base_mean, cand_mean) {
        (Some(b), Some(c)) => {
            let d = round4(c - b);
            delta = Some(d);
            if baseline.len() != candidate.len() {
                reasons.push(format!(
                    "row counts differ (baseline {}, candidate {})",
                    baseline.len(),
                    candidate.len()
                ));
            }
            if d < min_delta {
                reasons.push(format!("held-out delta {d:+.4} < required {min_delta:+.4}"));
            }
        }
        _ => reasons.push("held-out metric missing for baseline or candidate".to_string()),
    }
    if leaks > 0 {
        reasons.push(format!(
            "candidate copies training text ({leaks} match(es))"
        ));
    }
    if overlap > 0 {
        reasons.push(format!("{overlap} held-out row(s) also appear in training"));
    }
    let status = if reasons.is_empty() {
        "pass"
    } else if delta.is_none() && leaks == 0 && overlap == 0 {
        "incomplete"
    } else {
        "fail"
    };
    OptimizeVerdict {
        status,
        baseline_mean: base_mean,
        candidate_mean: cand_mean,
        delta,
        reasons,
    }
}

fn without(map: &Map<String, Value>, key: &str) -> Value {
    Value::Object(
        map.iter()
            .filter(|(k, _)| k.as_str() != key)
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect(),
    )
}

/// `OPTIMIZE_STOPPED_EARLY_LABEL` when a stall limit ended the job.
pub fn optimize_termination_label(status: &Value) -> Option<&'static str> {
    let map = status.as_object()?;
    let mut strings = Vec::new();
    row_strings(&without(map, "result"), "", &mut strings);
    if let Some(result) = map.get("result").and_then(Value::as_object) {
        row_strings(&without(result, "candidates"), "", &mut strings);
    }
    let text = strings
        .iter()
        .map(|(_, s)| s.to_lowercase())
        .collect::<Vec<_>>()
        .join(" ");
    OPTIMIZE_STALL_MARKERS
        .iter()
        .any(|m| text.contains(m))
        .then_some(OPTIMIZE_STOPPED_EARLY_LABEL)
}

fn function_tool(tool: &Map<String, Value>) -> Value {
    if let Some(f) = tool.get("function").filter(|f| f.is_object()) {
        return json!({"type": "function", "function": f});
    }
    let function: Map<String, Value> = ["name", "description", "parameters"]
        .iter()
        .filter_map(|k| tool.get(*k).map(|v| (k.to_string(), v.clone())))
        .collect();
    json!({"type": "function", "function": function})
}

/// Seed the optimizer request from an agent version's prompt definition.
pub fn optimize_seed_from_version(
    request: &Value,
    version: &Value,
    agent_version: &str,
) -> Result<(Value, Vec<String>), String> {
    let mut seeded = request.clone();
    let empty = Map::new();
    let definition = version
        .get("definition")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let mut notes = Vec::new();
    let mut updates = Map::new();
    if let Some(text) = definition.get("instructions").and_then(Value::as_str) {
        if !text.trim().is_empty() {
            updates.insert("system_prompt".into(), json!(text));
            notes.push("instructions".to_string());
        }
    }
    if let Some(model) = definition.get("model").and_then(Value::as_str) {
        if !model.is_empty() {
            updates.insert("model".into(), json!(model));
            notes.push("model".to_string());
        }
    }
    let tools: Vec<Value> = definition
        .get("tools")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_object)
                .filter(|t| t.get("type").and_then(Value::as_str) == Some("function"))
                .map(function_tool)
                .collect()
        })
        .unwrap_or_default();
    if !tools.is_empty() {
        notes.push(format!("{} function tool(s)", tools.len()));
        updates.insert("tools".into(), Value::Array(tools));
    }
    if notes.is_empty() {
        return Err(format!(
            "agent version {agent_version} has no prompt definition to seed from \
             (hosted agents: apply a candidate locally and use --candidate)"
        ));
    }
    let root = seeded
        .as_object_mut()
        .ok_or_else(|| "request must be an object".to_string())?;
    let opt = root
        .entry("options")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| "options must be an object".to_string())?
        .entry("optimization_config")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| "optimization_config must be an object".to_string())?;
    opt.extend(updates);
    root.entry("agent")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| "agent must be an object".to_string())?
        .insert("agent_version".into(), json!(agent_version));
    Ok((seeded, notes))
}

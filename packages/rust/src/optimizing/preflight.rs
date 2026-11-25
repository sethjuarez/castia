//! Offline `optimize run` preflight, mirroring `castia.optimizing.preflight`.
//!
//! Thresholds and field lists are pinned in
//! `spec/conformance/optimization/preflight.json`.

use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

pub const OPTIMIZE_PASSTHROUGH_FIELDS: &[&str] = &["query"];
/// Pass-through fields by evaluator registration type (Waypoint 25e0ffe5):
/// PROMPT keeps `ground_truth`; RUBRIC and unknown types keep only `query`.
pub const OPTIMIZE_PASSTHROUGH_BY_TYPE: &[(&str, &[&str])] = &[
    ("prompt", &["query", "ground_truth"]),
    ("rubric", &["query"]),
];
pub const OPTIMIZE_KNOWN_DROPPED_FIELDS: &[&str] = &["ground_truth", "retrieval_context"];
pub const OPTIMIZE_TURN_LEVEL_EVALUATOR_TYPES: &[&str] = &[
    "string_check",
    "text_similarity",
    "python",
    "code",
    "label_model",
    "score_model",
];
pub const OPTIMIZE_PILOT_MAX_ROWS: u64 = 20;
pub const OPTIMIZE_PILOT_MAX_CANDIDATES: u64 = 10;
pub const OPTIMIZE_APPROVAL_HOURS: f64 = 1.0;
pub const OPTIMIZE_SERVICE_CAP_HOURS: f64 = 6.0;
pub const OPTIMIZE_DEFAULT_ROW_SECONDS: f64 = 30.0;
pub const OPTIMIZE_ASSUMED_CANDIDATES: u64 = 10;

#[derive(Debug, Clone, PartialEq)]
pub struct OptimizeEstimate {
    pub rows: Option<u64>,
    pub candidates: u64,
    pub candidates_assumed: bool,
    pub row_seconds: f64,
    pub concurrency: u64,
    pub hours: Option<f64>,
    pub cost_usd: Option<f64>,
    pub needs_approval: bool,
    pub reasons: Vec<String>,
}

fn item_ref() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\{\{\s*item\.([A-Za-z0-9_]+)").expect("valid regex"))
}

fn collect_strings<'a>(value: &'a Value, out: &mut Vec<&'a str>) {
    match value {
        Value::String(s) => out.push(s),
        Value::Object(map) => map.values().for_each(|v| collect_strings(v, out)),
        Value::Array(items) => items.iter().for_each(|v| collect_strings(v, out)),
        _ => {}
    }
}

/// Every `{{ item.<field> }}` name in a nested evaluator definition.
pub fn optimize_item_references(value: &Value) -> Vec<String> {
    let mut strings = Vec::new();
    collect_strings(value, &mut strings);
    let mut found: Vec<String> = Vec::new();
    for text in strings {
        for cap in item_ref().captures_iter(text) {
            let name = cap[1].to_string();
            if !found.contains(&name) {
                found.push(name);
            }
        }
    }
    found
}

fn evaluator_name(entry: &Value) -> String {
    match entry {
        Value::String(s) => s.clone(),
        Value::Object(map) => map
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        _ => String::new(),
    }
}

fn evaluator_nodes<'a>(
    entry: &'a Value,
    name: &str,
    definitions: Option<&'a Value>,
) -> Vec<(String, &'a Value)> {
    let mut nodes = Vec::new();
    if entry.is_object() {
        let label = if name.is_empty() { "?" } else { name };
        nodes.push((label.to_string(), entry));
    }
    if let Some(def) = definitions.and_then(|d| d.get(name)) {
        if !def.is_null() {
            nodes.push((format!("{name} (definition)"), def));
        }
    }
    nodes
}

fn turn_kind(node: &Value) -> Option<String> {
    match node {
        Value::Object(map) => {
            for key in ["evaluation_level", "level"] {
                if map
                    .get(key)
                    .and_then(Value::as_str)
                    .is_some_and(|v| v.eq_ignore_ascii_case("turn"))
                {
                    return Some(format!("{key}: turn"));
                }
            }
            let kind = map
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_lowercase();
            if OPTIMIZE_TURN_LEVEL_EVALUATOR_TYPES.contains(&kind.as_str()) {
                return Some(format!("type {kind}"));
            }
            map.values().find_map(turn_kind)
        }
        Value::Array(items) => items.iter().find_map(turn_kind),
        _ => None,
    }
}

/// Flag turn-level evaluators and `evaluation_level: turn`.
pub fn optimize_evaluator_level_problems(
    evaluators: &[Value],
    options: &Value,
    definitions: Option<&Value>,
) -> Vec<String> {
    let mut problems = Vec::new();
    if options
        .get("evaluation_level")
        .and_then(Value::as_str)
        .is_some_and(|v| v.eq_ignore_ascii_case("turn"))
    {
        problems.push(
            "options.evaluation_level: 'turn' is rejected by the optimizer (400); \
             leave evaluation_level unset"
                .to_string(),
        );
    }
    for entry in evaluators {
        let name = evaluator_name(entry);
        for (label, node) in evaluator_nodes(entry, &name, definitions) {
            if let Some(kind) = turn_kind(node) {
                problems.push(format!(
                    "evaluator {label}: {kind} runs per turn and fails at optimizer \
                     run time; use a conversation-level evaluator"
                ));
                break;
            }
        }
    }
    problems
}

/// Registration type (`prompt`, `rubric`, ...) of an evaluator entry or definition.
pub fn optimize_evaluator_type(node: &Value) -> String {
    let Some(map) = node.as_object() else {
        return String::new();
    };
    for key in ["evaluator_type", "type"] {
        if let Some(kind) = map.get(key).and_then(Value::as_str) {
            if !kind.is_empty() {
                return kind.to_lowercase();
            }
        }
    }
    map.get("definition")
        .map(optimize_evaluator_type)
        .unwrap_or_default()
}

/// Check `item.*` references against pass-through fields and dataset rows.
///
/// With `passthrough = None` the allowed fields follow each evaluator's type.
pub fn optimize_item_field_problems(
    evaluators: &[Value],
    rows: Option<&[Value]>,
    passthrough: Option<&[&str]>,
    definitions: Option<&Value>,
) -> Vec<String> {
    let mut problems = Vec::new();
    for entry in evaluators {
        let name = evaluator_name(entry);
        let shown = if name.is_empty() { "?" } else { name.as_str() };
        let nodes = evaluator_nodes(entry, &name, definitions);
        let mut refs: Vec<String> = Vec::new();
        for (_, node) in &nodes {
            for r in optimize_item_references(node) {
                if !refs.contains(&r) {
                    refs.push(r);
                }
            }
        }
        let kind = nodes
            .iter()
            .map(|(_, n)| optimize_evaluator_type(n))
            .find(|k| !k.is_empty())
            .unwrap_or_default();
        let mut allowed: Vec<&str> = match passthrough {
            Some(fields) => fields.to_vec(),
            None => OPTIMIZE_PASSTHROUGH_BY_TYPE
                .iter()
                .find(|(t, _)| *t == kind)
                .map(|(_, f)| f.to_vec())
                .unwrap_or_else(|| OPTIMIZE_PASSTHROUGH_FIELDS.to_vec()),
        };
        allowed.sort_unstable();
        allowed.dedup();
        let scope = if kind.is_empty() {
            "this evaluator".to_string()
        } else {
            format!("{kind} evaluators")
        };
        for r in refs {
            if !allowed.contains(&r.as_str()) {
                let why = if OPTIMIZE_KNOWN_DROPPED_FIELDS.contains(&r.as_str()) {
                    format!("the optimizer drops it for {scope}")
                } else {
                    format!(
                        "the optimizer only passes {} to {scope}",
                        allowed
                            .iter()
                            .map(|f| format!("item.{f}"))
                            .collect::<Vec<_>>()
                            .join(", ")
                    )
                };
                problems.push(format!(
                    "evaluator {shown}: item.{r} scores against nothing because {why}; \
                     move what the grader needs into a pass-through field"
                ));
            } else if let Some(rows) = rows {
                let missing = rows.iter().filter(|row| row.get(&r).is_none()).count();
                if missing > 0 {
                    problems.push(format!(
                        "evaluator {shown}: item.{r} is missing from {missing}/{} dataset rows",
                        rows.len()
                    ));
                }
            }
        }
    }
    problems
}

fn round_to(value: f64, places: i32) -> f64 {
    let factor = 10f64.powi(places);
    (value * factor).round() / factor
}

/// Estimate wall-clock hours as rows x (candidates + baseline) x row time.
pub fn estimate_optimize(
    rows: Option<u64>,
    max_candidates: Option<u64>,
    row_seconds: f64,
    concurrency: Option<u64>,
    hourly_usd: Option<f64>,
) -> OptimizeEstimate {
    let assumed = max_candidates.is_none();
    let candidates = max_candidates.unwrap_or(OPTIMIZE_ASSUMED_CANDIDATES);
    let conc = concurrency.unwrap_or(1).max(1);
    let mut reasons = Vec::new();
    let mut hours = None;
    let mut cost = None;
    if let Some(rows) = rows {
        let h = round_to(
            rows as f64 * (candidates + 1) as f64 * row_seconds / conc as f64 / 3600.0,
            4,
        );
        hours = Some(h);
        cost = hourly_usd.map(|rate| round_to(h * rate, 2));
        if rows > OPTIMIZE_PILOT_MAX_ROWS {
            reasons.push(format!("{rows} rows > pilot {OPTIMIZE_PILOT_MAX_ROWS}"));
        }
        if h > OPTIMIZE_APPROVAL_HOURS {
            reasons.push(format!("~{h:.2} h > {OPTIMIZE_APPROVAL_HOURS} h"));
        }
    } else {
        reasons.push("row count unknown (registered dataset)".to_string());
    }
    if !assumed && candidates > OPTIMIZE_PILOT_MAX_CANDIDATES {
        reasons.push(format!(
            "{candidates} candidates > pilot {OPTIMIZE_PILOT_MAX_CANDIDATES}"
        ));
    }
    OptimizeEstimate {
        rows,
        candidates,
        candidates_assumed: assumed,
        row_seconds,
        concurrency: conc,
        hours,
        cost_usd: cost,
        needs_approval: !reasons.is_empty(),
        reasons,
    }
}

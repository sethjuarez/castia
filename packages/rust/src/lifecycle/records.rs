use crate::model::LifecycleRecordsRuntime;
use regex::Regex;
use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::sync::OnceLock;

pub const SCHEMA_VERSION: i64 = 1;
pub const REFERENCE_ORIGINS: [&str; 3] = ["human", "authoritative", "deterministic"];

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleRecordsRuntime;

#[async_trait::async_trait]
impl LifecycleRecordsRuntime for CastiaLifecycleRecordsRuntime {
    fn canonical_json(&self, value: &Value) -> String {
        canonical_json(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn check_public(&self, value: &Value) -> bool {
        check_public(value).unwrap_or_else(|error| panic!("{}", error.0));
        true
    }

    fn content_hash(&self, value: &Value) -> String {
        content_hash(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn safe_path(&self, path: &String) -> String {
        safe_path(path).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn normalize_record(&self, value: &Value) -> Value {
        normalize_record(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn record_id(&self, value: &Value) -> String {
        record_id(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn example_id(&self, value: &Value) -> String {
        example_id(value).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleRecordsError(String);

impl std::fmt::Display for LifecycleRecordsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LifecycleRecordsError {}

pub fn canonical_json(value: &Value) -> Result<String, LifecycleRecordsError> {
    let normalized = normalize_json(value)?;
    serde_json::to_string(&normalized).map_err(|error| LifecycleRecordsError(error.to_string()))
}

pub fn content_hash(value: &Value) -> Result<String, LifecycleRecordsError> {
    let canonical = canonical_json(value)?;
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn normalize_record(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleRecordsError("malformed evidence record".to_string()))?;
    require_schema(object)?;
    let kind = object
        .get("kind")
        .and_then(Value::as_str)
        .ok_or_else(|| LifecycleRecordsError("unknown evidence kind".to_string()))?;
    match kind {
        "agent" => normalize_agent(object),
        "dataset" => normalize_dataset(object),
        "run" => normalize_run(object),
        "candidate" => normalize_candidate(object),
        "decision" => normalize_decision(object),
        _ => Err(LifecycleRecordsError("unknown evidence kind".to_string())),
    }
}

pub fn record_id(value: &Value) -> Result<String, LifecycleRecordsError> {
    content_hash(&normalize_record(value)?)
}

pub fn example_id(value: &Value) -> Result<String, LifecycleRecordsError> {
    let input = value
        .as_object()
        .and_then(|object| object.get("input"))
        .and_then(Value::as_str)
        .ok_or_else(|| LifecycleRecordsError("input must be nonempty text".to_string()))?;
    text(input, "input")?;
    content_hash(&json_object([("input", Value::String(input.to_string()))]))
}

pub fn safe_path(value: &str) -> Result<String, LifecycleRecordsError> {
    if value.trim().is_empty() {
        return Err(LifecycleRecordsError(
            "relative path must be nonempty text".to_string(),
        ));
    }
    let normalized = value.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    let has_windows_drive = value.len() >= 2 && value.as_bytes()[1] == b':';
    let invalid = normalized.starts_with('/')
        || value.starts_with('\\')
        || has_windows_drive
        || normalized.contains(':')
        || normalized.contains('\0')
        || parts
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == "..")
        || parts
            .iter()
            .any(|part| part.ends_with(' ') || part.ends_with('.'))
        || parts.iter().any(|part| {
            let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
            matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                || (stem.len() == 4
                    && (stem.starts_with("COM") || stem.starts_with("LPT"))
                    && stem[3..].parse::<u8>().is_ok_and(|n| (1..=9).contains(&n)))
        });
    if invalid {
        return Err(path_error());
    }
    Ok(normalized)
}

pub fn check_public(value: &Value) -> Result<(), LifecycleRecordsError> {
    match value {
        Value::Object(object) => {
            for (key, item) in object {
                if secret_key(key) && !placeholder(item) {
                    return Err(LifecycleRecordsError(
                        "secret-bearing configuration key is not evidence".to_string(),
                    ));
                }
                check_public(item)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                check_public(item)?;
            }
        }
        Value::String(value) => {
            if secret_value().is_match(value) {
                return Err(LifecycleRecordsError(
                    "credential-shaped content is not evidence".to_string(),
                ));
            }
            let mut offset = 0;
            while offset < value.len() {
                let Some(captures) = assignment().captures_at(value, offset) else {
                    break;
                };
                let Some(matched) = captures.get(0) else {
                    break;
                };
                let key = captures.get(1).map(|m| m.as_str()).unwrap_or_default();
                let item = captures.get(2).map(|m| m.as_str()).unwrap_or_default();
                if secret_key(key) {
                    let literal = item
                        .trim()
                        .trim_end_matches('}')
                        .trim()
                        .trim_matches(['"', '\'']);
                    if !matches!(literal.to_ascii_lowercase().as_str(), "null" | "none" | "~")
                        && !placeholder(&Value::String(literal.to_string()))
                        && !placeholder(&Value::String(
                            item.trim().trim_matches(['"', '\'']).to_string(),
                        ))
                    {
                        return Err(LifecycleRecordsError(
                            "secret-bearing configuration assignment is not evidence".to_string(),
                        ));
                    }
                }
                offset = next_char_boundary(value, matched.start() + 1);
            }
        }
        _ => {}
    }
    Ok(())
}

fn normalize_json(value: &Value) -> Result<Value, LifecycleRecordsError> {
    match value {
        Value::Array(items) => Ok(Value::Array(
            items
                .iter()
                .map(normalize_json)
                .collect::<Result<Vec<_>, _>>()?,
        )),
        Value::Object(object) => {
            let mut normalized = Map::new();
            let mut keys: Vec<&String> = object.keys().collect();
            keys.sort();
            for key in keys {
                normalized.insert(key.clone(), normalize_json(&object[key])?);
            }
            Ok(Value::Object(normalized))
        }
        Value::Null | Value::Bool(_) | Value::String(_) | Value::Number(_) => Ok(value.clone()),
    }
}

fn normalize_agent(object: &Map<String, Value>) -> Result<Value, LifecycleRecordsError> {
    require_keys_with_optional(
        object,
        &[
            "schema_version",
            "kind",
            "source_files",
            "dependencies",
            "model",
            "instructions",
        ],
        &["tools"],
    )?;
    check_public(&Value::Object(object.clone()))?;
    let mut source_files = array(object, "source_files")?
        .iter()
        .map(normalize_file_digest)
        .collect::<Result<Vec<_>, _>>()?;
    source_files.sort_by_key(|value| string_field(value, "path").unwrap_or_default());
    let mut seen = std::collections::HashSet::new();
    for file in &source_files {
        let path = string_field(file, "path")?.to_lowercase();
        if !seen.insert(path) {
            return Err(LifecycleRecordsError("duplicate source paths".to_string()));
        }
    }
    let dependencies = object_field(object, "dependencies")?.clone();
    check_public(&Value::Object(dependencies.clone()))?;
    for (key, value) in &dependencies {
        text(key, "dependency")?;
        text(value.as_str().unwrap_or_default(), "dependency version")?;
    }
    let model = object_field(object, "model")?.clone();
    check_public(&Value::Object(model.clone()))?;
    if model.is_empty() {
        return Err(LifecycleRecordsError(
            "model configuration is required".to_string(),
        ));
    }
    let instructions = string_required(object, "instructions", "instructions")?;
    check_public(&Value::String(instructions.clone()))?;
    let default_tools = Value::Array(Vec::new());
    let tools = object
        .get("tools")
        .unwrap_or(&default_tools)
        .as_array()
        .ok_or_else(|| LifecycleRecordsError("tools must contain JSON objects".to_string()))?
        .clone();
    for tool in &tools {
        if !tool.is_object() {
            return Err(LifecycleRecordsError(
                "tools must contain JSON objects".to_string(),
            ));
        }
        check_public(tool)?;
    }
    Ok(json_object([
        ("schema_version", Value::from(SCHEMA_VERSION)),
        ("kind", Value::String("agent".to_string())),
        ("source_files", Value::Array(source_files)),
        ("dependencies", Value::Object(dependencies)),
        ("model", Value::Object(model)),
        ("instructions", Value::String(instructions)),
        ("tools", Value::Array(tools)),
    ]))
}

fn normalize_file_digest(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value.as_object().ok_or_else(|| {
        LifecycleRecordsError("source_files contains an invalid record".to_string())
    })?;
    require_keys(object, &["path", "sha256", "size"])?;
    let path = safe_path(string_required(object, "path", "relative path")?.as_str())?;
    digest(string_required(object, "sha256", "id")?.as_str(), "id")?;
    let size = int_required(object, "size", "file size", 0)?;
    Ok(json_object([
        ("path", Value::String(path)),
        ("sha256", object["sha256"].clone()),
        ("size", Value::from(size)),
    ]))
}

fn normalize_example(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleRecordsError("examples contains an invalid record".to_string()))?;
    require_keys(
        object,
        &[
            "input",
            "reference",
            "group",
            "provenance",
            "reviewers",
            "reference_origins",
        ],
    )?;
    let input = string_required(object, "input", "input")?;
    let reference = string_required(object, "reference", "reference")?;
    let group = string_required(object, "group", "group")?;
    for value in [&input, &reference, &group] {
        check_public(&Value::String(value.clone()))?;
    }
    let provenance = sorted_unique_text_array(object, "provenance")?;
    let reviewers = sorted_unique_text_array(object, "reviewers")?;
    let mut origins = string_array(object, "reference_origins")?;
    origins.sort();
    origins.dedup();
    if origins.is_empty()
        || origins
            .iter()
            .any(|origin| !REFERENCE_ORIGINS.contains(&origin.as_str()))
    {
        return Err(LifecycleRecordsError(
                        "reference_origin must be one of: human, authoritative, deterministic; model-generated and unknown origins are not accepted".to_string(),
                    ));
    }
    Ok(json_object([
        ("input", Value::String(input)),
        ("reference", Value::String(reference)),
        ("group", Value::String(group)),
        ("provenance", strings_value(provenance)),
        ("reviewers", strings_value(reviewers)),
        ("reference_origins", strings_value(origins)),
    ]))
}

fn normalize_dataset(object: &Map<String, Value>) -> Result<Value, LifecycleRecordsError> {
    require_keys(
        object,
        &[
            "schema_version",
            "kind",
            "examples",
            "train_ids",
            "heldout_ids",
            "seed",
            "redaction_version",
        ],
    )?;
    check_public(&Value::Object(object.clone()))?;
    let mut examples = array(object, "examples")?
        .iter()
        .map(normalize_example)
        .collect::<Result<Vec<_>, _>>()?;
    examples.sort_by_key(|value| example_id(value).unwrap_or_default());
    let ids = examples
        .iter()
        .map(example_id)
        .collect::<Result<Vec<_>, _>>()?;
    if examples.is_empty() || unique_len(&ids) != ids.len() {
        return Err(LifecycleRecordsError(
            "dataset must have unique nonempty examples".to_string(),
        ));
    }
    let train_ids = sorted_digest_array(object, "train_ids")?;
    let heldout_ids = sorted_digest_array(object, "heldout_ids")?;
    if train_ids.is_empty()
        || heldout_ids.is_empty()
        || unique_len(&train_ids) != train_ids.len()
        || unique_len(&heldout_ids) != heldout_ids.len()
    {
        return Err(LifecycleRecordsError(
            "both splits must be nonempty and unique".to_string(),
        ));
    }
    let train: std::collections::HashSet<_> = train_ids.iter().cloned().collect();
    let heldout: std::collections::HashSet<_> = heldout_ids.iter().cloned().collect();
    let all: std::collections::HashSet<_> = ids.iter().cloned().collect();
    if !train.is_disjoint(&heldout)
        || train
            .union(&heldout)
            .cloned()
            .collect::<std::collections::HashSet<_>>()
            != all
    {
        return Err(LifecycleRecordsError(
            "splits must partition the dataset without leakage".to_string(),
        ));
    }
    let train_groups = examples
        .iter()
        .filter(|e| train.contains(&example_id(e).unwrap_or_default()))
        .filter_map(|e| string_field(e, "group").ok())
        .collect::<std::collections::HashSet<_>>();
    let heldout_groups = examples
        .iter()
        .filter(|e| heldout.contains(&example_id(e).unwrap_or_default()))
        .filter_map(|e| string_field(e, "group").ok())
        .collect::<std::collections::HashSet<_>>();
    if !train_groups.is_disjoint(&heldout_groups) {
        return Err(LifecycleRecordsError(
            "a provenance group cannot cross splits".to_string(),
        ));
    }
    let seed = string_required(object, "seed", "seed")?;
    let redaction_version = string_required(object, "redaction_version", "redaction_version")?;
    Ok(json_object([
        ("schema_version", Value::from(SCHEMA_VERSION)),
        ("kind", Value::String("dataset".to_string())),
        ("examples", Value::Array(examples)),
        ("train_ids", strings_value(train_ids)),
        ("heldout_ids", strings_value(heldout_ids)),
        ("seed", Value::String(seed)),
        ("redaction_version", Value::String(redaction_version)),
    ]))
}

fn normalize_evaluator(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleRecordsError("a versioned evaluator is required".to_string()))?;
    require_keys(object, &["name", "version", "configuration"])?;
    let name = string_required(object, "name", "evaluator name")?;
    let version = string_required(object, "version", "evaluator version")?;
    let configuration = object_field(object, "configuration")?.clone();
    check_public(&Value::Object(configuration.clone()))?;
    Ok(json_object([
        ("name", Value::String(name)),
        ("version", Value::String(version)),
        ("configuration", Value::Object(configuration)),
    ]))
}

fn normalize_evaluation_result(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleRecordsError("results contains an invalid record".to_string()))?;
    require_keys_with_optional(object, &["example_id", "repetition", "metrics"], &["error"])?;
    let example_id = string_required(object, "example_id", "id")?;
    digest(&example_id, "id")?;
    let repetition = int_required(object, "repetition", "repetition", 0)?;
    let metrics = object_field(object, "metrics")?.clone();
    check_public(&Value::Object(metrics.clone()))?;
    for (key, value) in &metrics {
        text(key, "metric")?;
        number_value(
            value,
            key,
            matches!(key.as_str(), "latency_seconds" | "cost"),
        )?;
    }
    let error = object.get("error").cloned().unwrap_or(Value::Null);
    if !error.is_null() {
        let Some(error_text) = error.as_str() else {
            return Err(LifecycleRecordsError("unsupported error code".to_string()));
        };
        if !matches!(error_text, "timeout" | "callback_error" | "invalid_metrics") {
            return Err(LifecycleRecordsError("unsupported error code".to_string()));
        }
        if !metrics.is_empty() {
            return Err(LifecycleRecordsError(
                "failed evaluations cannot carry successful metrics".to_string(),
            ));
        }
    }
    Ok(json_object([
        ("example_id", Value::String(example_id)),
        ("repetition", Value::from(repetition)),
        ("metrics", Value::Object(metrics)),
        ("error", error),
    ]))
}

fn normalize_run(object: &Map<String, Value>) -> Result<Value, LifecycleRecordsError> {
    require_keys(
        object,
        &[
            "schema_version",
            "kind",
            "agent_id",
            "dataset_id",
            "evaluator",
            "split",
            "expected_ids",
            "repeats",
            "results",
        ],
    )?;
    check_public(&Value::Object(object.clone()))?;
    let agent_id = string_required(object, "agent_id", "agent_id")?;
    digest(&agent_id, "id")?;
    let dataset_id = string_required(object, "dataset_id", "dataset_id")?;
    digest(&dataset_id, "id")?;
    let evaluator = normalize_evaluator(object.get("evaluator").unwrap_or(&Value::Null))?;
    let split = string_required(object, "split", "split")?;
    if !matches!(split.as_str(), "train" | "heldout") {
        return Err(LifecycleRecordsError(
            "split must be train or heldout".to_string(),
        ));
    }
    let repeats = int_required(object, "repeats", "repeats", 1)?;
    let expected_ids = sorted_digest_array(object, "expected_ids")?;
    if expected_ids.is_empty() || unique_len(&expected_ids) != expected_ids.len() {
        return Err(LifecycleRecordsError(
            "expected coverage must be unique and nonempty".to_string(),
        ));
    }
    let mut results = array(object, "results")?
        .iter()
        .map(normalize_evaluation_result)
        .collect::<Result<Vec<_>, _>>()?;
    let mut keys = std::collections::HashSet::new();
    for result in &results {
        let id = string_field(result, "example_id")?;
        let repetition = result
            .get("repetition")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        if !expected_ids.contains(&id) || repetition >= repeats || !keys.insert((id, repetition)) {
            return Err(LifecycleRecordsError(
                "unexpected or duplicate evaluation result".to_string(),
            ));
        }
    }
    results.sort_by_key(|result| {
        (
            string_field(result, "example_id").unwrap_or_default(),
            result
                .get("repetition")
                .and_then(Value::as_i64)
                .unwrap_or_default(),
        )
    });
    Ok(json_object([
        ("schema_version", Value::from(SCHEMA_VERSION)),
        ("kind", Value::String("run".to_string())),
        ("agent_id", Value::String(agent_id)),
        ("dataset_id", Value::String(dataset_id)),
        ("evaluator", evaluator),
        ("split", Value::String(split)),
        ("expected_ids", strings_value(expected_ids)),
        ("repeats", Value::from(repeats)),
        ("results", Value::Array(results)),
    ]))
}

fn normalize_config_file(value: &Value) -> Result<Value, LifecycleRecordsError> {
    let object = value
        .as_object()
        .ok_or_else(|| LifecycleRecordsError("files contains an invalid record".to_string()))?;
    require_keys_with_optional(object, &["path", "content"], &["sha256"])?;
    let path = safe_path(string_required(object, "path", "relative path")?.as_str())?;
    let content = object
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(|| LifecycleRecordsError("config content must be UTF-8 text".to_string()))?
        .to_string();
    check_public(&Value::String(content.clone()))?;
    validate_config_content(&path, &content)?;
    let checksum = content_hash_bytes(content.as_bytes());
    let provided = object
        .get("sha256")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !provided.is_empty() && provided != checksum {
        return Err(LifecycleRecordsError(
            "configuration file digest mismatch".to_string(),
        ));
    }
    Ok(json_object([
        ("path", Value::String(path)),
        ("content", Value::String(content)),
        ("sha256", Value::String(checksum)),
    ]))
}

fn normalize_candidate(object: &Map<String, Value>) -> Result<Value, LifecycleRecordsError> {
    require_keys(
        object,
        &[
            "schema_version",
            "kind",
            "baseline_id",
            "agent_id",
            "files",
            "agent_snapshot",
        ],
    )?;
    check_public(&Value::Object(object.clone()))?;
    let baseline_id = string_required(object, "baseline_id", "baseline_id")?;
    digest(&baseline_id, "id")?;
    let agent_id = string_required(object, "agent_id", "agent_id")?;
    digest(&agent_id, "id")?;
    let agent_snapshot = normalize_record(object.get("agent_snapshot").unwrap_or(&Value::Null))?;
    if agent_snapshot.get("kind").and_then(Value::as_str) != Some("agent") {
        return Err(LifecycleRecordsError(
            "candidate requires its evaluated agent snapshot".to_string(),
        ));
    }
    if record_id(&agent_snapshot)? != agent_id {
        return Err(LifecycleRecordsError(
            "candidate agent identity does not match its evaluated snapshot".to_string(),
        ));
    }
    let mut files = array(object, "files")?
        .iter()
        .map(normalize_config_file)
        .collect::<Result<Vec<_>, _>>()?;
    if files.is_empty() {
        return Err(LifecycleRecordsError(
            "candidate needs unique explicit configuration files".to_string(),
        ));
    }
    let mut seen = std::collections::HashSet::new();
    for file in &files {
        if !seen.insert(string_field(file, "path")?.to_lowercase()) {
            return Err(LifecycleRecordsError(
                "candidate needs unique explicit configuration files".to_string(),
            ));
        }
    }
    let captured = agent_snapshot
        .get("source_files")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for file in &files {
        let path = string_field(file, "path")?;
        let content = string_field(file, "content")?;
        let Some(source) = captured
            .iter()
            .find(|source| string_field(source, "path").ok().as_deref() == Some(path.as_str()))
        else {
            return Err(LifecycleRecordsError(
                            "staged payload is not bound to the evaluated snapshot; capture files before evaluation".to_string(),
                        ));
        };
        if string_field(source, "sha256")? != string_field(file, "sha256")?
            || source.get("size").and_then(Value::as_i64).unwrap_or(-1)
                != content.as_bytes().len() as i64
        {
            return Err(LifecycleRecordsError(
                            "staged payload is not bound to the evaluated snapshot; capture files before evaluation".to_string(),
                        ));
        }
    }
    files.sort_by_key(|file| string_field(file, "path").unwrap_or_default());
    Ok(json_object([
        ("schema_version", Value::from(SCHEMA_VERSION)),
        ("kind", Value::String("candidate".to_string())),
        ("baseline_id", Value::String(baseline_id)),
        ("agent_id", Value::String(agent_id)),
        ("files", Value::Array(files)),
        ("agent_snapshot", agent_snapshot),
    ]))
}

fn normalize_decision(object: &Map<String, Value>) -> Result<Value, LifecycleRecordsError> {
    require_keys(
        object,
        &[
            "schema_version",
            "kind",
            "baseline_run_id",
            "candidate_run_id",
            "baseline_agent_id",
            "candidate_agent_id",
            "accepted",
            "reasons",
            "aggregates",
            "regressions",
            "gate",
        ],
    )?;
    check_public(&Value::Object(object.clone()))?;
    let mut out = Map::new();
    out.insert("schema_version".to_string(), Value::from(SCHEMA_VERSION));
    out.insert("kind".to_string(), Value::String("decision".to_string()));
    for field in [
        "baseline_run_id",
        "candidate_run_id",
        "baseline_agent_id",
        "candidate_agent_id",
    ] {
        let value = string_required(object, field, field)?;
        digest(&value, field)?;
        out.insert(field.to_string(), Value::String(value));
    }
    let accepted = object
        .get("accepted")
        .and_then(Value::as_bool)
        .ok_or_else(|| LifecycleRecordsError("accepted must be a boolean".to_string()))?;
    let reasons = string_array(object, "reasons")?;
    if accepted == !reasons.is_empty() {
        return Err(LifecycleRecordsError(
            "accepted decisions have no rejection reasons".to_string(),
        ));
    }
    for reason in &reasons {
        text(reason, "reason")?;
    }
    let mut regressions = sorted_digest_array(object, "regressions")?;
    regressions.sort();
    let aggregates = object_field(object, "aggregates")?.clone();
    check_public(&Value::Object(aggregates.clone()))?;
    let gate = object_field(object, "gate")?.clone();
    check_public(&Value::Object(gate.clone()))?;
    out.insert("accepted".to_string(), Value::Bool(accepted));
    out.insert("reasons".to_string(), strings_value(reasons));
    out.insert("aggregates".to_string(), Value::Object(aggregates));
    out.insert("regressions".to_string(), strings_value(regressions));
    out.insert("gate".to_string(), Value::Object(gate));
    Ok(Value::Object(out))
}

fn require_schema(object: &Map<String, Value>) -> Result<(), LifecycleRecordsError> {
    match object.get("schema_version") {
        None => Err(LifecycleRecordsError(
            "schema_version is required".to_string(),
        )),
        Some(Value::Number(value)) if value.as_i64() == Some(SCHEMA_VERSION) => Ok(()),
        _ => Err(LifecycleRecordsError(
            "unsupported evidence schema version".to_string(),
        )),
    }
}

fn require_keys(object: &Map<String, Value>, keys: &[&str]) -> Result<(), LifecycleRecordsError> {
    let allowed: std::collections::HashSet<_> = keys.iter().copied().collect();
    if object.keys().any(|key| !allowed.contains(key.as_str()))
        || keys.iter().any(|key| !object.contains_key(*key))
    {
        return Err(LifecycleRecordsError(
            "malformed evidence record".to_string(),
        ));
    }
    Ok(())
}

fn require_keys_with_optional(
    object: &Map<String, Value>,
    required: &[&str],
    optional: &[&str],
) -> Result<(), LifecycleRecordsError> {
    let allowed: std::collections::HashSet<_> =
        required.iter().chain(optional.iter()).copied().collect();
    if object.keys().any(|key| !allowed.contains(key.as_str()))
        || required.iter().any(|key| !object.contains_key(*key))
    {
        return Err(LifecycleRecordsError(
            "malformed evidence record".to_string(),
        ));
    }
    Ok(())
}

fn validate_config_content(path: &str, content: &str) -> Result<(), LifecycleRecordsError> {
    let suffix = path
        .rsplit_once('.')
        .map(|(_, suffix)| suffix.to_ascii_lowercase())
        .unwrap_or_default();
    match suffix.as_str() {
        "json" => {
            reject_json_duplicate_keys(content)?;
            let parsed: Value = serde_json::from_str(content)
                .map_err(|error| LifecycleRecordsError(error.to_string()))?;
            check_public(&parsed)?;
            canonical_json(&parsed)?;
        }
        "yaml" | "yml" => {
            if content.contains("!!") {
                return Err(LifecycleRecordsError(
                    "invalid safe YAML configuration".to_string(),
                ));
            }
            let parsed: Value = serde_yaml::from_str(content).map_err(|_| {
                LifecycleRecordsError("invalid safe YAML configuration".to_string())
            })?;
            check_public(&parsed)?;
            canonical_json(&parsed)?;
        }
        "toml" => {
            let parsed: toml::Value = toml::from_str(content)
                .map_err(|error| LifecycleRecordsError(error.to_string()))?;
            let parsed = serde_json::to_value(parsed)
                .map_err(|error| LifecycleRecordsError(error.to_string()))?;
            check_public(&parsed)?;
            canonical_json(&parsed)?;
        }
        "md" | "txt" => {}
        _ => {
            return Err(LifecycleRecordsError(
                "unsupported config format; use JSON, YAML, TOML, Markdown, or text".to_string(),
            ));
        }
    }
    Ok(())
}

pub(crate) fn reject_json_duplicate_keys(content: &str) -> Result<(), LifecycleRecordsError> {
    let mut deserializer = serde_json::Deserializer::from_str(content);
    NoDuplicateKeys
        .deserialize(&mut deserializer)
        .map_err(|error| LifecycleRecordsError(error.to_string()))
}

struct NoDuplicateKeys;

impl<'de> DeserializeSeed<'de> for NoDuplicateKeys {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<(), D::Error>
    where
        D: de::Deserializer<'de>,
    {
        deserializer.deserialize_any(NoDuplicateKeysVisitor)
    }
}

struct NoDuplicateKeysVisitor;

impl<'de> Visitor<'de> for NoDuplicateKeysVisitor {
    type Value = ();

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("JSON value")
    }

    fn visit_map<A>(self, mut access: A) -> Result<(), A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = HashSet::new();
        while let Some(key) = access.next_key::<String>()? {
            if !keys.insert(key) {
                return Err(de::Error::custom("duplicate JSON key"));
            }
            access.next_value_seed(NoDuplicateKeys)?;
        }
        Ok(())
    }

    fn visit_seq<A>(self, mut access: A) -> Result<(), A::Error>
    where
        A: SeqAccess<'de>,
    {
        while access.next_element_seed(NoDuplicateKeys)?.is_some() {}
        Ok(())
    }

    fn visit_bool<E>(self, _: bool) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_i64<E>(self, _: i64) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_u64<E>(self, _: u64) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_f64<E>(self, _: f64) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_str<E>(self, _: &str) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_string<E>(self, _: String) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_none<E>(self) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<(), E>
    where
        E: de::Error,
    {
        Ok(())
    }
}

fn text(value: &str, label: &str) -> Result<(), LifecycleRecordsError> {
    if value.trim().is_empty() {
        return Err(LifecycleRecordsError(format!(
            "{label} must be nonempty text"
        )));
    }
    Ok(())
}

fn digest(value: &str, label: &str) -> Result<(), LifecycleRecordsError> {
    if !digest_regex().is_match(value) {
        return Err(LifecycleRecordsError(format!(
            "{label} must be a lowercase SHA-256 digest"
        )));
    }
    Ok(())
}

fn int_required(
    object: &Map<String, Value>,
    field: &str,
    label: &str,
    minimum: i64,
) -> Result<i64, LifecycleRecordsError> {
    let Some(value) = object.get(field).and_then(Value::as_i64) else {
        return Err(LifecycleRecordsError(format!(
            "{label} must be an integer >= {minimum}"
        )));
    };
    if value < minimum {
        return Err(LifecycleRecordsError(format!(
            "{label} must be an integer >= {minimum}"
        )));
    }
    Ok(value)
}

fn number_value(
    value: &Value,
    label: &str,
    nonnegative: bool,
) -> Result<(), LifecycleRecordsError> {
    let Some(number) = value.as_f64() else {
        return Err(LifecycleRecordsError(format!("{label} must be finite")));
    };
    if !number.is_finite() {
        return Err(LifecycleRecordsError(format!("{label} must be finite")));
    }
    if nonnegative && number < 0.0 {
        return Err(LifecycleRecordsError(format!("{label} must be >= 0")));
    }
    Ok(())
}

fn string_required(
    object: &Map<String, Value>,
    field: &str,
    label: &str,
) -> Result<String, LifecycleRecordsError> {
    let value = object
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| LifecycleRecordsError(format!("{label} must be nonempty text")))?;
    text(value, label)?;
    Ok(value.to_string())
}

fn string_field(value: &Value, field: &str) -> Result<String, LifecycleRecordsError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| LifecycleRecordsError("malformed evidence record".to_string()))
}

fn object_field<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a Map<String, Value>, LifecycleRecordsError> {
    object
        .get(field)
        .and_then(Value::as_object)
        .ok_or_else(|| LifecycleRecordsError(format!("{field} must be a JSON object")))
}

fn array<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a Vec<Value>, LifecycleRecordsError> {
    object
        .get(field)
        .and_then(Value::as_array)
        .ok_or_else(|| LifecycleRecordsError(format!("{field} must be an array")))
}

fn string_array(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Vec<String>, LifecycleRecordsError> {
    array(object, field)?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .ok_or_else(|| LifecycleRecordsError(format!("{field} must contain strings")))
        })
        .collect()
}

fn sorted_unique_text_array(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Vec<String>, LifecycleRecordsError> {
    let mut values = string_array(object, field)?;
    values.sort();
    values.dedup();
    if values.is_empty() {
        return Err(LifecycleRecordsError(format!("{field} is required")));
    }
    for value in &values {
        text(value, field)?;
        check_public(&Value::String(value.clone()))?;
    }
    Ok(values)
}

fn sorted_digest_array(
    object: &Map<String, Value>,
    field: &str,
) -> Result<Vec<String>, LifecycleRecordsError> {
    let mut values = string_array(object, field)?;
    values.sort();
    for value in &values {
        digest(value, "regression example")?;
    }
    Ok(values)
}

fn unique_len(values: &[String]) -> usize {
    values
        .iter()
        .collect::<std::collections::HashSet<_>>()
        .len()
}

fn strings_value(values: Vec<String>) -> Value {
    Value::Array(values.into_iter().map(Value::String).collect())
}

fn json_object<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    Value::Object(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

fn content_hash_bytes(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn path_error() -> LifecycleRecordsError {
    LifecycleRecordsError("path must be a normalized relative file path".to_string())
}

fn secret_key(key: &str) -> bool {
    let separated = camel_boundary().replace_all(key, "${1}_${2}");
    let normalized: String = key
        .to_ascii_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    secret_key_regex().is_match(&separated.replace(['.', ' '], "_"))
        || matches!(
            normalized.as_str(),
            "apikey"
                | "accesstoken"
                | "clientsecret"
                | "connectionstring"
                | "accountkey"
                | "sharedaccesssignature"
                | "credentials"
        )
}

fn next_char_boundary(value: &str, start: usize) -> usize {
    let mut offset = start.min(value.len());
    while offset < value.len() && !value.is_char_boundary(offset) {
        offset += 1;
    }
    offset
}

fn placeholder(value: &Value) -> bool {
    matches!(value, Value::Null)
        || value
            .as_str()
            .is_some_and(|value| value.is_empty() || placeholder_regex().is_match(value))
}

fn camel_boundary() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"([a-z0-9])([A-Z])").unwrap())
}

fn digest_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^[0-9a-f]{64}$").unwrap())
}

fn secret_key_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)(^|[_-])(password|passwd|secret|token|api[_-]?key|authorization|credential)($|[_-])",
        )
        .unwrap()
    })
}

fn secret_value() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)-----BEGIN .*PRIVATE KEY-----|Bearer\s+\S+|(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})|https?://[^/\s:@]+:[^/\s@]+@",
        )
        .unwrap()
    })
}

fn assignment() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r#"(?im)(?:^|[,{;#\n])\s*["']?([A-Za-z][A-Za-z0-9_. -]*?)["']?\s*[:=]\s*([^\n,]+)"#,
        )
        .unwrap()
    })
}

fn placeholder_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^(?:\$\{[A-Z_][A-Z0-9_]*\}|<redacted>|\[redacted\])$").unwrap()
    })
}

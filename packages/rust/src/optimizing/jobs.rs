//! Pure Foundry Agent Optimizer helpers for the GA `agent_optimization_jobs`
//! contract (`api-version=v1`, no `Foundry-Features` preview header).

use crate::model::OptimizerJobsRuntime;
use chrono::{DateTime, FixedOffset, NaiveDate, NaiveDateTime, Timelike};
use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

pub const API_VERSION: &str = "v1";
/// API version of the agentserver-optimization candidate config resolver.
pub const RESOLVER_API_VERSION: &str = "2025-11-15-preview";

const TERMINAL_STATUSES: [&str; 4] = ["succeeded", "failed", "cancelled", "skipped"];
const TARGET_ATTRIBUTES: [&str; 4] = ["instructions", "model", "skills", "tools"];
const GROUND_TRUTH_ALIASES: [&str; 3] = ["ground_truth", "answer", "expected"];
const UNSUPPORTED_OPTIONS: [(&str, &str, &str); 2] = [
    (
        "max_stalls",
        "maxStalls",
        "the GA optimizer has no stall budget; drop it",
    ),
    (
        "evaluation_level",
        "evaluationLevel",
        "the GA optimizer evaluates conversations only; drop it and use \
         conversation-level evaluators",
    ),
];
const OPTIMIZATION_CONFIG_KEYS: [&str; 6] = [
    "model",
    "model_search_space",
    "skills",
    "system_prompt",
    "target_attributes",
    "tools",
];
const TRACE_KEYS: [&str; 4] = [
    "agent_name",
    "resource_id",
    "training_split_ratio",
    "window",
];
const URL_SAFE: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

#[derive(Debug, Clone)]
pub struct OptimizerJobsError(String);

impl fmt::Display for OptimizerJobsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl Error for OptimizerJobsError {}

fn error(message: impl Into<String>) -> OptimizerJobsError {
    OptimizerJobsError(message.into())
}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaOptimizerJobsRuntime;

#[async_trait::async_trait]
impl OptimizerJobsRuntime for CastiaOptimizerJobsRuntime {
    fn best_optimizer_candidate_id(&self, status: &Value) -> Option<String> {
        best_optimizer_candidate_id(status)
    }

    fn optimizer_candidate_apply_plan(&self, candidate_id: &String, config: &Value) -> Value {
        optimizer_candidate_apply_plan(candidate_id, config)
    }

    fn optimizer_job_id(&self, payload: &Value) -> Option<String> {
        optimizer_job_id(payload)
    }

    fn optimizer_tool_names(&self, tools: &Value) -> Vec<String> {
        optimizer_tool_names(tools)
    }

    fn optimizer_request(
        &self,
        eval_config: &Value,
        baseline: &Value,
        dataset_items: &Value,
        validation_items: &Value,
        agent_name: &Option<String>,
        agent_version: &Option<String>,
        eval_model: &Option<String>,
        optimize_model: &Option<String>,
        max_candidates: &Option<i32>,
    ) -> Value {
        optimizer_request(
            eval_config,
            baseline,
            dataset_items,
            validation_items,
            agent_name.as_deref(),
            agent_version.as_deref(),
            eval_model.as_deref(),
            optimize_model.as_deref(),
            *max_candidates,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn optimizer_rest_request(
        &self,
        project_endpoint: &String,
        action: &String,
        job_id: &Option<String>,
        candidate_id: &Option<String>,
        body: &Value,
    ) -> Value {
        optimizer_rest_request(
            project_endpoint,
            action,
            job_id.as_deref(),
            candidate_id.as_deref(),
            body,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn terminal_optimizer_status(&self, status: &Option<String>) -> bool {
        terminal_optimizer_status(status.as_deref())
    }
}

pub fn optimizer_tool_names(tools: &Value) -> Vec<String> {
    tools
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|tool| {
            tool.get("function")
                .and_then(|function| function.get("name"))
                .or_else(|| tool.get("name"))
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
        })
        .collect()
}

/// Build a GA `agent_optimization_jobs` request.
///
/// `baseline` carries the already-loaded baseline (`model`, `instructions`,
/// `tools`); `dataset_items` and `validation_items` carry the already-loaded
/// JSONL rows for inline datasets. A top-level `traces` block switches the job
/// to skill optimization over Application Insights agent traces.
#[allow(clippy::too_many_arguments)]
pub fn optimizer_request(
    eval_config: &Value,
    baseline: &Value,
    dataset_items: &Value,
    validation_items: &Value,
    agent_name: Option<&str>,
    agent_version: Option<&str>,
    eval_model: Option<&str>,
    optimize_model: Option<&str>,
    max_candidates: Option<i32>,
) -> Result<Value, OptimizerJobsError> {
    let agent = object_field(eval_config, &["agent"]);
    let options = object_field(eval_config, &["options"]);
    let opt_config = object_field(
        &Value::Object(options.clone()),
        &["optimization_config", "optimizationConfig"],
    );

    for (key, alias, reason) in UNSUPPORTED_OPTIONS {
        if options.contains_key(key) || options.contains_key(alias) {
            return Err(error(format!("options.{key} is not supported: {reason}")));
        }
    }
    let unknown = sorted_keys_outside(&opt_config, &OPTIMIZATION_CONFIG_KEYS);
    if !unknown.is_empty() {
        return Err(error(format!(
            "options.optimization_config has unsupported keys: {} (supported: {})",
            unknown.join(", "),
            OPTIMIZATION_CONFIG_KEYS.join(", ")
        )));
    }

    let resolved_agent = override_or(agent_name, lookup(&agent, &["name"]))
        .ok_or_else(|| error("agent.name is required (or pass --agent)"))?;
    let resolved_eval = override_or(eval_model, lookup(&options, &["eval_model", "evalModel"]))
        .ok_or_else(|| error("options.eval_model is required (or pass --eval-model)"))?;
    let resolved_optimize = override_or(
        optimize_model,
        lookup(&options, &["optimization_model", "optimizationModel"]),
    )
    .ok_or_else(|| error("options.optimization_model is required (or pass --optimize-model)"))?;
    let max_error = || error("max_candidates must be >= 1");
    let resolved_max = match max_candidates {
        Some(value) => Some(i64::from(value)),
        None => match lookup(&options, &["max_candidates", "maxCandidates"]) {
            None => None,
            Some(Value::Bool(_)) => return Err(max_error()),
            Some(value) => Some(int_like(value).ok_or_else(max_error)?),
        },
    };
    if matches!(resolved_max, Some(value) if value < 1) {
        return Err(max_error());
    }

    let mut target = Map::new();
    target.insert("type".to_string(), json!("foundry_agent"));
    target.insert("name".to_string(), Value::String(resolved_agent.clone()));
    if let Some(version) = agent_version
        .filter(|value| !value.is_empty())
        .map(|value| Value::String(value.to_string()))
        .or_else(|| lookup(&agent, &["version"]).cloned())
        .filter(|value| value.as_str() != Some(""))
    {
        target.insert("version".to_string(), Value::String(py_str(&version)));
    }

    let mut request = Map::new();
    if let Some(name) = lookup(&options, &["display_name", "displayName"]).filter(|v| truthy(v)) {
        request.insert("display_name".to_string(), Value::String(py_str(name)));
    }
    request.insert("target_configuration".to_string(), Value::Object(target));
    request.insert(
        "optimization_model_configuration".to_string(),
        json!({"model": resolved_optimize}),
    );

    let evaluators = eval_config.get("evaluators").unwrap_or(&Value::Null);
    let validation = evaluation_set(
        lookup_value(eval_config, &["validation_dataset", "validationDataset"]),
        None,
        validation_items,
        "validation_dataset",
    )?;
    let concurrency = lookup(
        &options,
        &["max_concurrent_agent_runs", "maxConcurrentAgentRuns"],
    );
    let dataset_file = lookup_value(eval_config, &["dataset_file", "datasetFile"]);

    if let Some(traces) = lookup_value(eval_config, &["traces"]) {
        let conflicts: Vec<&str> = [
            ("dataset", lookup_value(eval_config, &["dataset"]).is_some()),
            ("dataset_file", dataset_file.is_some()),
            ("options.max_candidates", resolved_max.is_some()),
            ("options.optimization_config", !opt_config.is_empty()),
        ]
        .into_iter()
        .filter_map(|(name, present)| present.then_some(name))
        .collect();
        if !conflicts.is_empty() {
            return Err(error(format!(
                "traces: runs skill optimization from agent traces and cannot be combined with {}",
                conflicts.join(", ")
            )));
        }
        let mut evaluation = Map::new();
        evaluation.insert(
            "evaluation_model".to_string(),
            json!({"model": resolved_eval}),
        );
        if truthy(evaluators) {
            evaluation.insert(
                "evaluators".to_string(),
                Value::Array(evaluator_refs(evaluators)?),
            );
        }
        if let Some(validation) = validation {
            evaluation.insert("validation_set".to_string(), validation);
        }
        if let Some(concurrency) = concurrency {
            evaluation.insert(
                "max_concurrent_agent_runs".to_string(),
                json!(positive_int(
                    Some(concurrency),
                    "options.max_concurrent_agent_runs"
                )?),
            );
        }
        request.insert(
            "optimization_configuration".to_string(),
            skill_optimization(traces, evaluation, &resolved_agent)?,
        );
        return Ok(Value::Object(request));
    }

    let training = evaluation_set(
        lookup_value(eval_config, &["dataset"]),
        dataset_file,
        dataset_items,
        "dataset",
    )?
    .ok_or_else(|| error("dataset.local_uri, dataset.name, or dataset_file is required"))?;

    let mut baseline_config = Map::new();
    if let Some(prompt) = first_truthy(&[
        opt_config.get("system_prompt"),
        baseline.get("instructions"),
    ]) {
        baseline_config.insert("system_prompt".to_string(), prompt.clone());
    }
    if let Some(model) = first_truthy(&[
        opt_config.get("model"),
        agent.get("model"),
        baseline.get("model"),
    ]) {
        baseline_config.insert("current_model".to_string(), Value::String(py_str(model)));
    }
    baseline_config.insert(
        "skills".to_string(),
        array_or_empty(first_truthy(&[opt_config.get("skills")])),
    );
    baseline_config.insert(
        "tools".to_string(),
        array_or_empty(first_truthy(&[
            opt_config.get("tools"),
            baseline.get("tools"),
        ])),
    );

    let mut space = Map::new();
    if let Some(attributes) = opt_config.get("target_attributes").filter(|v| !v.is_null()) {
        let attributes = string_list(attributes);
        let bad: BTreeSet<&str> = attributes
            .iter()
            .map(String::as_str)
            .filter(|value| !TARGET_ATTRIBUTES.contains(value))
            .collect();
        if !bad.is_empty() {
            return Err(error(format!(
                "optimization_config.target_attributes has unsupported values: {}",
                bad.into_iter().collect::<Vec<_>>().join(", ")
            )));
        }
        space.insert("target_attributes".to_string(), json!(attributes));
    }
    if let Some(models) = opt_config
        .get("model_search_space")
        .filter(|v| !v.is_null())
    {
        space.insert("model_search_space".to_string(), json!(string_list(models)));
    }

    let mut evaluation = Map::new();
    evaluation.insert("training_set".to_string(), training);
    evaluation.insert(
        "evaluators".to_string(),
        Value::Array(evaluator_refs(evaluators)?),
    );
    evaluation.insert(
        "evaluation_model".to_string(),
        json!({"model": resolved_eval}),
    );
    if let Some(validation) = validation {
        evaluation.insert("validation_set".to_string(), validation);
    }
    if let Some(concurrency) = concurrency {
        evaluation.insert(
            "max_concurrent_agent_runs".to_string(),
            json!(positive_int(
                Some(concurrency),
                "options.max_concurrent_agent_runs"
            )?),
        );
    }

    let mut search = Map::new();
    if let Some(value) = resolved_max {
        search.insert("max_candidates".to_string(), json!(value));
    }

    let goal = lookup(&options, &["goal"])
        .filter(|v| truthy(v))
        .map(py_str)
        .unwrap_or_else(|| "improve_quality".to_string());
    request.insert(
        "optimization_configuration".to_string(),
        json!({
            "type": "agent_optimization",
            "goal": goal,
            "evaluation_configuration": evaluation,
            "candidate_search_configuration": search,
            "baseline_agent_configuration": baseline_config,
            "agent_optimization_space": space,
        }),
    );
    Ok(Value::Object(request))
}

/// Describe the HTTP request for one GA optimizer REST action.
///
/// Actions: `start`, `estimate`, `status`, `cancel`, `candidates`, `candidate`
/// (with `expand=mutations`), `candidate_config_file` (the candidate's
/// `config.json` download), and `candidate_resolved_config` (the
/// agentserver-optimization resolver call, `candidates/{id}/config` on the
/// `2025-11-15-preview` API version).
pub fn optimizer_rest_request(
    project_endpoint: &str,
    action: &str,
    job_id: Option<&str>,
    candidate_id: Option<&str>,
    body: &Value,
) -> Result<Value, OptimizerJobsError> {
    let endpoint = project_endpoint.trim_end_matches('/');
    let jobs = "/agent_optimization_jobs";
    let job = || required(job_id, "jobId").map(segment);
    let candidate = || required(candidate_id, "candidateId");
    let (method, path, params, send_body): (&str, String, Vec<(&str, String)>, bool) = match action
    {
        "start" => ("POST", jobs.to_string(), Vec::new(), true),
        "estimate" => ("POST", format!("{jobs}:estimate"), Vec::new(), true),
        "status" => ("GET", format!("{jobs}/{}", job()?), Vec::new(), false),
        "cancel" => (
            "POST",
            format!("{jobs}/{}:cancel", job()?),
            Vec::new(),
            false,
        ),
        "candidates" => (
            "GET",
            format!("{jobs}/{}/candidates", job()?),
            Vec::new(),
            false,
        ),
        "candidate" => {
            let job = job()?;
            (
                "GET",
                format!("{jobs}/{job}/candidates/{}", segment(candidate()?)),
                vec![("expand", "mutations".to_string())],
                false,
            )
        }
        "candidate_config_file" => {
            let job = job()?;
            (
                "GET",
                format!("{jobs}/{job}/files"),
                vec![
                    ("path", "config.json".to_string()),
                    ("candidate_id", candidate()?.to_string()),
                ],
                false,
            )
        }
        "candidate_resolved_config" => {
            let job = job()?;
            (
                "GET",
                format!("{jobs}/{job}/candidates/{}/config", segment(candidate()?)),
                Vec::new(),
                false,
            )
        }
        _ => return Err(error(format!("unknown optimizer action {action:?}"))),
    };
    let api_version = if action == "candidate_resolved_config" {
        RESOLVER_API_VERSION
    } else {
        API_VERSION
    };
    let mut headers = Map::new();
    if send_body {
        headers.insert(
            "Content-Type".to_string(),
            Value::String("application/json".to_string()),
        );
    }
    let query = params
        .iter()
        .map(|(key, value)| (*key, value.as_str()))
        .chain(std::iter::once(("api-version", api_version)))
        .map(|(key, value)| format!("{}={}", query_encode(key), query_encode(value)))
        .collect::<Vec<_>>()
        .join("&");
    Ok(json!({
        "method": method,
        "url": format!("{endpoint}{path}?{query}"),
        "headers": headers,
        "body": if send_body { body.clone() } else { Value::Null },
    }))
}

/// Whether an optimizer status is terminal (`skipped` included).
pub fn terminal_optimizer_status(status: Option<&str>) -> bool {
    TERMINAL_STATUSES.contains(&status.unwrap_or_default().to_ascii_lowercase().as_str())
}

/// `{id, code, message}` for eval output items whose sample errored: the
/// OpenAI evals `sample.error` shape or the flattened `sample_error` receipt.
/// Native scores are invalid when this is non-empty.
pub fn optimizer_sample_errors(items: &[Value]) -> Vec<Value> {
    fn present(value: Option<&Value>) -> Option<&Value> {
        value.filter(|v| match v {
            Value::Null | Value::Bool(false) => false,
            Value::String(s) => !s.is_empty(),
            Value::Object(m) => !m.is_empty(),
            Value::Array(a) => !a.is_empty(),
            Value::Number(n) => n.as_f64() != Some(0.0),
            Value::Bool(true) => true,
        })
    }
    items
        .iter()
        .filter(|item| item.is_object())
        .filter_map(|item| {
            let error = present(item.get("sample").and_then(|s| s.get("error")))
                .or_else(|| present(item.get("sample_error")))?;
            let (code, message) = match error {
                Value::Object(detail) => (
                    detail.get("code").cloned().unwrap_or(Value::Null),
                    detail.get("message").cloned().unwrap_or(Value::Null),
                ),
                Value::String(text) => (Value::Null, Value::String(text.clone())),
                other => (Value::Null, Value::String(other.to_string())),
            };
            Some(json!({
                "id": item.get("id").cloned().unwrap_or(Value::Null),
                "code": code,
                "message": message,
            }))
        })
        .collect()
}

/// `result.candidate_summary` from a GA job payload (or an empty map).
pub fn optimizer_candidate_summary(status: &Value) -> Map<String, Value> {
    status
        .get("result")
        .and_then(|result| result.get("candidate_summary"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

/// The best candidate id from `result.candidate_summary.best_id`.
pub fn best_optimizer_candidate_id(status: &Value) -> Option<String> {
    optimizer_candidate_summary(status)
        .get("best_id")
        .filter(|value| truthy(value))
        .map(py_str)
}

pub fn optimizer_job_id(payload: &Value) -> Option<String> {
    ["id", "job_id"]
        .iter()
        .find_map(|key| payload.get(*key).filter(|value| truthy(value)).map(py_str))
}

/// Combine the job baseline, a candidate's `config.json` and its mutations
/// into the candidate's full runnable configuration.
pub fn resolve_optimizer_candidate_config(
    job: &Value,
    candidate: &Value,
    config_file: Option<&Value>,
) -> Result<Value, OptimizerJobsError> {
    let candidate_id = candidate
        .get("candidate_id")
        .filter(|value| truthy(value))
        .map(py_str)
        .unwrap_or_else(|| "?".to_string());
    let opt = object_field(job, &["optimization_configuration"]);
    let job_type = opt.get("type").unwrap_or(&Value::Null);
    if !job_type.is_null() && job_type.as_str() != Some("agent_optimization") {
        return Err(error(format!(
            "candidate {candidate_id}: apply supports agent_optimization jobs only (job type is {})",
            py_repr(job_type)
        )));
    }
    let status = candidate
        .get("status")
        .filter(|value| truthy(value))
        .map(|value| py_str(value).to_lowercase())
        .unwrap_or_default();
    if !status.is_empty() && status != "completed" {
        return Err(error(format!(
            "candidate {candidate_id} is {status}; only completed candidates can be applied"
        )));
    }
    let baseline = object_field(
        &Value::Object(opt.clone()),
        &["baseline_agent_configuration"],
    );
    let mut instructions = baseline
        .get("system_prompt")
        .cloned()
        .unwrap_or(Value::Null);
    let mut model = baseline
        .get("current_model")
        .cloned()
        .unwrap_or(Value::Null);
    let mut tools = array_or_empty(baseline.get("tools").filter(|v| truthy(v)));
    let mut skills = array_or_empty(baseline.get("skills").filter(|v| truthy(v)));

    let mutations = candidate
        .get("output")
        .and_then(|output| output.get("mutations"))
        .filter(|value| truthy(value))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if config_file.is_none() && mutations.is_empty() {
        return Err(error(format!(
            "candidate {candidate_id} has no config.json and no mutations \
             (it is likely the baseline); nothing to apply"
        )));
    }
    if let Some(config_file) = config_file {
        let object = config_file.as_object().ok_or_else(|| {
            error(format!(
                "candidate {candidate_id}: config.json must be a JSON object"
            ))
        })?;
        if let Some(value) = object.get("instructions").filter(|v| truthy(v)) {
            instructions = value.clone();
        }
        if let Some(value) = object.get("model").filter(|v| truthy(v)) {
            model = value.clone();
        }
        if let Some(value) = object.get("tools").filter(|v| v.is_array()) {
            tools = value.clone();
        }
        if let Some(value) = object.get("skills").filter(|v| v.is_array()) {
            skills = value.clone();
        }
    }

    for mutation in &mutations {
        let kind = mutation.get("type").unwrap_or(&Value::Null);
        let value = mutation.get("value").unwrap_or(&Value::Null);
        let non_empty_text = value.as_str().is_some_and(|text| !text.is_empty());
        match kind.as_str() {
            Some("instructions") if non_empty_text => {
                if config_file.is_none() {
                    instructions = value.clone();
                }
            }
            Some("model") if non_empty_text => {
                if config_file.is_none() {
                    model = value.clone();
                }
            }
            Some("tools") if value.is_array() => {
                tools = merge_by_name(&tools, value, tool_name)?;
            }
            Some("skills") if value.is_array() => {
                skills = merge_by_name(&skills, value, skill_name)?;
            }
            _ => {
                return Err(error(format!(
                    "candidate {candidate_id}: unsupported or malformed mutation {}",
                    py_repr(kind)
                )))
            }
        }
    }

    if !truthy(&instructions) {
        return Err(error(format!(
            "candidate {candidate_id}: resolved configuration has no instructions"
        )));
    }
    let mut config = Map::new();
    for (key, value) in [
        ("instructions", instructions),
        ("model", model),
        ("tools", tools),
        ("skills", skills),
    ] {
        let empty_list = value.as_array().is_some_and(Vec::is_empty);
        if !value.is_null() && !empty_list {
            config.insert(key.to_string(), value);
        }
    }
    Ok(Value::Object(config))
}

pub fn optimizer_candidate_apply_plan(candidate_id: &str, config: &Value) -> Value {
    let mut metadata = String::new();
    if let Some(model) = config
        .get("model")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        metadata.push_str(&format!("model: {model}\n"));
    }
    if let Some(temperature) = config.get("temperature").filter(|value| !value.is_null()) {
        metadata.push_str(&format!("temperature: {}\n", yaml_scalar(temperature)));
    }
    let instructions = config
        .get("instructions")
        .or_else(|| config.get("system_prompt"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    if instructions.is_some() {
        metadata.push_str("instruction_file: instructions.md\n");
    }
    let tools = config
        .get("tools")
        .and_then(Value::as_array)
        .filter(|value| !value.is_empty());
    let tools_text = tools.map(|tools| pretty_json(&Value::Array(tools.clone())));
    if tools_text.is_some() {
        metadata.push_str("tool_file: tools.json\n");
        metadata.push_str("tools_file: tools.json\n");
    }
    let skill_files = skill_files(config.get("skills").and_then(Value::as_array));
    if !skill_files.is_empty() {
        metadata.push_str("skill_dir: skills\n");
    }
    json!({
        "candidateDir": candidate_id,
        "metadata": metadata,
        "instructions": instructions
            .map(|value| Value::String(value.to_string()))
            .unwrap_or(Value::Null),
        "tools": tools_text.map(Value::String).unwrap_or(Value::Null),
        "skillFiles": skill_files,
    })
}

fn object_field(value: &Value, keys: &[&str]) -> Map<String, Value> {
    lookup_value(value, keys)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

/// First non-null value among `keys` (canonical snake_case name first, then aliases).
fn lookup<'a>(object: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter()
        .find_map(|key| object.get(*key).filter(|value| !value.is_null()))
}

fn lookup_value<'a>(value: &'a Value, keys: &[&str]) -> Option<&'a Value> {
    value.as_object().and_then(|object| lookup(object, keys))
}

fn override_or(value: Option<&str>, fallback: Option<&Value>) -> Option<String> {
    value
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .or_else(|| fallback.filter(|value| truthy(value)).map(py_str))
}

fn first_truthy<'a>(values: &[Option<&'a Value>]) -> Option<&'a Value> {
    values.iter().flatten().copied().find(|value| truthy(value))
}

fn array_or_empty(value: Option<&Value>) -> Value {
    Value::Array(value.and_then(Value::as_array).cloned().unwrap_or_default())
}

fn string_list(value: &Value) -> Vec<String> {
    value.as_array().into_iter().flatten().map(py_str).collect()
}

fn sorted_keys_outside<'a>(object: &'a Map<String, Value>, allowed: &[&str]) -> Vec<&'a str> {
    let keys: BTreeSet<&str> = object
        .keys()
        .map(String::as_str)
        .filter(|key| !allowed.contains(key))
        .collect();
    keys.into_iter().collect()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|number| number != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

/// Python `str()` for JSON scalars.
fn py_str(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// Python `repr()` for JSON scalars.
fn py_repr(value: &Value) -> String {
    match value {
        Value::String(value) => format!("'{value}'"),
        other => py_str(other),
    }
}

fn int_like(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_f64().map(|value| value.trunc() as i64)),
        Value::String(text) => text.trim().parse().ok(),
        _ => None,
    }
}

fn positive_int(value: Option<&Value>, location: &str) -> Result<i64, OptimizerJobsError> {
    value
        .and_then(Value::as_i64)
        .filter(|value| *value >= 1)
        .ok_or_else(|| error(format!("{location} must be a positive integer")))
}

fn evaluation_set(
    block: Option<&Value>,
    legacy_file: Option<&Value>,
    items: &Value,
    location: &str,
) -> Result<Option<Value>, OptimizerJobsError> {
    let block = block.and_then(Value::as_object);
    let source = if legacy_file.is_some_and(truthy) {
        Some(inline_source(items, location)?)
    } else if let Some(object) = block {
        if lookup(object, &["local_uri", "localUri"]).is_some_and(truthy) {
            Some(inline_source(items, location)?)
        } else if let Some(name) = object.get("name").filter(|value| truthy(value)) {
            let version = object
                .get("version")
                .filter(|value| !value.is_null() && value.as_str() != Some(""))
                .ok_or_else(|| {
                    error(format!(
                        "{location}.version is required for a registered dataset \
                         ({location}.name={})",
                        py_repr(name)
                    ))
                })?;
            Some(json!({
                "type": "dataset_reference",
                "name": py_str(name),
                "version": py_str(version),
            }))
        } else {
            None
        }
    } else {
        None
    };
    Ok(source.map(|source| json!({"type": "target_completion", "source": source})))
}

fn inline_source(items: &Value, location: &str) -> Result<Value, OptimizerJobsError> {
    let rows = items
        .as_array()
        .ok_or_else(|| error(format!("{location} requires inline dataset items")))?;
    if rows.is_empty() {
        return Err(error(format!("{location}: dataset items are empty")));
    }
    let cases = rows
        .iter()
        .enumerate()
        .map(|(index, row)| test_case(row, &format!("{location}:{}", index + 1)))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({"type": "inline", "test_cases": cases}))
}

fn test_case(row: &Value, location: &str) -> Result<Value, OptimizerJobsError> {
    let object = row
        .as_object()
        .ok_or_else(|| error(format!("{location}: expected a JSON object")))?;
    let query = object
        .get("query")
        .and_then(Value::as_str)
        .filter(|query| !query.trim().is_empty())
        .ok_or_else(|| error(format!("{location}: 'query' must be non-empty text")))?;
    let mut allowed = vec!["query"];
    allowed.extend(GROUND_TRUTH_ALIASES);
    let unknown = sorted_keys_outside(object, &allowed);
    if !unknown.is_empty() {
        return Err(error(format!(
            "{location}: unsupported fields {}; optimizer test cases accept query and \
             ground_truth (aliases: answer, expected)",
            unknown.join(", ")
        )));
    }
    let truths: Vec<(&str, String)> = GROUND_TRUTH_ALIASES
        .iter()
        .filter_map(|key| {
            object
                .get(*key)
                .filter(|value| !value.is_null())
                .map(|value| (*key, as_text(value)))
        })
        .collect();
    let distinct: BTreeSet<&str> = truths.iter().map(|(_, value)| value.as_str()).collect();
    if distinct.len() > 1 {
        let names: BTreeSet<&str> = truths.iter().map(|(key, _)| *key).collect();
        return Err(error(format!(
            "{location}: conflicting ground truth values in {}",
            names.into_iter().collect::<Vec<_>>().join(", ")
        )));
    }
    let mut case = Map::new();
    case.insert("query".to_string(), Value::String(query.to_string()));
    if let Some((_, truth)) = truths.into_iter().next() {
        case.insert("ground_truth".to_string(), Value::String(truth));
    }
    Ok(Value::Object(case))
}

fn as_text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        other => python_json(other),
    }
}

/// `json.dumps(value, ensure_ascii=False, sort_keys=True)` with Python separators.
fn python_json(value: &Value) -> String {
    match value {
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(python_json).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(object) => {
            let mut keys: Vec<&String> = object.keys().collect();
            keys.sort();
            format!(
                "{{{}}}",
                keys.into_iter()
                    .map(|key| format!(
                        "{}: {}",
                        Value::String(key.clone()),
                        python_json(&object[key])
                    ))
                    .collect::<Vec<_>>()
                    .join(", ")
            )
        }
        other => other.to_string(),
    }
}

fn evaluator_refs(entries: &Value) -> Result<Vec<Value>, OptimizerJobsError> {
    let mut refs = Vec::new();
    for entry in entries.as_array().into_iter().flatten() {
        match entry {
            Value::String(_) => refs.push(json!({"name": entry})),
            Value::Object(object) => {
                let Some(name) = object.get("name").filter(|value| truthy(value)) else {
                    continue;
                };
                let mut item = Map::new();
                item.insert("name".to_string(), name.clone());
                if let Some(version) = object.get("version").filter(|value| !value.is_null()) {
                    item.insert("version".to_string(), Value::String(py_str(version)));
                }
                if let Some(parameters) = object
                    .get("initialization_parameters")
                    .filter(|value| !value.is_null())
                {
                    item.insert("initialization_parameters".to_string(), parameters.clone());
                }
                refs.push(Value::Object(item));
            }
            _ => {}
        }
    }
    if refs.is_empty() {
        return Err(error("at least one evaluator is required"));
    }
    Ok(refs)
}

fn skill_optimization(
    traces: &Value,
    evaluation: Map<String, Value>,
    agent_name: &str,
) -> Result<Value, OptimizerJobsError> {
    let object = traces
        .as_object()
        .ok_or_else(|| error("traces: expected a mapping"))?;
    let unknown = sorted_keys_outside(object, &TRACE_KEYS);
    if !unknown.is_empty() {
        return Err(error(format!(
            "traces has unsupported keys: {}",
            unknown.join(", ")
        )));
    }
    let resource_id = object
        .get("resource_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            error("traces.resource_id (Application Insights resource id) is required")
        })?;
    let trace_agent = object
        .get("agent_name")
        .filter(|value| truthy(value))
        .map(py_str)
        .unwrap_or_else(|| agent_name.to_string());
    let mut config = json!({
        "type": "skill_optimization",
        "context": [{
            "type": "agent_traces",
            "trace_connection_configuration": {
                "type": "application_insights",
                "resource_id": resource_id,
                "agent_name": trace_agent,
            },
            "trace_window": trace_window(object.get("window"))?,
        }],
        "evaluation_configuration": evaluation,
    });
    if let Some(ratio) = object
        .get("training_split_ratio")
        .filter(|value| !value.is_null())
    {
        let ratio = ratio
            .as_f64()
            .filter(|ratio| *ratio > 0.0 && *ratio < 1.0)
            .ok_or_else(|| {
                error("traces.training_split_ratio must be between 0 and 1 (exclusive)")
            })?;
        config["training_split_ratio"] = json!(ratio);
    }
    Ok(config)
}

fn trace_window(window: Option<&Value>) -> Result<Value, OptimizerJobsError> {
    let object = window.and_then(Value::as_object).ok_or_else(|| {
        error(
            "traces.window is required: {start_time, end_time[, max_traces]} \
             or {trace_ids, lookback_hours}",
        )
    })?;
    let fixed = ["start_time", "end_time", "max_traces"];
    let by_id = ["trace_ids", "lookback_hours"];
    let has_fixed = object.keys().any(|key| fixed.contains(&key.as_str()));
    let has_by_id = object.keys().any(|key| by_id.contains(&key.as_str()));
    if has_fixed && has_by_id {
        return Err(error(
            "traces.window cannot mix a time range with trace_ids",
        ));
    }
    if has_by_id {
        let extra = sorted_keys_outside(object, &by_id);
        if !extra.is_empty() {
            return Err(error(format!(
                "traces.window has unsupported keys: {}",
                extra.join(", ")
            )));
        }
        let ids = object
            .get("trace_ids")
            .and_then(Value::as_array)
            .filter(|ids| !ids.is_empty())
            .and_then(|ids| {
                ids.iter()
                    .map(|id| {
                        id.as_str()
                            .map(str::trim)
                            .filter(|id| !id.is_empty())
                            .map(str::to_string)
                    })
                    .collect::<Option<Vec<_>>>()
            })
            .ok_or_else(|| error("traces.window.trace_ids must be a non-empty list of ids"))?;
        return Ok(json!({
            "type": "trace_ids",
            "trace_ids": ids,
            "lookback_hours": positive_int(
                object.get("lookback_hours"),
                "traces.window.lookback_hours"
            )?,
        }));
    }
    let extra = sorted_keys_outside(object, &fixed);
    if !extra.is_empty() {
        return Err(error(format!(
            "traces.window has unsupported keys: {}",
            extra.join(", ")
        )));
    }
    let start = aware_datetime(object.get("start_time"), "traces.window.start_time")?;
    let end = aware_datetime(object.get("end_time"), "traces.window.end_time")?;
    if start >= end {
        return Err(error("traces.window.start_time must be before end_time"));
    }
    let mut payload = json!({
        "type": "fixed",
        "start_time": isoformat(&start),
        "end_time": isoformat(&end),
    });
    if let Some(max_traces) = object.get("max_traces").filter(|value| !value.is_null()) {
        payload["max_traces"] = json!(positive_int(Some(max_traces), "traces.window.max_traces")?);
    }
    Ok(payload)
}

fn aware_datetime(
    value: Option<&Value>,
    location: &str,
) -> Result<DateTime<FixedOffset>, OptimizerJobsError> {
    let raw = value.and_then(Value::as_str).unwrap_or_default();
    let text = raw.trim();
    if text.is_empty() {
        return Err(error(format!(
            "{location} is required (ISO 8601 with a timezone)"
        )));
    }
    let normalized = match text.as_bytes().get(10) {
        Some(b' ') => format!("{}T{}", &text[..10], &text[11..]),
        _ => text.to_string(),
    };
    if let Ok(parsed) = DateTime::parse_from_rfc3339(&normalized) {
        return Ok(parsed);
    }
    let naive = NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M:%S%.f").is_ok()
        || NaiveDateTime::parse_from_str(&normalized, "%Y-%m-%dT%H:%M").is_ok()
        || NaiveDate::parse_from_str(&normalized, "%Y-%m-%d").is_ok();
    if naive {
        return Err(error(format!(
            "{location} must include a timezone (for example 2026-01-01T00:00:00Z)"
        )));
    }
    Err(error(format!(
        "{location}: not an ISO 8601 timestamp: '{raw}'"
    )))
}

/// Python `datetime.isoformat()` for an aware timestamp.
fn isoformat(value: &DateTime<FixedOffset>) -> String {
    let pattern = if value.nanosecond() == 0 {
        "%Y-%m-%dT%H:%M:%S%:z"
    } else {
        "%Y-%m-%dT%H:%M:%S%.6f%:z"
    };
    value.format(pattern).to_string()
}

fn merge_by_name(
    base: &Value,
    updates: &Value,
    name_of: fn(&Value) -> Option<String>,
) -> Result<Value, OptimizerJobsError> {
    let mut merged = base.as_array().cloned().unwrap_or_default();
    for item in updates.as_array().into_iter().flatten() {
        let name =
            name_of(item).ok_or_else(|| error(format!("mutation entry has no name: {item}")))?;
        match merged
            .iter()
            .position(|existing| name_of(existing).as_deref() == Some(name.as_str()))
        {
            Some(index) => merged[index] = item.clone(),
            None => merged.push(item.clone()),
        }
    }
    Ok(Value::Array(merged))
}

fn tool_name(tool: &Value) -> Option<String> {
    tool.get("function")
        .and_then(|function| function.get("name"))
        .filter(|name| truthy(name))
        .or_else(|| tool.get("name").filter(|name| truthy(name)))
        .map(py_str)
}

fn skill_name(skill: &Value) -> Option<String> {
    skill.get("name").filter(|name| truthy(name)).map(py_str)
}

fn required<'a>(value: Option<&'a str>, name: &str) -> Result<&'a str, OptimizerJobsError> {
    value
        .filter(|value| !value.is_empty())
        .ok_or_else(|| error(format!("{name} is required")))
}

/// Python `urllib.parse.quote(value, safe="")`.
fn segment(value: &str) -> String {
    utf8_percent_encode(value, URL_SAFE).to_string()
}

/// Python `urllib.parse.quote_plus` as used by `urlencode`.
fn query_encode(value: &str) -> String {
    utf8_percent_encode(value, URL_SAFE)
        .to_string()
        .replace("%20", "+")
}

fn pretty_json(value: &Value) -> String {
    format!(
        "{}\n",
        serde_json::to_string_pretty(value).unwrap_or_default()
    )
}

fn yaml_scalar(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}

fn skill_files(skills: Option<&Vec<Value>>) -> Vec<Value> {
    skills
        .into_iter()
        .flatten()
        .filter_map(|skill| {
            let object = skill.as_object()?;
            let name = object
                .get("name")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())?;
            let mut content = format!("---\nname: {name}\n");
            if let Some(description) = object
                .get("description")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                content.push_str(&format!("description: {description}\n"));
            }
            content.push_str("---\n");
            if let Some(body) = object
                .get("body")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                content.push_str(body.trim_end_matches('\n'));
                content.push('\n');
            }
            Some(json!({
                "path": format!("skills/{name}/SKILL.md"),
                "content": content,
            }))
        })
        .collect()
}

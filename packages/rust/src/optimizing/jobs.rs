use crate::model::OptimizerJobsRuntime;
use serde_json::{json, Map, Value};
use std::error::Error;
use std::fmt;

pub const API_VERSION: &str = "v1";
pub const FOUNDRY_FEATURES: &str = "AgentsOptimization=V2Preview";

#[derive(Debug, Clone)]
pub struct OptimizerJobsError(String);

impl fmt::Display for OptimizerJobsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl Error for OptimizerJobsError {}

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
    let agent = object_field(eval_config, "agent");
    let options = object_field(eval_config, "options");
    let opt_config = object_field(&Value::Object(options.clone()), "optimization_config");

    let resolved_agent = agent_name
        .filter(|value| !value.is_empty())
        .or_else(|| string_in(&agent, "name"))
        .ok_or_else(|| {
            OptimizerJobsError("agent.name is required (or pass --agent)".to_string())
        })?;
    let resolved_eval = eval_model
        .filter(|value| !value.is_empty())
        .or_else(|| string_in(&options, "eval_model").or_else(|| string_in(&options, "evalModel")))
        .ok_or_else(|| {
            OptimizerJobsError("options.eval_model is required (or pass --eval-model)".to_string())
        })?;
    let resolved_optimize = optimize_model
        .filter(|value| !value.is_empty())
        .or_else(|| {
            string_in(&options, "optimization_model")
                .or_else(|| string_in(&options, "optimizationModel"))
        })
        .ok_or_else(|| {
            OptimizerJobsError(
                "options.optimization_model is required (or pass --optimize-model)".to_string(),
            )
        })?;
    let resolved_max = max_candidates.or_else(|| {
        options
            .get("max_candidates")
            .or_else(|| options.get("maxCandidates"))
            .and_then(Value::as_i64)
            .map(|value| value as i32)
    });
    if matches!(resolved_max, Some(value) if value < 1) {
        return Err(OptimizerJobsError(
            "max_candidates must be >= 1".to_string(),
        ));
    }

    let mut optimization_config = opt_config;
    if let Some(model) = string_in(&agent, "model").or_else(|| {
        baseline
            .get("model")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
    }) {
        optimization_config.insert("model".to_string(), Value::String(model.to_string()));
    }
    if let Some(instructions) = baseline
        .get("instructions")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        optimization_config.insert(
            "system_prompt".to_string(),
            Value::String(instructions.to_string()),
        );
    }
    optimization_config
        .entry("skills".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Some(tools) = baseline.get("tools").and_then(Value::as_array) {
        if !tools.is_empty() {
            optimization_config.insert("tools".to_string(), Value::Array(tools.clone()));
        }
    }

    let mut options_out = Map::new();
    options_out.insert(
        "eval_model".to_string(),
        Value::String(resolved_eval.to_string()),
    );
    options_out.insert(
        "optimization_model".to_string(),
        Value::String(resolved_optimize.to_string()),
    );
    options_out.insert(
        "optimization_config".to_string(),
        Value::Object(optimization_config),
    );
    if let Some(value) = resolved_max {
        options_out.insert("max_candidates".to_string(), json!(value));
    }
    for (key, aliases) in [
        ("evaluation_level", ["evaluation_level", "evaluationLevel"]),
        ("max_stalls", ["max_stalls", "maxStalls"]),
        (
            "max_concurrent_agent_runs",
            ["max_concurrent_agent_runs", "maxConcurrentAgentRuns"],
        ),
    ] {
        if let Some(value) = aliases.iter().find_map(|alias| options.get(*alias)) {
            options_out.insert(key.to_string(), value.clone());
        }
    }

    let train_dataset = match legacy_dataset_payload(eval_config, dataset_items)? {
        Some(payload) => Some(payload),
        None => dataset_payload(
            eval_config.get("dataset").unwrap_or(&Value::Null),
            dataset_items,
        )?,
    }
    .ok_or_else(|| {
        OptimizerJobsError(
            "dataset.local_uri, dataset.name, or dataset_file is required".to_string(),
        )
    })?;

    let mut out = Map::new();
    out.insert(
        "agent".to_string(),
        json!({
            "agent_name": resolved_agent,
            "agent_version": agent_version
                .filter(|value| !value.is_empty())
                .or_else(|| string_in(&agent, "version"))
                .unwrap_or(""),
        }),
    );
    out.insert(
        "evaluators".to_string(),
        Value::Array(evaluator_refs(
            eval_config.get("evaluators").unwrap_or(&Value::Null),
        )?),
    );
    out.insert("options".to_string(), Value::Object(options_out));
    out.insert("train_dataset".to_string(), train_dataset);
    if let Some(validation) = dataset_payload(
        eval_config
            .get("validation_dataset")
            .unwrap_or(&Value::Null),
        validation_items,
    )? {
        out.insert("validation_dataset".to_string(), validation);
    }
    Ok(Value::Object(out))
}

pub fn optimizer_rest_request(
    project_endpoint: &str,
    action: &str,
    job_id: Option<&str>,
    candidate_id: Option<&str>,
    body: &Value,
) -> Result<Value, OptimizerJobsError> {
    let endpoint = project_endpoint.trim_end_matches('/');
    let (method, path, output_body, content_type) = match action {
        "start" => (
            "POST",
            "/agent_optimization_jobs".to_string(),
            json!({"inputs": body.clone()}),
            true,
        ),
        "status" => (
            "GET",
            format!("/agent_optimization_jobs/{}", required(job_id, "jobId")?),
            Value::Null,
            false,
        ),
        "cancel" => (
            "POST",
            format!(
                "/agent_optimization_jobs/{}:cancel",
                required(job_id, "jobId")?
            ),
            Value::Null,
            false,
        ),
        "candidate_config" => (
            "GET",
            format!(
                "/agent_optimization_jobs/{}/candidates/{}/config",
                required(job_id, "jobId")?,
                required(candidate_id, "candidateId")?
            ),
            Value::Null,
            false,
        ),
        _ => {
            return Err(OptimizerJobsError(format!(
                "unknown optimizer action {action:?}"
            )))
        }
    };
    let mut headers = Map::new();
    headers.insert(
        "Foundry-Features".to_string(),
        Value::String(FOUNDRY_FEATURES.to_string()),
    );
    if content_type {
        headers.insert(
            "Content-Type".to_string(),
            Value::String("application/json".to_string()),
        );
    }
    Ok(json!({
        "method": method,
        "url": with_api_version(&format!("{endpoint}{path}")),
        "headers": headers,
        "body": output_body,
    }))
}

pub fn terminal_optimizer_status(status: Option<&str>) -> bool {
    matches!(
        status.unwrap_or_default().to_ascii_lowercase().as_str(),
        "succeeded" | "failed" | "cancelled" | "canceled" | "completed"
    )
}

pub fn best_optimizer_candidate_id(status: &Value) -> Option<String> {
    let result = status.get("result")?.as_object()?;
    if let Some(best) = result.get("best").and_then(value_string) {
        return Some(best);
    }
    for key in ["best_candidate_id", "bestCandidateId"] {
        if let Some(best) = result.get(key).and_then(value_string) {
            return Some(best);
        }
    }
    let candidates = result.get("candidates")?.as_array()?;
    let mut best: Option<(String, f64)> = None;
    for candidate in candidates {
        let Some(object) = candidate.as_object() else {
            continue;
        };
        let Some(id) = object
            .get("candidate_id")
            .or_else(|| object.get("candidateId"))
            .and_then(value_string)
        else {
            continue;
        };
        let score = ["avg_score", "average_score", "score"]
            .iter()
            .find_map(|key| object.get(*key).and_then(truthy_f64))
            .unwrap_or(0.0);
        if best
            .as_ref()
            .map(|(_, best_score)| score > *best_score)
            .unwrap_or(true)
        {
            best = Some((id, score));
        }
    }
    best.map(|(id, _)| id)
}

pub fn optimizer_job_id(payload: &Value) -> Option<String> {
    ["operation_id", "operationId", "id", "job_id", "jobId"]
        .iter()
        .find_map(|key| payload.get(*key).and_then(value_string))
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

fn object_field(value: &Value, key: &str) -> Map<String, Value> {
    value
        .get(key)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

fn string_in<'a>(object: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn value_string(value: &Value) -> Option<String> {
    match value {
        Value::String(value) if !value.is_empty() => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn truthy_f64(value: &Value) -> Option<f64> {
    let score = value.as_f64()?;
    (score != 0.0).then_some(score)
}

fn legacy_dataset_payload(
    eval_config: &Value,
    items: &Value,
) -> Result<Option<Value>, OptimizerJobsError> {
    if eval_config
        .get("dataset_file")
        .or_else(|| eval_config.get("datasetFile"))
        .is_none()
    {
        return Ok(None);
    }
    let rows = non_empty_items(items)?;
    Ok(Some(json!({"type": "inline", "items": rows})))
}

fn dataset_payload(block: &Value, items: &Value) -> Result<Option<Value>, OptimizerJobsError> {
    let Some(object) = block.as_object() else {
        return Ok(None);
    };
    if object
        .get("local_uri")
        .or_else(|| object.get("localUri"))
        .is_some()
    {
        let rows = non_empty_items(items)?;
        return Ok(Some(json!({"type": "inline", "items": rows})));
    }
    if let Some(name) = object.get("name").and_then(value_string) {
        let mut payload = Map::new();
        payload.insert("type".to_string(), Value::String("reference".to_string()));
        payload.insert("name".to_string(), Value::String(name));
        if let Some(version) = object.get("version").and_then(value_string) {
            payload.insert("version".to_string(), Value::String(version));
        }
        return Ok(Some(Value::Object(payload)));
    }
    Ok(None)
}

fn non_empty_items(items: &Value) -> Result<Vec<Value>, OptimizerJobsError> {
    let rows = items.as_array().cloned().ok_or_else(|| {
        OptimizerJobsError("dataset.local_uri requires inline dataset items".to_string())
    })?;
    if rows.is_empty() {
        return Err(OptimizerJobsError(
            "dataset.local_uri requires non-empty inline dataset items".to_string(),
        ));
    }
    Ok(rows)
}

fn evaluator_refs(entries: &Value) -> Result<Vec<Value>, OptimizerJobsError> {
    let mut refs = Vec::new();
    for entry in entries.as_array().into_iter().flatten() {
        if let Some(name) = entry.as_str().filter(|value| !value.is_empty()) {
            refs.push(json!({"name": name}));
        } else if let Some(object) = entry.as_object() {
            if let Some(name) = object.get("name").and_then(value_string) {
                let mut item = Map::new();
                item.insert("name".to_string(), Value::String(name));
                if let Some(version) = object.get("version").and_then(value_string) {
                    item.insert("version".to_string(), Value::String(version));
                }
                if let Some(parameters) = object
                    .get("initialization_parameters")
                    .filter(|value| !value.is_null())
                {
                    item.insert("initialization_parameters".to_string(), parameters.clone());
                }
                refs.push(Value::Object(item));
            }
        }
    }
    if refs.is_empty() {
        return Err(OptimizerJobsError(
            "at least one evaluator is required".to_string(),
        ));
    }
    Ok(refs)
}

fn required<'a>(value: Option<&'a str>, name: &str) -> Result<&'a str, OptimizerJobsError> {
    value
        .filter(|value| !value.is_empty())
        .ok_or_else(|| OptimizerJobsError(format!("{name} is required")))
}

fn with_api_version(url: &str) -> String {
    let sep = if url.contains('?') { '&' } else { '?' };
    format!("{url}{sep}api-version={API_VERSION}")
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

use crate::model::FinetuningTrainingRuntime;
use serde_json::{json, Map, Value};
use std::collections::{BTreeSet, HashSet};

const VALID_CHAT_ROLES: &[&str] = &["system", "developer", "user", "assistant", "tool"];
const SFT_HYPERPARAMETERS: &[&str] = &["n_epochs", "batch_size", "learning_rate_multiplier"];
const DPO_HYPERPARAMETERS: &[&str] = &[
    "n_epochs",
    "batch_size",
    "learning_rate_multiplier",
    "beta",
    "l2_multiplier",
];
const RFT_HYPERPARAMETERS: &[&str] = &[
    "eval_interval",
    "eval_samples",
    "compute_multiplier",
    "reasoning_effort",
    "n_epochs",
    "batch_size",
    "learning_rate_multiplier",
];
const GRADER_TYPES: &[&str] = &[
    "string_check",
    "text_similarity",
    "score_model",
    "python",
    "multi",
    "endpoint",
];
const STRING_CHECK_OPS: &[&str] = &["eq", "ne", "like", "ilike"];
const TEXT_SIMILARITY_METRICS: &[&str] = &[
    "bleu",
    "gleu",
    "meteor",
    "rouge_1",
    "rouge_2",
    "rouge_3",
    "rouge_4",
    "rouge_5",
    "rouge_l",
    "cosine",
    "fuzzy_match",
];

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaFinetuningTrainingRuntime;

#[async_trait::async_trait]
impl FinetuningTrainingRuntime for CastiaFinetuningTrainingRuntime {
    fn build_dpo_method(&self, hyperparameters: &Value) -> Value {
        build_dpo_method(Some(hyperparameters)).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn build_rft_job(
        &self,
        model: &String,
        training_file: &String,
        validation_file: &String,
        grader: &Value,
        hyperparameters: &Value,
        response_format: &Value,
        suffix: &Option<String>,
        seed: &Option<i32>,
    ) -> Value {
        let response_format = (!response_format.is_null()).then_some(response_format);
        build_rft_job(
            model,
            training_file,
            validation_file,
            grader,
            Some(hyperparameters),
            response_format,
            suffix.as_deref(),
            seed.map(i64::from),
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn build_rft_method(
        &self,
        grader: &Value,
        hyperparameters: &Value,
        response_format: &Value,
    ) -> Value {
        let response_format = (!response_format.is_null()).then_some(response_format);
        build_rft_method(grader, Some(hyperparameters), response_format)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn build_sft_job(
        &self,
        model: &String,
        training_file: &String,
        validation_file: &Option<String>,
        hyperparameters: &Value,
        suffix: &Option<String>,
        seed: &Option<i32>,
    ) -> Value {
        build_sft_job(
            model,
            training_file,
            validation_file.as_deref(),
            Some(hyperparameters),
            suffix.as_deref(),
            seed.map(i64::from),
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn build_sft_method(&self, hyperparameters: &Value) -> Value {
        build_sft_method(Some(hyperparameters)).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn string_check_grader(
        &self,
        name: &String,
        input: &String,
        reference: &String,
        operation: &String,
    ) -> Value {
        string_check_grader(name, input, reference, operation)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn validate_dpo_example(&self, row: &Value) -> Vec<String> {
        validate_dpo_example(row)
    }

    fn validate_grader(&self, grader: &Value) -> Vec<String> {
        validate_grader(grader)
    }

    fn validate_rft_dataset(&self, rows: &Value, grader: &Value, split: &String) -> Vec<String> {
        let rows = rows.as_array().map(Vec::as_slice).unwrap_or(&[]);
        validate_rft_dataset(rows, Some(grader), split)
    }

    fn validate_rft_example(&self, row: &Value) -> Vec<String> {
        validate_rft_example(row)
    }

    fn validate_sft_example(&self, row: &Value) -> Vec<String> {
        validate_sft_example(row)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FinetuningError(pub String);

impl std::fmt::Display for FinetuningError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for FinetuningError {}

pub fn validate_sft_example(row: &Value) -> Vec<String> {
    if !row.is_object() {
        return vec!["example is not a JSON object".to_string()];
    }
    validate_chat_messages(row.get("messages"), "example", true, Some("assistant"))
}

pub fn validate_sft_dataset(rows: &[Value], split: &str) -> Vec<String> {
    validate_dataset(rows, split, validate_sft_example)
}

pub fn validate_sft_splits(train: &[Value], validation: Option<&[Value]>) -> Vec<String> {
    let mut problems = validate_sft_dataset(train, "training");
    if let Some(validation) = validation {
        problems.extend(validate_sft_dataset(validation, "validation"));
    }
    problems
}

pub fn build_sft_method(hyperparameters: Option<&Value>) -> Result<Value, FinetuningError> {
    build_method(
        "supervised",
        "supervised",
        hyperparameters,
        SFT_HYPERPARAMETERS,
    )
}

pub fn build_sft_job(
    model: &str,
    training_file: &str,
    validation_file: Option<&str>,
    hyperparameters: Option<&Value>,
    suffix: Option<&str>,
    seed: Option<i64>,
) -> Result<Value, FinetuningError> {
    let mut job = Map::new();
    job.insert("model".to_string(), Value::String(model.to_string()));
    job.insert(
        "training_file".to_string(),
        Value::String(training_file.to_string()),
    );
    job.insert("method".to_string(), build_sft_method(hyperparameters)?);
    if let Some(validation_file) = validation_file.filter(|value| !value.is_empty()) {
        job.insert(
            "validation_file".to_string(),
            Value::String(validation_file.to_string()),
        );
    }
    if let Some(suffix) = suffix {
        job.insert("suffix".to_string(), Value::String(suffix.to_string()));
    }
    if let Some(seed) = seed {
        job.insert("seed".to_string(), Value::Number(seed.into()));
    }
    Ok(Value::Object(job))
}

pub fn validate_dpo_example(row: &Value) -> Vec<String> {
    let mut problems = Vec::new();
    if !row.is_object() {
        return vec!["example is not a JSON object".to_string()];
    }

    let input_block = row.get("input");
    if input_block.and_then(Value::as_object).is_none() {
        problems.push("input must be an object".to_string());
    } else {
        problems.extend(validate_chat_messages(
            input_block.and_then(|value| value.get("messages")),
            "input",
            false,
            None,
        ));
    }
    problems.extend(validate_preference_messages(
        row.get("preferred_output"),
        "preferred_output",
    ));
    problems.extend(validate_preference_messages(
        row.get("non_preferred_output"),
        "non_preferred_output",
    ));
    problems
}

pub fn validate_dpo_dataset(rows: &[Value], split: &str) -> Vec<String> {
    validate_dataset(rows, split, validate_dpo_example)
}

pub fn validate_dpo_splits(train: &[Value], validation: Option<&[Value]>) -> Vec<String> {
    let mut problems = validate_dpo_dataset(train, "training");
    if let Some(validation) = validation {
        problems.extend(validate_dpo_dataset(validation, "validation"));
    }
    problems
}

pub fn build_dpo_method(hyperparameters: Option<&Value>) -> Result<Value, FinetuningError> {
    build_method("dpo", "dpo", hyperparameters, DPO_HYPERPARAMETERS)
}

pub fn build_dpo_job(
    model: &str,
    training_file: &str,
    validation_file: Option<&str>,
    hyperparameters: Option<&Value>,
    suffix: Option<&str>,
    seed: Option<i64>,
) -> Result<Value, FinetuningError> {
    let mut job = Map::new();
    job.insert("model".to_string(), Value::String(model.to_string()));
    job.insert(
        "training_file".to_string(),
        Value::String(training_file.to_string()),
    );
    job.insert("method".to_string(), build_dpo_method(hyperparameters)?);
    if let Some(validation_file) = validation_file.filter(|value| !value.is_empty()) {
        job.insert(
            "validation_file".to_string(),
            Value::String(validation_file.to_string()),
        );
    }
    if let Some(suffix) = suffix {
        job.insert("suffix".to_string(), Value::String(suffix.to_string()));
    }
    if let Some(seed) = seed {
        job.insert("seed".to_string(), Value::Number(seed.into()));
    }
    Ok(Value::Object(job))
}

pub fn string_check_grader(
    name: &str,
    input: &str,
    reference: &str,
    operation: &str,
) -> Result<Value, FinetuningError> {
    if !STRING_CHECK_OPS.contains(&operation) {
        return Err(FinetuningError(format!(
            "operation must be one of {}, got {}",
            py_tuple(STRING_CHECK_OPS),
            py_repr(operation)
        )));
    }
    Ok(json!({
        "type": "string_check",
        "name": name,
        "input": input,
        "reference": reference,
        "operation": operation,
    }))
}

pub fn text_similarity_grader(
    name: &str,
    input: &str,
    reference: &str,
    evaluation_metric: &str,
    pass_threshold: Option<f64>,
) -> Result<Value, FinetuningError> {
    if !TEXT_SIMILARITY_METRICS.contains(&evaluation_metric) {
        return Err(FinetuningError(format!(
            "evaluation_metric must be one of {}, got {}",
            py_tuple(TEXT_SIMILARITY_METRICS),
            py_repr(evaluation_metric)
        )));
    }
    let mut grader = json!({
        "type": "text_similarity",
        "name": name,
        "input": input,
        "reference": reference,
        "evaluation_metric": evaluation_metric,
    });
    if let Some(pass_threshold) = pass_threshold {
        grader["pass_threshold"] = json!(pass_threshold);
    }
    Ok(grader)
}

pub fn score_model_grader(
    name: &str,
    model: &str,
    input: &[Value],
    range: Option<(f64, f64)>,
    pass_threshold: Option<f64>,
    sampling_params: Option<&Value>,
) -> Value {
    let (lo, hi) = range.unwrap_or((0.0, 1.0));
    let mut grader = json!({
        "type": "score_model",
        "name": name,
        "model": model,
        "input": input,
        "range": [lo, hi],
    });
    if let Some(pass_threshold) = pass_threshold {
        grader["pass_threshold"] = json!(pass_threshold);
    }
    if let Some(sampling_params) = sampling_params {
        grader["sampling_params"] = sampling_params.clone();
    }
    grader
}

pub fn python_grader(name: &str, source: &str, image_tag: Option<&str>) -> Value {
    let mut grader = json!({
        "type": "python",
        "name": name,
        "source": source,
    });
    if let Some(image_tag) = image_tag {
        grader["image_tag"] = Value::String(image_tag.to_string());
    }
    grader
}

pub fn multi_grader(name: &str, graders: &Value, calculate_output: &str) -> Value {
    json!({
        "type": "multi",
        "name": name,
        "graders": graders,
        "calculate_output": calculate_output,
    })
}

pub fn validate_grader(grader: &Value) -> Vec<String> {
    let mut problems = Vec::new();
    let Some(obj) = grader.as_object() else {
        return vec!["grader must be a mapping".to_string()];
    };

    let gtype = obj.get("type").and_then(Value::as_str);
    if !gtype.is_some_and(|value| GRADER_TYPES.contains(&value)) {
        problems.push(format!(
            "grader type {} is not one of {}",
            py_repr_opt(gtype),
            py_tuple(GRADER_TYPES)
        ));
    }
    if !py_truthy(obj.get("name")) {
        problems.push("grader has no 'name'".to_string());
    }

    for field in required_grader_fields(gtype) {
        if is_empty_required(obj.get(*field)) {
            problems.push(format!(
                "{} grader missing required field {}",
                gtype.unwrap_or("None"),
                py_repr(field)
            ));
        }
    }

    if gtype == Some("string_check") {
        let operation = obj.get("operation");
        let valid = operation
            .and_then(Value::as_str)
            .is_some_and(|value| STRING_CHECK_OPS.contains(&value));
        if operation.is_some_and(|value| !value.is_null()) && !valid {
            problems.push(format!(
                "string_check operation {} not in {}",
                py_value_repr(operation.unwrap()),
                py_tuple(STRING_CHECK_OPS)
            ));
        }
    }
    if gtype == Some("text_similarity") {
        let metric = obj.get("evaluation_metric");
        let valid = metric
            .and_then(Value::as_str)
            .is_some_and(|value| TEXT_SIMILARITY_METRICS.contains(&value));
        if metric.is_some_and(|value| !value.is_null()) && !valid {
            problems.push(format!(
                "text_similarity metric {} not in {}",
                py_value_repr(metric.unwrap()),
                py_tuple(TEXT_SIMILARITY_METRICS)
            ));
        }
    }
    if gtype == Some("multi") {
        if let Some(subs) = obj.get("graders").and_then(Value::as_object) {
            for (key, sub) in subs {
                for problem in validate_grader(sub) {
                    problems.push(format!("sub-grader {}: {}", py_repr(key), problem));
                }
            }
        }
    }

    for reference in template_refs(grader) {
        let ns = reference
            .split_once('.')
            .map_or(reference.as_str(), |(ns, _)| ns);
        if ns != "sample" && ns != "item" {
            problems.push(format!(
                "template {{{{ {} }}}} uses unknown namespace {} (expected one of {})",
                reference,
                py_repr(ns),
                py_tuple(&["sample", "item"])
            ));
        }
    }
    problems
}

pub fn grader_item_fields(grader: &Value) -> BTreeSet<String> {
    template_refs(grader)
        .into_iter()
        .filter_map(|reference| reference.strip_prefix("item.").map(ToString::to_string))
        .collect()
}

pub fn validate_rft_example(row: &Value) -> Vec<String> {
    let mut problems = Vec::new();
    if !row.is_object() {
        return vec!["example is not a JSON object".to_string()];
    }

    let Some(messages) = row.get("messages").and_then(Value::as_array) else {
        return vec!["example has no non-empty 'messages' list".to_string()];
    };
    if messages.is_empty() {
        return vec!["example has no non-empty 'messages' list".to_string()];
    }

    for (index, message) in messages.iter().enumerate() {
        let Some(obj) = message.as_object() else {
            problems.push(format!(
                "message #{} is not a role-bearing object",
                index + 1
            ));
            continue;
        };
        if !obj.contains_key("role") {
            problems.push(format!(
                "message #{} is not a role-bearing object",
                index + 1
            ));
            continue;
        };
        let role = obj.get("role").and_then(Value::as_str);
        if !role.is_some_and(|role| VALID_CHAT_ROLES.contains(&role)) {
            problems.push(format!(
                "message #{} has invalid role {}",
                index + 1,
                py_value_repr(obj.get("role").unwrap())
            ));
        }
    }

    if let Some(last) = messages.last().and_then(Value::as_object) {
        let last_role = last.get("role").and_then(Value::as_str);
        if last_role != Some("user") {
            problems.push(format!(
                "final message role must be 'user', got {} (RFT generates the model's turn from the trailing user prompt)",
                last.get("role").map(py_value_repr).unwrap_or_else(|| "None".to_string())
            ));
        }
    }
    problems
}

pub fn validate_rft_dataset(rows: &[Value], grader: Option<&Value>, split: &str) -> Vec<String> {
    if rows.is_empty() {
        return vec![format!("{split}: no examples")];
    }
    let mut problems = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        for problem in validate_rft_example(row) {
            problems.push(format!("{split}[{index}]: {problem}"));
        }
    }
    if let Some(grader) = grader {
        let needed = grader_item_fields(grader);
        for (index, row) in rows.iter().enumerate() {
            let Some(obj) = row.as_object() else {
                continue;
            };
            let missing: Vec<_> = needed
                .iter()
                .filter(|field| !obj.contains_key(*field))
                .cloned()
                .collect();
            if !missing.is_empty() {
                problems.push(format!(
                    "{split}[{index}]: grader references item fields not on the row: {}",
                    missing.join(", ")
                ));
            }
        }
    }
    problems
}

pub fn validate_rft_splits(
    train: &[Value],
    validation: &[Value],
    grader: Option<&Value>,
) -> Vec<String> {
    let mut problems = Vec::new();
    if let Some(grader) = grader {
        problems.extend(
            validate_grader(grader)
                .into_iter()
                .map(|problem| format!("grader: {problem}")),
        );
    }
    problems.extend(validate_rft_dataset(train, grader, "training"));
    problems.extend(validate_rft_dataset(validation, grader, "validation"));
    problems
}

pub fn build_rft_method(
    grader: &Value,
    hyperparameters: Option<&Value>,
    response_format: Option<&Value>,
) -> Result<Value, FinetuningError> {
    if let Some(hyperparameters) = hyperparameters.filter(|value| !is_empty_value(value)) {
        reject_unknown_hyperparameters("RFT", hyperparameters, RFT_HYPERPARAMETERS)?;
    }
    let mut reinforcement = Map::new();
    reinforcement.insert("grader".to_string(), grader.clone());
    if let Some(hyperparameters) = hyperparameters.filter(|value| !is_empty_value(value)) {
        reinforcement.insert("hyperparameters".to_string(), hyperparameters.clone());
    }
    if let Some(response_format) = response_format {
        reinforcement.insert("response_format".to_string(), response_format.clone());
    }
    Ok(json!({
        "type": "reinforcement",
        "reinforcement": reinforcement,
    }))
}

pub fn build_rft_job(
    model: &str,
    training_file: &str,
    validation_file: &str,
    grader: &Value,
    hyperparameters: Option<&Value>,
    response_format: Option<&Value>,
    suffix: Option<&str>,
    seed: Option<i64>,
) -> Result<Value, FinetuningError> {
    if validation_file.is_empty() {
        return Err(FinetuningError(
            "RFT requires a validation_file in addition to training_file".to_string(),
        ));
    }
    let mut job = Map::new();
    job.insert("model".to_string(), Value::String(model.to_string()));
    job.insert(
        "training_file".to_string(),
        Value::String(training_file.to_string()),
    );
    job.insert(
        "validation_file".to_string(),
        Value::String(validation_file.to_string()),
    );
    job.insert(
        "method".to_string(),
        build_rft_method(grader, hyperparameters, response_format)?,
    );
    if let Some(suffix) = suffix {
        job.insert("suffix".to_string(), Value::String(suffix.to_string()));
    }
    if let Some(seed) = seed {
        job.insert("seed".to_string(), Value::Number(seed.into()));
    }
    Ok(Value::Object(job))
}

fn validate_chat_messages(
    messages: Option<&Value>,
    split: &str,
    require_assistant: bool,
    final_role: Option<&str>,
) -> Vec<String> {
    let mut problems = Vec::new();
    let Some(messages) = messages.and_then(Value::as_array) else {
        return vec![format!("{split}: no non-empty 'messages' list")];
    };
    if messages.is_empty() {
        return vec![format!("{split}: no non-empty 'messages' list")];
    }

    let mut has_user = false;
    let mut has_assistant = false;
    for (index, message) in messages.iter().enumerate() {
        let Some(msg) = message.as_object() else {
            problems.push(format!("{split}: message #{} is not an object", index + 1));
            continue;
        };
        let role_value = msg.get("role");
        let role = role_value.and_then(Value::as_str);
        if !role.is_some_and(|value| VALID_CHAT_ROLES.contains(&value)) {
            problems.push(format!(
                "{split}: message #{} has invalid role {}",
                index + 1,
                role_value
                    .map(py_value_repr)
                    .unwrap_or_else(|| "None".to_string())
            ));
        }
        has_user |= role == Some("user");
        has_assistant |= role == Some("assistant");
        if !has_content_or_tool_calls(msg) {
            problems.push(format!(
                "{split}: message #{} has no content or tool_calls",
                index + 1
            ));
        }
    }

    if !has_user {
        problems.push(format!(
            "{split}: messages must include at least one user message"
        ));
    }
    if require_assistant && !has_assistant {
        problems.push(format!(
            "{split}: messages must include at least one assistant message"
        ));
    }
    if let Some(final_role) = final_role {
        let role = messages
            .last()
            .and_then(Value::as_object)
            .and_then(|msg| msg.get("role"))
            .and_then(Value::as_str);
        if role != Some(final_role) {
            problems.push(format!(
                "{split}: final message role must be {}, got {}",
                py_repr(final_role),
                py_repr_opt(role)
            ));
        }
    }
    problems
}

fn validate_preference_messages(messages: Option<&Value>, split: &str) -> Vec<String> {
    let mut problems = Vec::new();
    let Some(messages) = messages.and_then(Value::as_array) else {
        return vec![format!("{split}: no non-empty message list")];
    };
    if messages.is_empty() {
        return vec![format!("{split}: no non-empty message list")];
    }

    let mut has_assistant = false;
    for (index, message) in messages.iter().enumerate() {
        let Some(msg) = message.as_object() else {
            problems.push(format!("{split}: message #{} is not an object", index + 1));
            continue;
        };
        let role = msg.get("role").and_then(Value::as_str);
        if role != Some("assistant") && role != Some("tool") {
            problems.push(format!(
                "{split}: message #{} role must be 'assistant' or 'tool', got {}",
                index + 1,
                py_repr_opt(role)
            ));
        }
        has_assistant |= role == Some("assistant");
        if !has_content_or_tool_calls(msg) {
            problems.push(format!(
                "{split}: message #{} has no content or tool_calls",
                index + 1
            ));
        }
    }
    if !has_assistant {
        problems.push(format!(
            "{split}: must include at least one assistant message"
        ));
    }
    problems
}

fn has_content_or_tool_calls(msg: &Map<String, Value>) -> bool {
    match msg.get("content") {
        Some(Value::String(content)) if !content.trim().is_empty() => return true,
        Some(Value::Array(content)) if !content.is_empty() => return true,
        _ => {}
    }
    msg.get("tool_calls")
        .and_then(Value::as_array)
        .is_some_and(|tool_calls| !tool_calls.is_empty())
}

fn validate_dataset(
    rows: &[Value],
    split: &str,
    validate_one: fn(&Value) -> Vec<String>,
) -> Vec<String> {
    if rows.is_empty() {
        return vec![format!("{split}: no examples")];
    }
    let mut problems = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        for problem in validate_one(row) {
            problems.push(format!("{split}[{index}]: {problem}"));
        }
    }
    problems
}

fn build_method(
    method_type: &str,
    payload_key: &str,
    hyperparameters: Option<&Value>,
    known: &[&str],
) -> Result<Value, FinetuningError> {
    if let Some(hyperparameters) = hyperparameters.filter(|value| !is_empty_value(value)) {
        let label = match method_type {
            "supervised" => "SFT",
            "dpo" => "DPO",
            _ => method_type,
        };
        reject_unknown_hyperparameters(label, hyperparameters, known)?;
    }
    let mut method = Map::new();
    method.insert("type".to_string(), Value::String(method_type.to_string()));
    if let Some(hyperparameters) = hyperparameters.filter(|value| !is_empty_value(value)) {
        method.insert(
            payload_key.to_string(),
            json!({ "hyperparameters": hyperparameters }),
        );
    }
    Ok(Value::Object(method))
}

fn reject_unknown_hyperparameters(
    label: &str,
    hyperparameters: &Value,
    known: &[&str],
) -> Result<(), FinetuningError> {
    let Some(obj) = hyperparameters.as_object() else {
        return Ok(());
    };
    let known_set: HashSet<&str> = known.iter().copied().collect();
    let unknown: Vec<_> = obj
        .keys()
        .filter(|key| !known_set.contains(key.as_str()))
        .cloned()
        .collect();
    if !unknown.is_empty() {
        return Err(FinetuningError(format!(
            "unknown {label} hyperparameter(s): {} (known: {})",
            unknown.join(", "),
            known.join(", ")
        )));
    }
    Ok(())
}

fn required_grader_fields(gtype: Option<&str>) -> &'static [&'static str] {
    match gtype {
        Some("string_check") => &["input", "reference", "operation"],
        Some("text_similarity") => &["input", "reference", "evaluation_metric"],
        Some("score_model") => &["model", "input"],
        Some("python") => &["source"],
        Some("multi") => &["graders", "calculate_output"],
        Some("endpoint") => &["endpoint"],
        _ => &[],
    }
}

fn is_empty_required(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => true,
        Some(Value::String(value)) => value.is_empty(),
        Some(Value::Array(value)) => value.is_empty(),
        Some(Value::Object(value)) => value.is_empty(),
        _ => false,
    }
}

fn is_empty_value(value: &Value) -> bool {
    matches!(value, Value::Null)
        || matches!(value, Value::Bool(false))
        || value.as_i64() == Some(0)
        || value.as_u64() == Some(0)
        || value.as_f64() == Some(0.0)
        || value.as_object().is_some_and(Map::is_empty)
        || value.as_array().is_some_and(Vec::is_empty)
}

fn py_truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => {
            value.as_i64() != Some(0) && value.as_u64() != Some(0) && value.as_f64() != Some(0.0)
        }
        Some(Value::String(value)) => !value.is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
    }
}

fn template_refs(value: &Value) -> BTreeSet<String> {
    let mut refs = BTreeSet::new();
    collect_template_refs(value, &mut refs);
    refs
}

fn collect_template_refs(value: &Value, refs: &mut BTreeSet<String>) {
    match value {
        Value::String(value) => {
            let bytes = value.as_bytes();
            let mut index = 0;
            while let Some(start) = value[index..].find("{{") {
                let open = index + start + 2;
                let Some(end_rel) = value[open..].find("}}") else {
                    break;
                };
                let close = open + end_rel;
                let candidate = value[open..close].trim();
                if is_template_identifier(candidate) {
                    refs.insert(candidate.to_string());
                }
                index = close + 2;
                if index >= bytes.len() {
                    break;
                }
            }
        }
        Value::Array(values) => {
            for value in values {
                collect_template_refs(value, refs);
            }
        }
        Value::Object(values) => {
            for value in values.values() {
                collect_template_refs(value, refs);
            }
        }
        _ => {}
    }
}

fn is_template_identifier(candidate: &str) -> bool {
    let mut chars = candidate.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '.')
}

fn py_tuple(values: &[&str]) -> String {
    format!(
        "({})",
        values
            .iter()
            .map(|value| py_repr(value))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

fn py_repr(value: &str) -> String {
    format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn py_repr_opt(value: Option<&str>) -> String {
    value.map(py_repr).unwrap_or_else(|| "None".to_string())
}

fn py_value_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => py_repr(value),
        Value::Array(_) | Value::Object(_) => value.to_string(),
    }
}

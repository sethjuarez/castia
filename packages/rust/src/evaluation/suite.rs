use crate::model::EvaluationSuiteRuntime;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

const DEFAULT_CONFIG: &str = "eval.yaml";
const AZD_EVAL: [&str; 4] = ["azd", "ai", "agent", "eval"];

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaEvaluationSuiteRuntime;

#[async_trait::async_trait]
impl EvaluationSuiteRuntime for CastiaEvaluationSuiteRuntime {
    fn build_generate_argv(&self, options: &Value) -> Value {
        build_generate_argv(options)
    }

    fn build_run_argv(&self, options: &Value) -> Value {
        build_run_argv(options)
    }

    fn build_update_argv(&self, options: &Value) -> Value {
        build_update_argv(options)
    }

    fn read_rubric(&self, value: &Value) -> Value {
        read_rubric(value)
    }

    fn load_suite(&self, path: &String) -> Value {
        load_suite(path).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn validate_suite(&self, path: &String) -> Value {
        validate_suite(path).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct EvaluationSuiteError(String);

impl std::fmt::Display for EvaluationSuiteError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for EvaluationSuiteError {}

pub fn build_generate_argv(options: &Value) -> Value {
    let mut argv = base("generate");
    flag(&mut argv, "--agent", options.get("agent"));
    flag(
        &mut argv,
        "--gen-instruction",
        options.get("genInstruction"),
    );
    flag(
        &mut argv,
        "--gen-instruction-file",
        options.get("genInstructionFile"),
    );
    flag(&mut argv, "--eval-model", options.get("evalModel"));
    flag(&mut argv, "--max-samples", options.get("maxSamples"));
    for evaluator in options
        .get("evaluators")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        flag(&mut argv, "--evaluator", Some(evaluator));
    }
    flag(&mut argv, "--dataset", options.get("dataset"));
    if let Some(value) = options.get("outFile") {
        flag(&mut argv, "--out-file", Some(value));
    } else {
        flag_str(&mut argv, "--out-file", DEFAULT_CONFIG);
    }
    flag(&mut argv, "--trace-days", options.get("traceDays"));
    flag(&mut argv, "--name", options.get("name"));
    flag(
        &mut argv,
        "--project-endpoint",
        options.get("projectEndpoint"),
    );
    bool_flag(&mut argv, "--reset-defaults", options.get("resetDefaults"));
    bool_flag(&mut argv, "--no-wait", options.get("noWait"));
    bool_flag(&mut argv, "--no-prompt", options.get("noPrompt"));
    Value::Array(argv.into_iter().map(Value::String).collect())
}

pub fn build_update_argv(options: &Value) -> Value {
    let mut argv = base("update");
    flag_or_default(&mut argv, "--config", options, "config");
    bool_flag(&mut argv, "--evaluator-only", options.get("evaluatorOnly"));
    bool_flag(&mut argv, "--dataset-only", options.get("datasetOnly"));
    bool_flag(&mut argv, "--no-prompt", options.get("noPrompt"));
    Value::Array(argv.into_iter().map(Value::String).collect())
}

pub fn build_run_argv(options: &Value) -> Value {
    let mut argv = base("run");
    flag_or_default(&mut argv, "--config", options, "config");
    flag(&mut argv, "--name", options.get("name"));
    bool_flag(&mut argv, "--no-wait", options.get("noWait"));
    bool_flag(&mut argv, "--no-prompt", options.get("noPrompt"));
    Value::Array(argv.into_iter().map(Value::String).collect())
}

pub fn read_rubric(value: &Value) -> Value {
    let items = value
        .get("dimensions")
        .and_then(Value::as_array)
        .or_else(|| value.as_array());
    let Some(items) = items else {
        return json!([]);
    };

    Value::Array(
        items
            .iter()
            .filter_map(|item| {
                let object = item.as_object()?;
                let id = python_str_or_empty(
                    object
                        .get("id")
                        .filter(|value| python_truthy(value))
                        .or_else(|| object.get("name").filter(|value| python_truthy(value))),
                );
                let description = python_str_or_empty(
                    object
                        .get("description")
                        .filter(|value| python_truthy(value)),
                );
                let weight = object
                    .get("weight")
                    .filter(|value| value.is_number())
                    .cloned()
                    .unwrap_or(Value::Null);
                let always_applicable = object
                    .get("always_applicable")
                    .map(python_truthy)
                    .unwrap_or(false);
                Some(json!({
                    "id": id,
                    "description": description,
                    "weight": weight,
                    "always_applicable": always_applicable,
                }))
            })
            .collect(),
    )
}

pub fn load_suite(path: impl AsRef<Path>) -> Result<Value, EvaluationSuiteError> {
    let path = path.as_ref();
    let data = read_yaml_value(path)?;
    let object = data.as_object();
    let evaluators = object
        .and_then(|object| object.get("evaluators"))
        .and_then(Value::as_array)
        .map(|items| items.iter().map(parse_evaluator).collect())
        .unwrap_or_else(Vec::new);

    let mut datasets = Vec::new();
    if let Some(object) = object {
        for role in ["dataset", "validation_dataset"] {
            if let Some(dataset) = object
                .get(role)
                .and_then(|value| parse_dataset(role, value))
            {
                datasets.push(dataset);
            }
        }
    }

    Ok(json!({
        "path": path.to_string_lossy(),
        "name": object.and_then(|object| object.get("name")).cloned().unwrap_or(Value::Null),
        "agent": object_field_or_empty(object, "agent"),
        "evaluators": evaluators,
        "datasets": datasets,
        "options": object_field_or_empty(object, "options"),
    }))
}

pub fn validate_suite(path: impl AsRef<Path>) -> Result<Value, EvaluationSuiteError> {
    let suite = load_suite(&path)?;
    let suite_path = PathBuf::from(
        suite
            .get("path")
            .and_then(Value::as_str)
            .unwrap_or(DEFAULT_CONFIG),
    );
    let root = suite_path.parent().unwrap_or_else(|| Path::new(""));
    let mut problems = Vec::<String>::new();
    let mut notes = Vec::<String>::new();
    let mut rubrics = Vec::<Value>::new();
    let evaluators = suite
        .get("evaluators")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    if evaluators.is_empty() {
        notes.push(
            "no evaluators declared -- run 'python -m castia eval generate' to synthesize a rubric, or add builtin.<name>"
                .to_string(),
        );
    }

    for evaluator in evaluators {
        let name = evaluator
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let local_uri = evaluator.get("local_uri").and_then(Value::as_str);
        let builtin = evaluator
            .get("builtin")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let Some(local_uri) = local_uri.filter(|value| !value.is_empty()) else {
            if !builtin && name.is_empty() {
                problems.push("an evaluator entry has neither a name nor a local_uri".to_string());
            }
            continue;
        };

        let label = if name.is_empty() { local_uri } else { name };
        let rubric_path = root.join(local_uri);
        if !rubric_path.exists() {
            problems.push(format!(
                "evaluator '{}': rubric dimensions file '{}' does not exist",
                label, local_uri
            ));
            continue;
        }

        let dims = match read_rubric_file(&rubric_path) {
            Ok(dims) => dims,
            Err(error) => {
                problems.push(format!(
                    "evaluator '{}': cannot read rubric '{}': {}",
                    label, local_uri, error
                ));
                continue;
            }
        };
        let Some(dimensions) = dims.as_array() else {
            continue;
        };
        if dimensions.is_empty() {
            problems.push(format!(
                "evaluator '{}': rubric '{}' has no 'dimensions'",
                label, local_uri
            ));
            continue;
        }
        upsert_rubric_summary(&mut rubrics, label, dimensions.len());

        for (index, dimension) in dimensions.iter().enumerate() {
            let id = dimension
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let description = dimension
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if id.is_empty() {
                problems.push(format!(
                    "evaluator '{}': dimension #{} has no id",
                    label,
                    index + 1
                ));
            }
            if description.is_empty() {
                let dim_label = if id.is_empty() {
                    format!("#{}", index + 1)
                } else {
                    id.to_string()
                };
                problems.push(format!(
                    "evaluator '{}': dimension '{}' has no description",
                    label, dim_label
                ));
            }
        }
    }

    for dataset in suite
        .get("datasets")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
    {
        let role = dataset
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if let Some(local_uri) = dataset
            .get("local_uri")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            if !root.join(local_uri).exists() {
                problems.push(format!(
                    "{}: local_uri '{}' does not exist",
                    role, local_uri
                ));
            }
        }
    }

    Ok(json!({
        "ok": problems.is_empty(),
        "problems": problems,
        "notes": notes,
        "rubrics": rubrics,
    }))
}

pub fn read_rubric_file(path: impl AsRef<Path>) -> Result<Value, EvaluationSuiteError> {
    Ok(read_rubric(&read_yaml_value(path.as_ref())?))
}

fn base(verb: &str) -> Vec<String> {
    AZD_EVAL
        .into_iter()
        .chain([verb])
        .map(str::to_string)
        .collect()
}

fn read_yaml_value(path: &Path) -> Result<Value, EvaluationSuiteError> {
    let text = fs::read_to_string(path).map_err(|error| EvaluationSuiteError(error.to_string()))?;
    let yaml: serde_yaml::Value =
        serde_yaml::from_str(&text).map_err(|error| EvaluationSuiteError(error.to_string()))?;
    serde_json::to_value(yaml).map_err(|error| EvaluationSuiteError(error.to_string()))
}

fn upsert_rubric_summary(rubrics: &mut Vec<Value>, name: &str, count: usize) {
    if let Some(existing) = rubrics
        .iter_mut()
        .find(|rubric| rubric.get("name").and_then(Value::as_str) == Some(name))
    {
        *existing = json!({ "name": name, "count": count });
    } else {
        rubrics.push(json!({ "name": name, "count": count }));
    }
}

fn parse_evaluator(entry: &Value) -> Value {
    if let Some(name) = entry.as_str() {
        return json!({
            "name": name,
            "kind": null,
            "local_uri": null,
            "version": null,
            "builtin": name.starts_with("builtin."),
        });
    }
    let Some(object) = entry.as_object() else {
        let name = python_str(entry);
        return json!({
            "name": name,
            "kind": null,
            "local_uri": null,
            "version": null,
            "builtin": name.starts_with("builtin."),
        });
    };
    let name = python_str_or_empty(
        object
            .get("name")
            .filter(|value| python_truthy(value))
            .or_else(|| {
                object
                    .get("evaluator_name")
                    .filter(|value| python_truthy(value))
            }),
    );
    json!({
        "name": name,
        "kind": option_python_str(object.get("kind").filter(|value| python_truthy(value)).or_else(|| object.get("type").filter(|value| python_truthy(value)))),
        "local_uri": option_python_str(object.get("local_uri")),
        "version": option_python_str(object.get("version")),
        "builtin": name.starts_with("builtin."),
    })
}

fn parse_dataset(role: &str, block: &Value) -> Option<Value> {
    let object = block.as_object()?;
    Some(json!({
        "role": role,
        "local_uri": option_python_str(object.get("local_uri")),
        "dataset_file": option_python_str(object.get("dataset_file")),
        "version": option_python_str(object.get("version")),
    }))
}

fn option_python_str(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Null) | None => Value::Null,
        Some(value) => Value::String(python_str(value)),
    }
}

fn object_field_or_empty(object: Option<&serde_json::Map<String, Value>>, key: &str) -> Value {
    object
        .and_then(|object| object.get(key))
        .and_then(|value| value.as_object().map(|_| value.clone()))
        .unwrap_or_else(|| json!({}))
}

fn flag(argv: &mut Vec<String>, name: &str, value: Option<&Value>) {
    let Some(value) = value else {
        return;
    };
    if value.is_null() {
        return;
    }

    if matches!(value, Value::String(value) if value.is_empty()) {
        return;
    }
    let value = python_str(value);
    argv.push(name.to_string());
    argv.push(value);
}

fn flag_or_default(argv: &mut Vec<String>, name: &str, options: &Value, key: &str) {
    if let Some(value) = options.get(key) {
        flag(argv, name, Some(value));
    } else {
        flag_str(argv, name, DEFAULT_CONFIG);
    }
}

fn flag_str(argv: &mut Vec<String>, name: &str, value: &str) {
    if !value.is_empty() {
        argv.push(name.to_string());
        argv.push(value.to_string());
    }
}

fn bool_flag(argv: &mut Vec<String>, name: &str, value: Option<&Value>) {
    if value.map(python_truthy).unwrap_or(false) {
        argv.push(name.to_string());
    }
}

fn python_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().map(|n| n != 0.0).unwrap_or(true),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

fn python_str_or_empty(value: Option<&Value>) -> String {
    value.map(python_str).unwrap_or_default()
}

fn python_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Array(_) | Value::Object(_) => value.to_string(),
    }
}

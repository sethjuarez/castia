use crate::model::EvaluationSuiteRuntime;
use serde_json::{json, Value};

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
}

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

fn base(verb: &str) -> Vec<String> {
    AZD_EVAL
        .into_iter()
        .chain([verb])
        .map(str::to_string)
        .collect()
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

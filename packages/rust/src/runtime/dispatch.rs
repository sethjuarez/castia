use crate::model::RuntimeDispatchRuntime;
use serde_json::{json, Value};

#[derive(Debug, Clone)]
pub struct DispatchError(String);

impl std::fmt::Display for DispatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DispatchError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRuntimeDispatchRuntime;

#[async_trait::async_trait]
impl RuntimeDispatchRuntime for CastiaRuntimeDispatchRuntime {
    fn activity_dispatch_plan(&self, parameters: &Value, text: &String) -> Value {
        activity_dispatch_plan(parameters, text)
    }

    fn activity_result_text(&self, result: &Value) -> Option<String> {
        activity_result_text(result)
    }

    fn invoke_dispatch_plan(&self, parameters: &Value, value: &Value) -> Value {
        invoke_dispatch_plan(parameters, value)
    }

    fn invoke_result_body(&self, result: &Value) -> Value {
        invoke_result_body(result)
    }

    fn return_body(&self, result: &Value) -> String {
        return_body(result)
    }

    fn stream_chunks(&self, result: &Value) -> Vec<String> {
        stream_chunks(result)
    }

    fn wire_dispatch_plan(
        &self,
        handler_name: &String,
        parameters: &Value,
        text: &String,
        streaming: &bool,
    ) -> Value {
        wire_dispatch_plan(handler_name, parameters, text, *streaming)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn activity_dispatch_plan(parameters: &Value, text: &str) -> Value {
    let params = parameter_list(parameters);
    let wants_text = params
        .iter()
        .any(|param| source_for_activity(param) == "text");
    if wants_text && text.is_empty() {
        return json!({"skip": true, "injections": []});
    }
    json!({
        "skip": false,
        "injections": params.iter().map(|param| injection(param, source_for_activity(param), Some(Value::String(text.to_string())))).collect::<Vec<_>>(),
    })
}

pub fn invoke_dispatch_plan(parameters: &Value, value: &Value) -> Value {
    let params = parameter_list(parameters);
    json!({
        "injections": params.iter().map(|param| injection(param, source_for_invoke(param), Some(value.clone()))).collect::<Vec<_>>(),
    })
}

pub fn wire_dispatch_plan(
    handler_name: &str,
    parameters: &Value,
    text: &str,
    streaming: bool,
) -> Result<Value, DispatchError> {
    let params = parameter_list(parameters);
    for param in &params {
        if matches!(param.annotation.as_deref(), Some("Activity" | "Message")) {
            let annotation = param.annotation.as_deref().unwrap_or_default();
            let mode = if streaming {
                "a streaming wire handler must take the input text and Depends(...) only."
            } else {
                "a handler served over @app.responses(), @app.chat(), or @app.invocations() must take the input text and Depends(...) only."
            };
            return Err(DispatchError(format!(
                "handler '{handler_name}' asks for {annotation}, which only exists on the Activity Protocol; {mode}"
            )));
        }
    }
    Ok(json!({
        "injections": params.iter().map(|param| injection(param, source_for_wire(param), Some(Value::String(text.to_string())))).collect::<Vec<_>>(),
    }))
}

pub fn activity_result_text(result: &Value) -> Option<String> {
    result
        .as_str()
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

pub fn invoke_result_body(result: &Value) -> Value {
    if result.as_object().is_some() {
        result.clone()
    } else {
        Value::Null
    }
}

pub fn return_body(result: &Value) -> String {
    result.as_str().unwrap_or_default().to_string()
}

pub fn stream_chunks(result: &Value) -> Vec<String> {
    if let Some(text) = result.as_str().filter(|text| !text.is_empty()) {
        return vec![text.to_string()];
    }
    result
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|text| !text.is_empty())
        .map(str::to_string)
        .collect()
}

#[derive(Debug, Clone)]
struct Parameter {
    name: String,
    annotation: Option<String>,
    default_kind: Option<String>,
}

fn parameter_list(value: &Value) -> Vec<Parameter> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let object = item.as_object()?;
            Some(Parameter {
                name: object
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                annotation: object
                    .get("annotation")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                default_kind: object
                    .get("defaultKind")
                    .or_else(|| object.get("default_kind"))
                    .and_then(Value::as_str)
                    .map(str::to_string),
            })
        })
        .collect()
}

fn source_for_activity(param: &Parameter) -> &'static str {
    if param.default_kind.as_deref() == Some("Depends") {
        "dependency"
    } else if param.annotation.as_deref() == Some("Activity") {
        "activity"
    } else if param.annotation.as_deref() == Some("Message") {
        "message"
    } else {
        "text"
    }
}

fn source_for_invoke(param: &Parameter) -> &'static str {
    if param.default_kind.as_deref() == Some("Depends") {
        "dependency"
    } else if param.annotation.as_deref() == Some("Activity") {
        "activity"
    } else if param.annotation.as_deref() == Some("Message") {
        "message"
    } else {
        "invoke_value"
    }
}

fn source_for_wire(param: &Parameter) -> &'static str {
    if param.default_kind.as_deref() == Some("Depends") {
        "dependency"
    } else {
        "text"
    }
}

fn injection(param: &Parameter, source: &str, value: Option<Value>) -> Value {
    let mut object = serde_json::Map::new();
    object.insert("name".to_string(), Value::String(param.name.clone()));
    object.insert("source".to_string(), Value::String(source.to_string()));
    match source {
        "text" => {
            object.insert("value".to_string(), value.unwrap_or_default());
        }
        "invoke_value" => {
            object.insert("value".to_string(), value.unwrap_or_default());
        }
        _ => {}
    }
    Value::Object(object)
}

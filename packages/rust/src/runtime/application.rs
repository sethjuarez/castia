use crate::model::RuntimeApplicationRuntime;
use serde_json::{json, Value};
use std::collections::BTreeSet;

#[derive(Debug, Clone)]
pub struct ApplicationError(String);

impl std::fmt::Display for ApplicationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ApplicationError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRuntimeApplicationRuntime;

#[async_trait::async_trait]
impl RuntimeApplicationRuntime for CastiaRuntimeApplicationRuntime {
    fn include_application_plan(
        &self,
        existing_tool_provider_count: &i32,
        incoming_tool_provider_count: &i32,
    ) -> Value {
        include_application_plan(*existing_tool_provider_count, *incoming_tool_provider_count)
    }

    fn registered_tools(&self, provider_outputs: &Value) -> Value {
        registered_tools(provider_outputs)
    }

    fn responses_only_application_projection(
        &self,
        name: &String,
        requested_name: &Option<String>,
        has_responses: &bool,
        has_responses_stream: &bool,
        registered_tools: &Value,
    ) -> Value {
        responses_only_application_projection(
            name,
            requested_name.as_deref(),
            *has_responses,
            *has_responses_stream,
            registered_tools,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn registered_tools(provider_outputs: &Value) -> Value {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for provider in provider_outputs.as_array().into_iter().flatten() {
        for tool in provider.as_array().into_iter().flatten() {
            let name = tool.get("name").and_then(Value::as_str);
            if let Some(name) = name {
                if !seen.insert(name.to_string()) {
                    continue;
                }
            }
            out.push(tool.clone());
        }
    }
    Value::Array(out)
}

pub fn include_application_plan(
    existing_tool_provider_count: i32,
    incoming_tool_provider_count: i32,
) -> Value {
    json!({
        "toolProviderCount": existing_tool_provider_count + incoming_tool_provider_count,
    })
}

pub fn responses_only_application_projection(
    name: &str,
    requested_name: Option<&str>,
    has_responses: bool,
    has_responses_stream: bool,
    registered_tools: &Value,
) -> Result<Value, ApplicationError> {
    if !has_responses {
        return Err(ApplicationError(
            "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting."
                .to_string(),
        ));
    }

    let projection_name = requested_name
        .filter(|value| !value.is_empty())
        .map(|value| Value::String(value.to_string()))
        .unwrap_or_else(|| {
            if name.is_empty() {
                Value::Null
            } else {
                Value::String(format!("{name}-optimize"))
            }
        });
    let mut wire_protocols = vec![Value::String("responses".to_string())];
    if has_responses_stream {
        wire_protocols.push(Value::String("responses_stream".to_string()));
    }

    Ok(json!({
        "name": projection_name,
        "protocols": ["responses"],
        "wireProtocols": wire_protocols,
        "tools": registered_tools.as_array().cloned().unwrap_or_default(),
    }))
}

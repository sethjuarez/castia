use crate::model::RuntimeRouterRuntime;
use serde_json::{json, Value};
use std::collections::BTreeSet;

const WIRE_ORDER: [&str; 3] = ["responses", "invocations", "chat"];

#[derive(Debug, Clone)]
pub struct RouterError(String);

impl std::fmt::Display for RouterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RouterError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRuntimeRouterRuntime;

#[async_trait::async_trait]
impl RuntimeRouterRuntime for CastiaRuntimeRouterRuntime {
    fn include_plan(
        &self,
        existing_wire: &Vec<String>,
        incoming_wire: &Vec<String>,
        existing_invokes: &Vec<String>,
        incoming_invokes: &Vec<String>,
    ) -> Value {
        include_plan(
            existing_wire,
            incoming_wire,
            existing_invokes,
            incoming_invokes,
        )
        .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn registered_protocols(
        &self,
        activity_route_count: &i32,
        invoke_names: &Vec<String>,
        wire_protocols: &Vec<String>,
    ) -> Vec<String> {
        registered_protocols(*activity_route_count, invoke_names, wire_protocols)
    }

    fn responses_only_projection(
        &self,
        name: &String,
        has_responses: &bool,
        tool_names: &Vec<String>,
    ) -> Value {
        responses_only_projection(name, *has_responses, tool_names)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn registered_protocols(
    activity_route_count: i32,
    invoke_names: &[String],
    wire_protocols: &[String],
) -> Vec<String> {
    let mut names = Vec::new();
    if activity_route_count > 0 || !invoke_names.is_empty() {
        names.push("activity".to_string());
    }
    for protocol in WIRE_ORDER {
        if wire_protocols
            .iter()
            .any(|registered| registered == protocol)
        {
            names.push(protocol.to_string());
        }
    }
    names
}

pub fn include_plan(
    existing_wire: &[String],
    incoming_wire: &[String],
    existing_invokes: &[String],
    incoming_invokes: &[String],
) -> Result<Value, RouterError> {
    for protocol in incoming_wire {
        if existing_wire.contains(protocol) {
            return Err(RouterError(format!(
                "protocol '{protocol}' already has a handler ('existing'); a wire protocol answers every caller, so it takes exactly one handler, but 'incoming' also registered @app.{protocol}(). Keep a single handler per wire protocol."
            )));
        }
    }
    for name in incoming_invokes {
        if existing_invokes.contains(name) {
            return Err(RouterError(format!(
                "invoke '{name}' already has a handler ('existing'); an invoke name is answered by exactly one handler, but 'incoming' also registered @app.invoke('{name}'). Keep a single handler per invoke name."
            )));
        }
    }
    Ok(json!({
        "ok": true,
        "wire": concat(existing_wire, incoming_wire),
        "invokes": concat(existing_invokes, incoming_invokes),
    }))
}

pub fn responses_only_projection(
    name: &str,
    has_responses: bool,
    tool_names: &[String],
) -> Result<Value, RouterError> {
    if !has_responses {
        return Err(RouterError(
            "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting."
                .to_string(),
        ));
    }
    let mut seen = BTreeSet::new();
    let mut tools = Vec::new();
    for tool in tool_names {
        if seen.insert(tool.clone()) {
            tools.push(tool.clone());
        }
    }
    Ok(json!({
        "name": if name.is_empty() { Value::Null } else { Value::String(format!("{name}-optimize")) },
        "protocols": ["responses"],
        "tools": tools,
    }))
}

fn concat(left: &[String], right: &[String]) -> Vec<String> {
    left.iter().chain(right.iter()).cloned().collect()
}

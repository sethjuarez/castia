use crate::model::ObserveTracingRuntime;
use serde_json::{json, Map, Value};

pub const PROVIDER: &str = "microsoft.foundry";
pub const TRACER_NAME: &str = "castia";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaObserveTracingRuntime;

#[async_trait::async_trait]
impl ObserveTracingRuntime for CastiaObserveTracingRuntime {
    fn invoke_agent_span(
        &self,
        name: &Option<String>,
        env_name: &Option<String>,
        env_version: &Option<String>,
        system: &Option<String>,
    ) -> Value {
        invoke_agent_span(
            name.as_deref(),
            env_name.as_deref(),
            env_version.as_deref(),
            system.as_deref(),
        )
    }

    fn execute_tool_span(&self, name: &String, system: &Option<String>) -> Value {
        execute_tool_span(name, system.as_deref())
    }
}

pub fn identity_attributes(name: &str, version: Option<&str>) -> Map<String, Value> {
    let mut attributes = Map::from_iter([(
        "gen_ai.agent.name".to_string(),
        Value::String(name.to_string()),
    )]);
    if let Some(version) = version.filter(|value| !value.is_empty()) {
        attributes.insert(
            "gen_ai.agent.id".to_string(),
            Value::String(format!("{name}:{version}")),
        );
        attributes.insert(
            "gen_ai.agent.version".to_string(),
            Value::String(version.to_string()),
        );
    }
    attributes
}

pub fn invoke_agent_span(
    name: Option<&str>,
    env_name: Option<&str>,
    env_version: Option<&str>,
    system: Option<&str>,
) -> Value {
    let agent_name = name
        .filter(|value| !value.is_empty())
        .or(env_name)
        .unwrap_or("agent");
    let system = system.unwrap_or(PROVIDER);
    let mut attributes = Map::from_iter([
        (
            "gen_ai.operation.name".to_string(),
            Value::String("invoke_agent".to_string()),
        ),
        (
            "gen_ai.system".to_string(),
            Value::String(system.to_string()),
        ),
        (
            "gen_ai.provider.name".to_string(),
            Value::String(system.to_string()),
        ),
    ]);
    attributes.extend(identity_attributes(agent_name, env_version));
    json!({
        "tracer_name": TRACER_NAME,
        "span_name": format!("invoke_agent {agent_name}"),
        "attributes": attributes,
    })
}

pub fn execute_tool_span(name: &str, system: Option<&str>) -> Value {
    let system = system.unwrap_or(PROVIDER);
    json!({
        "tracer_name": TRACER_NAME,
        "span_name": format!("execute_tool {name}"),
        "attributes": {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.system": system,
            "gen_ai.provider.name": system,
            "gen_ai.tool.name": name,
        },
    })
}

use crate::model::ModelRuntime;
use crate::optimizing::apply_optimized_toolbox_tools;
use serde_json::{json, Value};
use std::error::Error;
use std::fmt;

const PRIVATE_TOOL_PREFIX: &str = "x-castia-";
const REASONING_EFFORTS: [&str; 4] = ["minimal", "low", "medium", "high"];

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaModelRuntime;

#[async_trait::async_trait]
impl ModelRuntime for CastiaModelRuntime {
    fn instructions_param(&self, instructions: &Option<String>) -> Value {
        instructions_param(instructions.as_deref())
    }

    fn public_tool_spec(&self, spec: &Value, tool_definitions: &Value) -> Value {
        public_tool_spec(spec, tool_definitions)
    }

    fn reasoning_param(&self, effort: &Option<String>) -> Value {
        try_reasoning_param(effort.as_deref()).unwrap_or_else(|error| panic!("{error}"))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReasoningEffortError {
    message: String,
}

impl fmt::Display for ReasoningEffortError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for ReasoningEffortError {}

pub fn reasoning_param(effort: Option<&str>) -> Value {
    try_reasoning_param(effort).unwrap_or_else(|error| panic!("{error}"))
}

pub fn try_reasoning_param(effort: Option<&str>) -> Result<Value, ReasoningEffortError> {
    let Some(effort) = effort else {
        return Ok(json!({}));
    };
    let level = effort.trim().to_lowercase();
    if !REASONING_EFFORTS.contains(&level.as_str()) {
        return Err(ReasoningEffortError {
            message: format!(
                "reasoning_effort must be one of ('minimal', 'low', 'medium', 'high'), got '{}'",
                effort
            ),
        });
    }
    Ok(json!({ "reasoning": { "effort": level } }))
}

pub fn instructions_param(instructions: Option<&str>) -> Value {
    instructions
        .map(|instructions| json!({ "instructions": instructions }))
        .unwrap_or_else(|| json!({}))
}

pub fn public_tool_spec(spec: &Value, tool_definitions: &Value) -> Value {
    let optimized = apply_optimized_toolbox_tools(spec, tool_definitions);
    let Some(object) = optimized.as_object() else {
        return optimized;
    };
    Value::Object(
        object
            .iter()
            .filter(|(key, _)| !key.starts_with(PRIVATE_TOOL_PREFIX))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}

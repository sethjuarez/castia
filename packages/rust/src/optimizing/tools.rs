use serde_json::{Map, Value};

pub const OPTIMIZER_TOOL_DEFINITIONS_KEY: &str = "x-castia-optimizer-tool-definitions";
const SERVER_DESCRIPTION_KEY: &str = "x-castia-server-description";

pub fn tools_json(tools: &[Value]) -> Vec<Value> {
    tools
        .iter()
        .flat_map(|tool| {
            tool.get(OPTIMIZER_TOOL_DEFINITIONS_KEY)
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_else(|| function_definition(tool).into_iter().collect())
        })
        .collect()
}

pub fn apply_optimized_tool_definitions(tools: &[Value], definitions: &Value) -> Vec<Value> {
    let lookup = definition_lookup(definitions);
    if lookup.is_empty() {
        return tools.to_vec();
    }

    tools
        .iter()
        .map(|tool| {
            apply_toolbox_sidecar(tool, &lookup).unwrap_or_else(|| {
                apply_function_tool(tool, &lookup).unwrap_or_else(|| tool.clone())
            })
        })
        .collect()
}

fn function_definition(tool: &Value) -> Option<Value> {
    let func = if let Some(func) = tool.get("function").and_then(Value::as_object) {
        Value::Object(func.clone())
    } else if tool.get("name").is_some() {
        let mut func = tool.as_object()?.clone();
        func.remove("type");
        Value::Object(func)
    } else {
        return None;
    };
    Some(serde_json::json!({ "type": "function", "function": func }))
}

fn definition_lookup(definitions: &Value) -> Map<String, Value> {
    let mut lookup = Map::new();
    let Some(items) = definitions.as_array() else {
        return lookup;
    };

    for item in items {
        let Some(func) = function_payload(item) else {
            continue;
        };
        if let Some(name) = func.get("name").and_then(Value::as_str) {
            lookup.insert(name.to_string(), Value::Object(func.clone()));
        }
    }
    lookup
}

fn function_payload(value: &Value) -> Option<&Map<String, Value>> {
    value
        .get("function")
        .and_then(Value::as_object)
        .or_else(|| value.as_object().filter(|object| object.contains_key("name")))
}

fn apply_function_tool(tool: &Value, lookup: &Map<String, Value>) -> Option<Value> {
    let func = function_payload(tool)?;
    let optimized = lookup.get(func.get("name")?.as_str()?)?.as_object()?;
    let updated = apply_function_payload(func, optimized)?;

    if tool.get("function").is_some() {
        let mut out = tool.as_object()?.clone();
        out.insert("function".to_string(), Value::Object(updated));
        Some(Value::Object(out))
    } else {
        Some(Value::Object(updated))
    }
}

fn apply_toolbox_sidecar(tool: &Value, lookup: &Map<String, Value>) -> Option<Value> {
    let sidecar = tool
        .get(OPTIMIZER_TOOL_DEFINITIONS_KEY)
        .and_then(Value::as_array)?;
    if sidecar.is_empty() {
        return None;
    }

    let mut changed = false;
    let rewritten: Vec<Value> = sidecar
        .iter()
        .map(|item| {
            let Some(func) = item.get("function").and_then(Value::as_object) else {
                return item.clone();
            };
            let Some(name) = func.get("name").and_then(Value::as_str) else {
                return item.clone();
            };
            let Some(optimized) = lookup.get(name).and_then(Value::as_object) else {
                return item.clone();
            };
            let Some(updated_func) = apply_function_payload(func, optimized) else {
                return item.clone();
            };
            changed = true;
            let mut updated_item = item.as_object().cloned().unwrap_or_default();
            updated_item.insert("function".to_string(), Value::Object(updated_func));
            Value::Object(updated_item)
        })
        .collect();

    if !changed {
        return None;
    }

    let mut out = tool.as_object()?.clone();
    out.insert(
        OPTIMIZER_TOOL_DEFINITIONS_KEY.to_string(),
        Value::Array(rewritten.clone()),
    );
    let base = tool.get(SERVER_DESCRIPTION_KEY).and_then(Value::as_str);
    out.insert(
        "server_description".to_string(),
        Value::String(server_description(base, &rewritten)),
    );
    Some(Value::Object(out))
}

fn apply_function_payload(
    current: &Map<String, Value>,
    optimized: &Map<String, Value>,
) -> Option<Map<String, Value>> {
    let mut changed = false;
    let mut out = current.clone();

    if let Some(description) = optimized.get("description").and_then(Value::as_str) {
        if current.get("description").and_then(Value::as_str) != Some(description) {
            out.insert(
                "description".to_string(),
                Value::String(description.to_string()),
            );
            changed = true;
        }
    }

    if let Some(parameters) =
        merge_parameter_descriptions(current.get("parameters"), optimized.get("parameters"))
    {
        out.insert("parameters".to_string(), parameters);
        changed = true;
    }

    changed.then_some(out)
}

fn merge_parameter_descriptions(current: Option<&Value>, optimized: Option<&Value>) -> Option<Value> {
    let current = current?.as_object()?;
    let optimized = optimized?.as_object()?;
    let current_props = current.get("properties")?.as_object()?;
    let optimized_props = optimized.get("properties")?.as_object()?;

    let mut changed = false;
    let mut merged_props = Map::new();
    for (name, schema) in current_props {
        let mut merged_schema = schema.clone();
        if let (Some(schema), Some(description)) = (
            merged_schema.as_object_mut(),
            optimized_props
                .get(name)
                .and_then(|schema| schema.get("description"))
                .and_then(Value::as_str),
        ) {
            if schema.get("description").and_then(Value::as_str) != Some(description) {
                schema.insert(
                    "description".to_string(),
                    Value::String(description.to_string()),
                );
                changed = true;
            }
        }
        merged_props.insert(name.clone(), merged_schema);
    }

    if !changed {
        return None;
    }
    let mut merged = current.clone();
    merged.insert("properties".to_string(), Value::Object(merged_props));
    Some(Value::Object(merged))
}

fn server_description(base: Option<&str>, tool_definitions: &[Value]) -> String {
    let mut lines = Vec::new();
    if let Some(base) = base.filter(|value| !value.is_empty()) {
        lines.push(base.to_string());
    }
    if !tool_definitions.is_empty() {
        lines.push("Local tool guidance:".to_string());
        for item in tool_definitions {
            let Some(func) = item.get("function").and_then(Value::as_object) else {
                continue;
            };
            let name = func.get("name").and_then(Value::as_str).unwrap_or_default();
            let description = func
                .get("description")
                .and_then(Value::as_str)
                .unwrap_or_default();
            lines.push(format!("- {name}: {description}"));
            if let Some(props) = func
                .get("parameters")
                .and_then(|value| value.get("properties"))
                .and_then(Value::as_object)
            {
                for (name, schema) in props {
                    if let Some(description) = schema.get("description").and_then(Value::as_str) {
                        lines.push(format!("  - {name}: {description}"));
                    }
                }
            }
        }
    }
    lines.join("\n")
}

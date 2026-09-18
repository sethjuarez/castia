use crate::model::IntegrationsToolboxRuntime;
use crate::optimizing::OPTIMIZER_TOOL_DEFINITIONS_KEY;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

pub const AI_FOUNDRY_SCOPE: &str = "https://ai.azure.com/.default";
pub const SERVER_DESCRIPTION_KEY: &str = "x-castia-server-description";
const API_VERSION: &str = "v1";
const FEDERATED_NAME_SEPARATOR: &str = "___";

#[derive(Debug, Clone)]
pub struct ToolboxError(String);

impl std::fmt::Display for ToolboxError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ToolboxError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaIntegrationsToolboxRuntime;

#[async_trait::async_trait]
impl IntegrationsToolboxRuntime for CastiaIntegrationsToolboxRuntime {
    fn compose_toolbox_endpoint(
        &self,
        project_endpoint: &String,
        name: &String,
        version: &Option<String>,
    ) -> String {
        compose_toolbox_endpoint(project_endpoint, name, version.as_deref())
    }

    fn knowledge_base_mcp_tool(&self, config: &Value) -> Value {
        knowledge_base_mcp_tool(config).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn platform_endpoint_env(&self, name: &String) -> String {
        platform_endpoint_env(name)
    }

    fn resolve_toolbox_endpoint(&self, env: &Value) -> Option<String> {
        resolve_toolbox_endpoint(env)
    }

    fn toolbox_mcp_tool(&self, config: &Value) -> Value {
        toolbox_mcp_tool(config).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

pub fn platform_endpoint_env(name: &str) -> String {
    static NON_ALNUM: OnceLock<Regex> = OnceLock::new();
    let slug = NON_ALNUM
        .get_or_init(|| Regex::new(r"[^0-9A-Za-z]+").expect("valid regex"))
        .replace_all(name, "_")
        .trim_matches('_')
        .to_ascii_uppercase();
    format!("TOOLBOX_{slug}_MCP_ENDPOINT")
}

pub fn compose_toolbox_endpoint(
    project_endpoint: &str,
    name: &str,
    version: Option<&str>,
) -> String {
    let base = project_endpoint.trim_end_matches('/');
    let path = if let Some(version) = version.filter(|value| !value.is_empty()) {
        format!("/toolboxes/{name}/versions/{version}/mcp")
    } else {
        format!("/toolboxes/{name}/mcp")
    };
    format!("{base}{path}?api-version={API_VERSION}")
}

pub fn resolve_toolbox_endpoint(env: &Value) -> Option<String> {
    let env = env.as_object()?;
    for key in ["TOOLBOX_ENDPOINT", "TOOLBOX_MCP_ENDPOINT"] {
        if let Some(url) = env
            .get(key)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            return Some(url.to_string());
        }
    }
    let name = env
        .get("TOOLBOX_NAME")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())?;
    let platform = platform_endpoint_env(name);
    if let Some(url) = env
        .get(&platform)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        return Some(url.to_string());
    }
    env.get("FOUNDRY_PROJECT_ENDPOINT")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(|project| {
            compose_toolbox_endpoint(
                project,
                name,
                env.get("TOOLBOX_VERSION").and_then(Value::as_str),
            )
        })
}

pub fn toolbox_mcp_tool(config: &Value) -> Result<Value, ToolboxError> {
    let object = config.as_object().cloned().unwrap_or_default();
    let allowed_tools = string_array(
        object
            .get("allowed_tools")
            .or_else(|| object.get("allowedTools")),
    )?;
    let descriptions = object
        .get("descriptions")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let param_guidance = object
        .get("param_guidance")
        .or_else(|| object.get("paramGuidance"))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let server_label = object
        .get("server_label")
        .or_else(|| object.get("serverLabel"))
        .and_then(Value::as_str)
        .unwrap_or("toolbox");
    let override_defs =
        optimizer_tool_definitions(server_label, &allowed_tools, &descriptions, &param_guidance)?;
    let endpoint = object
        .get("endpoint")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| resolve_toolbox_endpoint(object.get("env").unwrap_or(&Value::Null)));
    let Some(endpoint) = endpoint.filter(|value| !value.is_empty()) else {
        return Ok(Value::Null);
    };
    let mut spec = Map::new();
    spec.insert("type".to_string(), Value::String("mcp".to_string()));
    spec.insert(
        "server_label".to_string(),
        Value::String(server_label.to_string()),
    );
    spec.insert("server_url".to_string(), Value::String(endpoint));
    spec.insert(
        "require_approval".to_string(),
        Value::String(
            object
                .get("require_approval")
                .or_else(|| object.get("requireApproval"))
                .and_then(Value::as_str)
                .unwrap_or("never")
                .to_string(),
        ),
    );
    if !allowed_tools.is_empty() {
        spec.insert("allowed_tools".to_string(), strings(&allowed_tools));
    }
    let base_description = object
        .get("server_description")
        .or_else(|| object.get("serverDescription"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty());
    if base_description.is_some() || !override_defs.is_empty() {
        if let Some(base) = base_description {
            spec.insert(
                SERVER_DESCRIPTION_KEY.to_string(),
                Value::String(base.to_string()),
            );
        }
        spec.insert(
            "server_description".to_string(),
            Value::String(server_description(base_description, &override_defs)),
        );
    }
    if !override_defs.is_empty() {
        spec.insert(
            OPTIMIZER_TOOL_DEFINITIONS_KEY.to_string(),
            Value::Array(override_defs),
        );
    }
    if let Some(connection) = object
        .get("project_connection_id")
        .or_else(|| object.get("projectConnectionId"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        spec.insert(
            "project_connection_id".to_string(),
            Value::String(connection.to_string()),
        );
    }
    let mut headers = object
        .get("headers")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(token) = object
        .get("token")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        let mut merged = Map::new();
        merged.insert(
            "Authorization".to_string(),
            Value::String(format!("Bearer {token}")),
        );
        merged.extend(headers);
        headers = merged;
    }
    if !headers.is_empty() {
        spec.insert("headers".to_string(), Value::Object(headers));
    }
    Ok(Value::Object(spec))
}

pub fn knowledge_base_mcp_tool(config: &Value) -> Result<Value, ToolboxError> {
    let object = config.as_object().cloned().unwrap_or_default();
    let endpoint = object
        .get("endpoint")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| ToolboxError("endpoint is required".to_string()))?;
    let mut forwarded = object.clone();
    forwarded.insert("endpoint".to_string(), Value::String(endpoint.to_string()));
    forwarded.insert(
        "server_label".to_string(),
        Value::String(
            object
                .get("server_label")
                .or_else(|| object.get("serverLabel"))
                .and_then(Value::as_str)
                .unwrap_or("knowledge-base")
                .to_string(),
        ),
    );
    if !forwarded.contains_key("allowed_tools") && !forwarded.contains_key("allowedTools") {
        forwarded.insert(
            "allowed_tools".to_string(),
            json!(["knowledge_base_retrieve"]),
        );
    }
    let mut headers = object
        .get("headers")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if let Some(token) = object
        .get("search_token")
        .or_else(|| object.get("searchToken"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        headers.insert(
            "x-ms-query-source-authorization".to_string(),
            Value::String(token.to_string()),
        );
    }
    if !headers.is_empty() {
        forwarded.insert("headers".to_string(), Value::Object(headers));
    }
    toolbox_mcp_tool(&Value::Object(forwarded))
}

fn optimizer_tool_definitions(
    server_label: &str,
    allowed_tools: &[String],
    descriptions: &Map<String, Value>,
    param_guidance: &Map<String, Value>,
) -> Result<Vec<Value>, ToolboxError> {
    if descriptions.is_empty() && param_guidance.is_empty() {
        return Ok(Vec::new());
    }
    if allowed_tools.is_empty() {
        return Err(ToolboxError(
            "allowed_tools is required when toolbox descriptions or param_guidance are provided; castia cannot validate override names without a selected toolbox tool list".to_string(),
        ));
    }
    let selected = allowed_tools
        .iter()
        .map(|name| SelectedTool::new(server_label, name))
        .collect::<Vec<_>>();
    let resolved_descriptions = resolve_overrides("descriptions", descriptions, &selected)?;
    let resolved_params = resolve_overrides("param_guidance", param_guidance, &selected)?;
    let mut out = Vec::new();
    for tool in selected {
        let has_description_override = resolved_descriptions.contains_key(&tool.final_name);
        let description = resolved_descriptions
            .get(&tool.final_name)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        let params = resolved_params
            .get(&tool.final_name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        if !has_description_override && params.is_empty() {
            continue;
        }
        out.push(json!({
            "type": "function",
            "function": {
                "name": tool.final_name,
                "description": description.map(str::to_string).unwrap_or_else(|| format!("Call the federated toolbox tool '{}'.", tool.upstream_name)),
                "parameters": parameter_schema(&params),
            },
        }));
    }
    Ok(out)
}

#[derive(Clone)]
struct SelectedTool {
    upstream_name: String,
    final_name: String,
    legacy_labeled_name: String,
    bare_name: String,
}

impl SelectedTool {
    fn new(server_label: &str, upstream_name: &str) -> Self {
        Self {
            upstream_name: upstream_name.to_string(),
            final_name: upstream_name.to_string(),
            legacy_labeled_name: format!("{server_label}{FEDERATED_NAME_SEPARATOR}{upstream_name}"),
            bare_name: upstream_name
                .rsplit(FEDERATED_NAME_SEPARATOR)
                .next()
                .unwrap_or(upstream_name)
                .to_string(),
        }
    }

    fn matches(&self, key: &str) -> bool {
        key == self.final_name
            || key == self.upstream_name
            || key == self.legacy_labeled_name
            || key == self.bare_name
    }
}

fn resolve_overrides(
    label: &str,
    overrides: &Map<String, Value>,
    selected: &[SelectedTool],
) -> Result<Map<String, Value>, ToolboxError> {
    let mut resolved = Map::new();
    for (key, value) in overrides {
        let matches = selected
            .iter()
            .filter(|tool| tool.matches(key))
            .collect::<Vec<_>>();
        if matches.is_empty() {
            let allowed = selected
                .iter()
                .map(|tool| tool.final_name.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(ToolboxError(format!(
                "unknown toolbox {label} key '{key}'; expected one of: {allowed}"
            )));
        }
        if matches.len() > 1 {
            let choices = matches
                .iter()
                .map(|tool| tool.final_name.as_str())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(ToolboxError(format!(
                "ambiguous toolbox {label} key '{key}'; matches: {choices}"
            )));
        }
        resolved.insert(matches[0].final_name.clone(), value.clone());
    }
    Ok(resolved)
}

fn parameter_schema(guidance: &Map<String, Value>) -> Value {
    let properties = guidance
        .iter()
        .filter_map(|(name, description)| {
            description
                .as_str()
                .map(|description| (name.clone(), json!({"description": description})))
        })
        .collect::<Map<_, _>>();
    json!({
        "type": "object",
        "properties": properties,
        "required": [],
        "additionalProperties": true,
    })
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

fn string_array(value: Option<&Value>) -> Result<Vec<String>, ToolboxError> {
    match value {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| {
                item.as_str().map(str::to_string).ok_or_else(|| {
                    ToolboxError("allowed_tools must be a list of strings".to_string())
                })
            })
            .collect(),
        _ => Err(ToolboxError(
            "allowed_tools must be a list of strings".to_string(),
        )),
    }
}

fn strings(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

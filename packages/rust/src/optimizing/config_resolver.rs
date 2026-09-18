use crate::model::{AgentConfigResolution, AgentConfigResolver};
use serde_json::Value;
use std::env;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

const DEFAULT_MODEL: &str = "gpt-4o";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaAgentConfigResolver;

#[async_trait::async_trait]
impl AgentConfigResolver for CastiaAgentConfigResolver {
    async fn resolve(
        &self,
        config_dir: &Option<String>,
        cancellation: &AtomicBool,
    ) -> Result<AgentConfigResolution, Box<dyn Error + Send + Sync>> {
        if cancellation.load(Ordering::SeqCst) {
            return Err("agent config resolution cancelled".into());
        }

        Ok(self.resolve_best_effort(config_dir.as_deref()))
    }
}

impl CastiaAgentConfigResolver {
    pub fn resolve_best_effort(&self, config_dir: Option<&str>) -> AgentConfigResolution {
        if let Some(config) = env::var("OPTIMIZATION_CONFIG")
            .ok()
            .filter(|value| !value.trim().is_empty())
        {
            return self.resolve_inline(&config).unwrap_or_else(|| {
                eprintln!("invalid OPTIMIZATION_CONFIG; using env defaults");
                self.default_resolution()
            });
        }

        if env::var("OPTIMIZATION_CANDIDATE_ID")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .is_some()
            && env::var("OPTIMIZATION_RESOLVE_ENDPOINT")
                .ok()
                .filter(|value| !value.trim().is_empty())
                .is_some()
        {
            eprintln!(
                "OPTIMIZATION_RESOLVE_ENDPOINT is configured, but Rust resolver API support is not implemented; using env defaults"
            );
            return AgentConfigResolution {
                source: "unsupported:resolver_api".to_string(),
                model: Some(default_model()),
                instructions: None,
                tool_definitions: Value::Null,
            };
        }

        self.resolve_local(config_dir).unwrap_or_else(|| {
            eprintln!("no local optimizer config resolved; using env defaults");
            self.default_resolution()
        })
    }

    fn resolve_inline(&self, raw: &str) -> Option<AgentConfigResolution> {
        let value: Value = serde_json::from_str(raw).ok()?;
        let model = value
            .get("model")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .or_else(|| Some(default_model()));
        let instructions = value
            .get("instructions")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        Some(AgentConfigResolution {
            source: "env:OPTIMIZATION_CONFIG".to_string(),
            model,
            instructions,
            tool_definitions: normalized_tool_definitions(&value),
        })
    }

    fn resolve_local(&self, config_dir: Option<&str>) -> Option<AgentConfigResolution> {
        let root = local_config_root(config_dir)?;
        let candidate_dir = select_candidate_dir(&root)?;
        let metadata_path = candidate_dir.join("metadata.yaml");
        let metadata: Value =
            serde_yaml::from_str(&fs::read_to_string(&metadata_path).ok()?).ok()?;

        let instructions = metadata_string(&metadata, "instruction_file")
            .and_then(|instruction_file| read_relative(&candidate_dir, &instruction_file))
            .map(|base| compose_skills(base, &candidate_dir, &metadata))
            .filter(|value| !value.is_empty());
        let tool_definitions = ["tool_file", "tools_file"]
            .iter()
            .filter_map(|key| metadata_string(&metadata, key))
            .filter_map(|tool_file| read_relative(&candidate_dir, &tool_file))
            .filter_map(|raw| serde_json::from_str::<Value>(&raw).ok())
            .find(Value::is_array)
            .unwrap_or(Value::Null);

        Some(AgentConfigResolution {
            source: format!("local:{}", candidate_dir.display()),
            model: metadata_string(&metadata, "model").or_else(|| Some(default_model())),
            instructions,
            tool_definitions,
        })
    }

    fn default_resolution(&self) -> AgentConfigResolution {
        AgentConfigResolution {
            source: "default".to_string(),
            model: Some(default_model()),
            instructions: None,
            tool_definitions: Value::Null,
        }
    }
}

fn default_model() -> String {
    env::var("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.to_string())
}

fn normalized_tool_definitions(value: &Value) -> Value {
    value
        .get("toolDefinitions")
        .or_else(|| value.get("tool_definitions"))
        .filter(|value| value.is_array())
        .cloned()
        .unwrap_or(Value::Null)
}

fn local_config_root(config_dir: Option<&str>) -> Option<PathBuf> {
    if let Some(config_dir) = config_dir.filter(|value| !value.trim().is_empty()) {
        return Some(PathBuf::from(config_dir));
    }
    if let Ok(local_dir) = env::var("OPTIMIZATION_LOCAL_DIR") {
        if !local_dir.trim().is_empty() {
            return Some(PathBuf::from(local_dir));
        }
    }
    Some(PathBuf::from(".agent_configs"))
}

fn select_candidate_dir(root: &Path) -> Option<PathBuf> {
    if let Ok(candidate_id) = env::var("OPTIMIZATION_CANDIDATE_ID") {
        if !candidate_id.trim().is_empty() {
            return [
                root.join(&candidate_id),
                root.join(".agent_configs").join(&candidate_id),
            ]
            .into_iter()
            .find(|candidate| candidate.join("metadata.yaml").is_file());
        }
    }
    [
        root.to_path_buf(),
        root.join("baseline"),
        root.join(".agent_configs").join("baseline"),
    ]
    .into_iter()
    .find(|candidate| candidate.join("metadata.yaml").is_file())
}

fn metadata_string(metadata: &Value, key: &str) -> Option<String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn read_relative(base: &Path, file: &str) -> Option<String> {
    let path = PathBuf::from(file);
    let path = if path.is_absolute() {
        path
    } else {
        base.join(path)
    };
    fs::read_to_string(path).ok()
}

fn compose_skills(mut instructions: String, candidate_dir: &Path, metadata: &Value) -> String {
    let Some(skill_dir) = metadata_string(metadata, "skill_dir") else {
        return instructions;
    };
    let skill_root = {
        let path = PathBuf::from(skill_dir);
        if path.is_absolute() {
            path
        } else {
            candidate_dir.join(path)
        }
    };
    let mut skill_files: Vec<PathBuf> = match fs::read_dir(skill_root) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .map(|entry| entry.path().join("SKILL.md"))
            .filter(|path| path.is_file())
            .collect(),
        Err(_) => Vec::new(),
    };
    skill_files.sort();

    for skill_file in skill_files {
        if let Ok(skill) = fs::read_to_string(skill_file) {
            if !skill.trim().is_empty() {
                if !instructions.ends_with("\n\n") {
                    instructions.push_str("\n\n");
                }
                instructions.push_str(skill.trim());
            }
        }
    }
    instructions
}

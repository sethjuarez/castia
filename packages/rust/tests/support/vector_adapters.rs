use castia::model::{
    Activity, ActivityRuntime, AgentConfigResolver, ChatRuntime, InvocationsRuntime, LoadContext,
    ResponsesRuntime, SaveContext,
};
use castia::optimizing::CastiaAgentConfigResolver;
use castia::protocols::{
    CastiaActivityRuntime, CastiaChatRuntime, CastiaInvocationsRuntime, CastiaResponsesRuntime,
};
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::{Mutex, OnceLock};

static OPTIMIZER_ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
const OPTIMIZER_ENV: &[&str] = &[
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "OPTIMIZATION_CONFIG",
    "OPTIMIZATION_CANDIDATE_ID",
    "OPTIMIZATION_RESOLVE_ENDPOINT",
    "OPTIMIZATION_LOCAL_DIR",
];

pub struct Context {
    pub contract: String,
    pub operation: String,
    pub vector: Value,
    pub provider: Option<String>,
    pub target_api: Option<String>,
    pub doubles: Value,
    pub base_dir: String,
}

pub type SyncInvoke = fn(&Value, &Context) -> Result<Value, VectorError>;
pub type AsyncInvoke = Box<
    dyn Fn(&Value, &Context) -> Pin<Box<dyn Future<Output = Result<Value, VectorError>> + Send>>
        + Send
        + Sync,
>;
pub type Normalize = fn(&Value, &Context) -> Value;

pub enum Invoke {
    Sync(SyncInvoke),
    Async(AsyncInvoke),
}

pub struct Adapter {
    pub invoke: Invoke,
    pub normalize: Option<Normalize>,
}

pub struct VectorError {
    pub message: String,
    pub payload: Option<Value>,
}

pub fn adapters() -> HashMap<&'static str, Adapter> {
    HashMap::from([
        ("ActivityRuntime.agenticInstanceId", sync(agentic_instance_id)),
        ("ActivityRuntime.agenticTenantId", sync(agentic_tenant_id)),
        ("ActivityRuntime.agenticUser", sync(agentic_user)),
        ("ActivityRuntime.channel", sync(channel)),
        ("ActivityRuntime.isAgenticRequest", sync(is_agentic_request)),
        ("ActivityRuntime.mentions", sync(mentions)),
        (
            "AgentConfigResolver.resolve",
            sync_with_normalize(agent_config_resolve, normalize_agent_config_source),
        ),
        ("ChatRuntime.body", sync_with_normalize(chat_body, normalize_generated_id)),
        ("ChatRuntime.lastUserText", sync(chat_last_user_text)),
        ("InvocationsRuntime.body", sync(invocations_body)),
        ("InvocationsRuntime.inputText", sync(invocations_input_text)),
        ("ResponsesRuntime.inputText", sync(responses_input_text)),
        (
            "ResponsesRuntime.outputBody",
            sync_with_normalize(responses_output_body, normalize_generated_id),
        ),
    ])
}

pub fn waivers() -> HashMap<&'static str, &'static str> {
    HashMap::new()
}

pub fn doubles() -> Value {
    Value::Null
}

fn sync(invoke: SyncInvoke) -> Adapter {
    Adapter {
        invoke: Invoke::Sync(invoke),
        normalize: None,
    }
}

fn sync_with_normalize(invoke: SyncInvoke, normalize: Normalize) -> Adapter {
    Adapter {
        invoke: Invoke::Sync(invoke),
        normalize: Some(normalize),
    }
}

fn activity(input: &Value) -> Result<Activity, VectorError> {
    let Some(value) = input.get("activity") else {
        return Err(VectorError {
            message: "missing activity input".to_string(),
            payload: None,
        });
    };
    Activity::try_load_from_value(value, &LoadContext::default()).map_err(|error| VectorError {
        message: error.to_string(),
        payload: None,
    })
}

fn agentic_instance_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.agentic_instance_id(&activity)
    ))
}

fn agentic_tenant_id(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.agentic_tenant_id(&activity)
    ))
}

fn agentic_user(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(CastiaActivityRuntime.agentic_user(&activity)))
}

fn channel(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(CastiaActivityRuntime.channel(&activity)))
}

fn is_agentic_request(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    Ok(serde_json::json!(
        CastiaActivityRuntime.is_agentic_request(&activity)
    ))
}

fn mentions(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let activity = activity(input)?;
    let mentions = CastiaActivityRuntime.mentions(&activity);
    Ok(Value::Array(
        mentions
            .iter()
            .map(|mention| mention.to_value(&SaveContext::default()))
            .collect(),
    ))
}

fn agent_config_resolve(input: &Value, ctx: &Context) -> Result<Value, VectorError> {
    let _guard = OPTIMIZER_ENV_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let saved_env = save_optimizer_env();
    let temp = temp_dir(&ctx.operation);
    let result = (|| {
        apply_vector_env(&ctx.vector);
        write_vector_files(&temp, &ctx.vector)?;
        let config_dir = input
            .get("configDir")
            .and_then(Value::as_str)
            .map(|value| {
                if value == "$temp" {
                    temp.to_string_lossy().to_string()
                } else {
                    value.to_string()
                }
            });
        let resolution = CastiaAgentConfigResolver.resolve_best_effort(config_dir.as_deref());
        Ok(resolution.to_value(&SaveContext::default()))
    })();
    restore_optimizer_env(saved_env);
    let _ = fs::remove_dir_all(temp);
    result
}

fn responses_input_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(
        CastiaResponsesRuntime.input_text(input.get("value").unwrap_or(&Value::Null))
    ))
}

fn responses_output_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input.get("text").and_then(Value::as_str).unwrap_or_default();
    Ok(CastiaResponsesRuntime.output_body(&text.to_string()))
}

fn chat_last_user_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(
        CastiaChatRuntime.last_user_text(input.get("messages").unwrap_or(&Value::Null))
    ))
}

fn chat_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input.get("text").and_then(Value::as_str).unwrap_or_default();
    Ok(CastiaChatRuntime.body(&text.to_string()))
}

fn invocations_input_text(input: &Value, _: &Context) -> Result<Value, VectorError> {
    Ok(serde_json::json!(
        CastiaInvocationsRuntime.input_text(input.get("body").unwrap_or(&Value::Null))
    ))
}

fn invocations_body(input: &Value, _: &Context) -> Result<Value, VectorError> {
    let text = input.get("text").and_then(Value::as_str).unwrap_or_default();
    Ok(CastiaInvocationsRuntime.body(&text.to_string()))
}

fn normalize_generated_id(value: &Value, _: &Context) -> Value {
    let mut value = value.clone();
    if let Some(object) = value.as_object_mut() {
        object.remove("id");
    }
    value
}

fn normalize_agent_config_source(value: &Value, _: &Context) -> Value {
    let mut value = value.clone();
    if let Some(object) = value.as_object_mut() {
        if let Some(source) = object
            .get("source")
            .and_then(Value::as_str)
            .map(str::to_string)
        {
            if let Some(last) = source.rsplit(['/', '\\']).next() {
                if source.starts_with("local:") {
                    object.insert("source".to_string(), Value::String(format!("local:{last}")));
                }
            }
        }
    }
    value
}

fn save_optimizer_env() -> Vec<(&'static str, Option<String>)> {
    OPTIMIZER_ENV
        .iter()
        .map(|key| (*key, env::var(key).ok()))
        .collect()
}

fn restore_optimizer_env(saved: Vec<(&'static str, Option<String>)>) {
    for key in OPTIMIZER_ENV {
        env::remove_var(key);
    }
    for (key, value) in saved {
        if let Some(value) = value {
            env::set_var(key, value);
        }
    }
}

fn apply_vector_env(vector: &Value) {
    for key in OPTIMIZER_ENV {
        env::remove_var(key);
    }
    let Some(env_values) = vector.get("env").and_then(Value::as_object) else {
        return;
    };
    for (key, value) in env_values {
        if let Some(value) = value.as_str() {
            env::set_var(key, value);
        }
    }
}

fn write_vector_files(root: &Path, vector: &Value) -> Result<(), VectorError> {
    let Some(files) = vector.get("files").and_then(Value::as_array) else {
        return Ok(());
    };
    for file in files {
        let Some(relative) = file.get("path").and_then(Value::as_str) else {
            continue;
        };
        let Some(contents) = file.get("content").and_then(Value::as_str) else {
            continue;
        };
        let path = root.join(relative);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(io_error)?;
        }
        fs::write(path, contents).map_err(io_error)?;
    }
    Ok(())
}

fn temp_dir(label: &str) -> PathBuf {
    let root = env::temp_dir().join(format!(
        "castia-vector-{}-{}",
        label.replace(|c: char| !c.is_ascii_alphanumeric(), "_"),
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).expect("vector temp dir created");
    root
}

fn io_error(error: std::io::Error) -> VectorError {
    VectorError {
        message: error.to_string(),
        payload: None,
    }
}

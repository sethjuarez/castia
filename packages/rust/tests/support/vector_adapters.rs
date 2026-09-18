use castia::model::{Activity, ActivityRuntime, LoadContext, SaveContext};
use castia::protocols::CastiaActivityRuntime;
use serde_json::Value;
use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;

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

use crate::model::HostingAuthRuntime;
use serde_json::Value;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaHostingAuthRuntime;

#[async_trait::async_trait]
impl HostingAuthRuntime for CastiaHostingAuthRuntime {
    fn local_run(&self, env: &Value) -> bool {
        local_run(env)
    }
}

pub fn local_run(env: &Value) -> bool {
    if env
        .get("AGENT_DIGITAL_WORKER")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .is_some()
    {
        return true;
    }
    env.get("FOUNDRY_AGENT_TENANT_ID").is_none()
}

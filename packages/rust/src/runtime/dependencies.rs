use crate::model::RuntimeDependenciesRuntime;
use serde_json::{json, Value};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRuntimeDependenciesRuntime;

#[async_trait::async_trait]
impl RuntimeDependenciesRuntime for CastiaRuntimeDependenciesRuntime {
    fn depends_marker(&self, dependency_name: &String, use_cache: &bool) -> Value {
        depends_marker(dependency_name, *use_cache)
    }

    fn resolve_dependency_plan(
        &self,
        cache_keys: &Vec<String>,
        dependency_name: &String,
        use_cache: &bool,
    ) -> Value {
        resolve_dependency_plan(cache_keys, dependency_name, *use_cache)
    }
}

pub fn depends_marker(dependency_name: &str, use_cache: bool) -> Value {
    json!({
        "kind": "Depends",
        "dependency": dependency_name,
        "useCache": use_cache,
    })
}

pub fn resolve_dependency_plan(
    cache_keys: &[String],
    dependency_name: &str,
    _use_cache: bool,
) -> Value {
    if cache_keys.iter().any(|key| key == dependency_name) {
        json!({"action": "cache_hit", "store": false})
    } else {
        json!({"action": "call_dependency", "store": true})
    }
}

use crate::model::DeliveryManifestRuntime;
use serde_json::{json, Map, Value};

const PUBLISHABLE_PROTOCOLS: [&str; 3] = ["activity", "responses", "invocations"];

#[derive(Debug, Clone)]
pub struct ManifestError(String);

impl std::fmt::Display for ManifestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ManifestError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaDeliveryManifestRuntime;

#[async_trait::async_trait]
impl DeliveryManifestRuntime for CastiaDeliveryManifestRuntime {
    fn plan_manifest(
        &self,
        app_name: &String,
        registered_protocols: &Vec<String>,
        manifest: &Value,
        check: &bool,
    ) -> Value {
        plan_manifest(app_name, registered_protocols, manifest, *check)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn publishable_protocols(&self, registered_protocols: &Vec<String>) -> Vec<String> {
        publishable_protocols(registered_protocols)
    }

    fn service_protocol_items(&self, protocols: &Vec<String>) -> Value {
        service_protocol_items(protocols)
    }

    fn skipped_protocols(&self, registered_protocols: &Vec<String>) -> Vec<String> {
        skipped_protocols(registered_protocols)
    }
}

pub fn publishable_protocols(registered: &[String]) -> Vec<String> {
    registered
        .iter()
        .filter(|protocol| PUBLISHABLE_PROTOCOLS.contains(&protocol.as_str()))
        .cloned()
        .collect()
}

pub fn skipped_protocols(registered: &[String]) -> Vec<String> {
    registered
        .iter()
        .filter(|protocol| !PUBLISHABLE_PROTOCOLS.contains(&protocol.as_str()))
        .cloned()
        .collect()
}

pub fn service_protocol_items(protocols: &[String]) -> Value {
    Value::Array(
        protocols
            .iter()
            .map(|protocol| json!({"protocol": protocol, "version": "2.0.0"}))
            .collect(),
    )
}

pub fn plan_manifest(
    app_name: &str,
    registered_protocols: &[String],
    manifest: &Value,
    check: bool,
) -> Result<Value, ManifestError> {
    let services = manifest
        .get("services")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let service_name = pick_service(&services, app_name)?;
    let service = services
        .get(&service_name)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let desired = publishable_protocols(registered_protocols);
    let skipped = skipped_protocols(registered_protocols);
    let mut notes = Vec::new();
    if !skipped.is_empty() {
        notes.push(format!(
            "local-only protocol(s) not published to Foundry: {}",
            skipped.join(", ")
        ));
    }
    let before_service = protocol_names(service.get("protocols"));
    let endpoint = service.get("agentEndpoint").and_then(Value::as_object);
    let (before_endpoint, after_endpoint) = if let Some(endpoint) = endpoint {
        if endpoint.contains_key("protocols") {
            let before = protocol_strings(endpoint.get("protocols"));
            if !desired.iter().any(|protocol| protocol == "activity") && !before.is_empty() {
                notes.push(
                    "agentEndpoint present but 'activity' is not registered; review whether the Bot Service endpoint should remain"
                        .to_string(),
                );
            }
            (
                Value::Array(before.into_iter().map(Value::String).collect()),
                strings(&desired),
            )
        } else {
            (Value::Null, Value::Null)
        }
    } else {
        (Value::Null, Value::Null)
    };
    let before_endpoint_vec = before_endpoint.as_array().map(|items| {
        items
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect::<Vec<_>>()
    });
    let changed = before_service != desired
        || before_endpoint_vec
            .as_ref()
            .is_some_and(|before| before != &desired);
    let mut notes_value = notes.iter().cloned().map(Value::String).collect::<Vec<_>>();
    if !check && changed && before_endpoint_vec.is_some() && desired.iter().any(|p| p != "activity")
    {
        notes_value.push(Value::String(
            "authorizationSchemes left as authored; a non-activity protocol may need its own scheme -- review before deploy"
                .to_string(),
        ));
    }
    Ok(json!({
        "service": service_name,
        "desired": desired,
        "skipped": skipped,
        "before_service": before_service,
        "after_service": publishable_protocols(registered_protocols),
        "before_endpoint": before_endpoint,
        "after_endpoint": after_endpoint,
        "changed": changed,
        "written": !check && changed,
        "notes": notes_value,
    }))
}

fn pick_service(services: &Map<String, Value>, app_name: &str) -> Result<String, ManifestError> {
    if services.is_empty() {
        return Err(ManifestError("azure.yaml has no services".to_string()));
    }
    if services.contains_key(app_name) {
        return Ok(app_name.to_string());
    }
    if services.len() == 1 {
        return Ok(services.keys().next().expect("one service").to_string());
    }
    let names = services
        .keys()
        .map(|name| format!("'{name}'"))
        .collect::<Vec<_>>()
        .join(", ");
    Err(ManifestError(format!(
        "agent name '{app_name}' not among services [{names}]; cannot pick which service to update"
    )))
}

fn protocol_names(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.get("protocol")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect()
}

fn protocol_strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect()
}

fn strings(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

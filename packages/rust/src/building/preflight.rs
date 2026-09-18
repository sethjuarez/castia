use crate::model::BuildPreflightRuntime;
use regex::Regex;
use serde_json::{json, Value};
use std::sync::OnceLock;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaBuildPreflightRuntime;

#[async_trait::async_trait]
impl BuildPreflightRuntime for CastiaBuildPreflightRuntime {
    fn deployment_context_diagnostics(&self, environment: &Value, deployment: &bool) -> Value {
        deployment_context_diagnostics(environment, *deployment)
    }

    fn preflight_report(
        &self,
        root: &String,
        app_target: &String,
        protocols: &Vec<String>,
        diagnostics: &Value,
    ) -> Value {
        preflight_report(root, app_target, protocols, diagnostics)
    }

    fn required_env_diagnostics(&self, environment: &Value, required_env: &Vec<String>) -> Value {
        required_env_diagnostics(environment, required_env)
    }
}

pub fn preflight_report(
    root: &str,
    app_target: &str,
    protocols: &[String],
    diagnostics: &Value,
) -> Value {
    let (diagnostics, ok) = match diagnostics.as_array() {
        Some(rows) => {
            let ok = rows.iter().all(|item| {
                matches!(
                    item.get("status").and_then(Value::as_str),
                    Some("pass" | "warning" | "skipped")
                )
            });
            (rows.clone(), ok)
        }
        None => (Vec::new(), false),
    };
    json!({
        "root": root,
        "app_target": app_target,
        "ok": ok,
        "scope": "offline",
        "protocols": protocols,
        "diagnostics": diagnostics,
    })
}

pub fn required_env_diagnostics(environment: &Value, required_env: &[String]) -> Value {
    let environment = environment.as_object();
    let mut diagnostics = required_env
        .iter()
        .map(|key| {
            let present = environment
                .and_then(|env| env.get(key))
                .and_then(Value::as_str)
                .is_some_and(|value| !value.trim().is_empty());
            diagnostic(
                &format!("configuration.{key}"),
                if present { "pass" } else { "fail" },
                if present {
                    "Required setting is present."
                } else {
                    "Required setting is missing."
                },
            )
        })
        .collect::<Vec<_>>();
    diagnostics.push(diagnostic(
        "cloud",
        "skipped",
        "Credentials, RBAC, model availability and provisioning not checked.",
    ));
    Value::Array(diagnostics)
}

pub fn deployment_context_diagnostics(environment: &Value, deployment: bool) -> Value {
    let environment = environment.as_object();
    let mut diagnostics = Vec::new();
    for key in [
        "AZURE_LOCATION",
        "AZURE_AI_PROJECT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ] {
        let present = environment
            .and_then(|env| env.get(key))
            .and_then(Value::as_str)
            .is_some_and(|value| !value.trim().is_empty());
        diagnostics.push(diagnostic(
            &format!("azd.{key}"),
            if present {
                "pass"
            } else if deployment {
                "fail"
            } else {
                "warning"
            },
            if present {
                "Deployment setting is present; resource existence is not checked."
            } else {
                "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources."
            },
        ));
    }
    if let Some(project_id) = environment
        .and_then(|env| env.get("AZURE_AI_PROJECT_ID"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
    {
        static ARM_ID: OnceLock<Regex> = OnceLock::new();
        if !ARM_ID
            .get_or_init(|| {
                Regex::new(
                    r"(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+$",
                )
                .expect("valid regex")
            })
            .is_match(project_id.trim())
        {
            diagnostics.push(diagnostic(
                "azd.project_arm_id",
                if deployment { "fail" } else { "warning" },
                "AZURE_AI_PROJECT_ID must be the project ARM resource ID, not the project endpoint URL.",
            ));
        }
    }
    diagnostics.push(diagnostic(
        "azd.tenant",
        "skipped",
        "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication.",
    ));
    diagnostics.push(diagnostic(
        "azd.environment",
        "skipped",
        "Only supplied/process settings were checked; the selected azd environment was not read or modified.",
    ));
    Value::Array(diagnostics)
}

fn diagnostic(check: &str, status: &str, message: &str) -> Value {
    json!({
        "check": check,
        "status": status,
        "message": message,
    })
}

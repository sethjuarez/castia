//! Versioned `castia.cli.v1` JSON envelope for lifecycle commands run with `--json`.
//! Mirrors `castia.lifecycle.output` in the Python SDK.

use crate::model::LifecycleOutputRuntime;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::error::Error;
use std::fmt;
use std::sync::OnceLock;

pub const CLI_SCHEMA: &str = "castia.cli.v1";
pub const SEVERITIES: [&str; 3] = ["error", "warning", "info"];
pub const EFFECT_KEYS: [&str; 3] = ["network", "billable", "mutating"];
pub const NOT_OK_STATUSES: [&str; 5] = ["error", "failed", "problems", "cancelled", "canceled"];
pub const REDACTED: &str = "[REDACTED]";

const SECRET_KEY_EXACT: [&str; 7] = [
    "token",
    "authorization",
    "sig",
    "signature",
    "credential",
    "credentials",
    "sas",
];
const SECRET_KEY_SUFFIXES: [&str; 11] = [
    "secret",
    "password",
    "apikey",
    "accesskey",
    "accountkey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "bearertoken",
    "connectionstring",
    "sastoken",
];

#[derive(Debug, Clone)]
pub struct LifecycleOutputError(pub String);

impl fmt::Display for LifecycleOutputError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl Error for LifecycleOutputError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleOutputRuntime;

#[async_trait::async_trait]
impl LifecycleOutputRuntime for CastiaLifecycleOutputRuntime {
    fn command_envelope(
        &self,
        command: &String,
        status: &String,
        result: &Value,
        diagnostics: &Value,
        next_actions: &Vec<String>,
        effects: &Value,
    ) -> Value {
        command_envelope(command, status, result, diagnostics, next_actions, effects)
            .unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn redact_secrets(&self, value: &Value) -> Value {
        redact_secrets(value)
    }
}

pub fn is_secret_key(key: &str) -> bool {
    let normalized: String = key
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect();
    SECRET_KEY_EXACT.contains(&normalized.as_str())
        || SECRET_KEY_SUFFIXES
            .iter()
            .any(|suffix| normalized.ends_with(suffix))
}

fn secret_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        [
            r#"(?i)([?&]sig=)[^&\s"']+"#,
            r#"(?i)(AccountKey=)[^;\s"']+"#,
            r#"(?i)(SharedAccessSignature=)[^;\s"']+"#,
            r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+",
        ]
        .iter()
        .map(|p| Regex::new(p).expect("valid redaction pattern"))
        .collect()
    })
}

pub fn redact_secrets(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| {
                    let redacted = if is_secret_key(k) && !v.is_null() {
                        Value::String(REDACTED.to_string())
                    } else {
                        redact_secrets(v)
                    };
                    (k.clone(), redacted)
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(redact_secrets).collect()),
        Value::String(text) => {
            let mut text = text.clone();
            for pattern in secret_patterns() {
                text = pattern
                    .replace_all(&text, format!("${{1}}{REDACTED}").as_str())
                    .into_owned();
            }
            Value::String(text)
        }
        other => other.clone(),
    }
}

pub fn diagnostic(
    severity: &str,
    message: &str,
    code: Option<&str>,
) -> Result<Value, LifecycleOutputError> {
    if !SEVERITIES.contains(&severity) {
        return Err(LifecycleOutputError(format!(
            "diagnostic severity must be one of {}",
            SEVERITIES.join(", ")
        )));
    }
    Ok(json!({"severity": severity, "code": code, "message": message}))
}

fn text(value: Option<&Value>, default: &str) -> String {
    match value {
        None | Some(Value::Null) => default.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

fn normalize_diagnostics(diagnostics: &Value) -> Result<Vec<Value>, LifecycleOutputError> {
    let items = match diagnostics {
        Value::Null => return Ok(Vec::new()),
        Value::Array(items) => items,
        _ => {
            return Err(LifecycleOutputError(
                "diagnostics must be strings or objects".to_string(),
            ))
        }
    };
    items
        .iter()
        .map(|item| match item {
            Value::String(message) => diagnostic("error", message, None),
            Value::Object(map) => {
                let code = match map.get("code") {
                    None | Some(Value::Null) => None,
                    Some(code) => Some(text(Some(code), "")),
                };
                diagnostic(
                    &text(map.get("severity"), "error"),
                    &text(map.get("message"), ""),
                    code.as_deref(),
                )
            }
            _ => Err(LifecycleOutputError(
                "diagnostics must be strings or objects".to_string(),
            )),
        })
        .collect()
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn normalize_effects(effects: &Value) -> Result<Value, LifecycleOutputError> {
    let empty = Map::new();
    let map = match effects {
        Value::Null => &empty,
        Value::Object(map) => map,
        _ => {
            return Err(LifecycleOutputError(
                "effects must be an object".to_string(),
            ))
        }
    };
    let mut unknown: Vec<&str> = map
        .keys()
        .map(String::as_str)
        .filter(|k| !EFFECT_KEYS.contains(k))
        .collect();
    unknown.sort_unstable();
    if !unknown.is_empty() {
        return Err(LifecycleOutputError(format!(
            "unknown effect keys: {}",
            unknown.join(", ")
        )));
    }
    let mut normalized = Map::new();
    for key in EFFECT_KEYS {
        normalized.insert(
            key.to_string(),
            Value::Bool(map.get(key).is_some_and(truthy)),
        );
    }
    Ok(Value::Object(normalized))
}

pub fn command_envelope(
    command: &str,
    status: &str,
    result: &Value,
    diagnostics: &Value,
    next_actions: &[String],
    effects: &Value,
) -> Result<Value, LifecycleOutputError> {
    if command.is_empty() {
        return Err(LifecycleOutputError("command is required".to_string()));
    }
    if status.is_empty() {
        return Err(LifecycleOutputError("status is required".to_string()));
    }
    let normalized = normalize_diagnostics(diagnostics)?;
    let has_error = normalized.iter().any(|d| d["severity"] == "error");
    let ok = !NOT_OK_STATUSES.contains(&status.to_lowercase().as_str()) && !has_error;
    Ok(json!({
        "schema": CLI_SCHEMA,
        "command": command,
        "ok": ok,
        "status": status,
        "effects": normalize_effects(effects)?,
        "result": redact_secrets(result),
        "diagnostics": redact_secrets(&Value::Array(normalized)),
        "next": next_actions
            .iter()
            .map(|n| redact_secrets(&Value::String(n.clone())))
            .collect::<Vec<_>>(),
    }))
}

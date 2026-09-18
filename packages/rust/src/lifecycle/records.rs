use crate::model::LifecycleRecordsRuntime;
use regex::Regex;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleRecordsRuntime;

#[async_trait::async_trait]
impl LifecycleRecordsRuntime for CastiaLifecycleRecordsRuntime {
    fn canonical_json(&self, value: &Value) -> String {
        canonical_json(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn check_public(&self, value: &Value) -> bool {
        check_public(value).unwrap_or_else(|error| panic!("{}", error.0));
        true
    }

    fn content_hash(&self, value: &Value) -> String {
        content_hash(value).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn safe_path(&self, path: &String) -> String {
        safe_path(path).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleRecordsError(String);

impl std::fmt::Display for LifecycleRecordsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LifecycleRecordsError {}

pub fn canonical_json(value: &Value) -> Result<String, LifecycleRecordsError> {
    let normalized = normalize_json(value)?;
    serde_json::to_string(&normalized).map_err(|error| LifecycleRecordsError(error.to_string()))
}

pub fn content_hash(value: &Value) -> Result<String, LifecycleRecordsError> {
    let canonical = canonical_json(value)?;
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn safe_path(value: &str) -> Result<String, LifecycleRecordsError> {
    if value.trim().is_empty() {
        return Err(LifecycleRecordsError(
            "relative path must be nonempty text".to_string(),
        ));
    }
    let normalized = value.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    let has_windows_drive = value.len() >= 2 && value.as_bytes()[1] == b':';
    let invalid = normalized.starts_with('/')
        || value.starts_with('\\')
        || has_windows_drive
        || normalized.contains(':')
        || normalized.contains('\0')
        || parts
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == "..")
        || parts
            .iter()
            .any(|part| part.ends_with(' ') || part.ends_with('.'))
        || parts.iter().any(|part| {
            let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
            matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                || (stem.len() == 4
                    && (stem.starts_with("COM") || stem.starts_with("LPT"))
                    && stem[3..].parse::<u8>().is_ok_and(|n| (1..=9).contains(&n)))
        });
    if invalid {
        return Err(path_error());
    }
    Ok(normalized)
}

pub fn check_public(value: &Value) -> Result<(), LifecycleRecordsError> {
    match value {
        Value::Object(object) => {
            for (key, item) in object {
                if secret_key(key) && !placeholder(item) {
                    return Err(LifecycleRecordsError(
                        "secret-bearing configuration key is not evidence".to_string(),
                    ));
                }
                check_public(item)?;
            }
        }
        Value::Array(items) => {
            for item in items {
                check_public(item)?;
            }
        }
        Value::String(value) => {
            if secret_value().is_match(value) {
                return Err(LifecycleRecordsError(
                    "credential-shaped content is not evidence".to_string(),
                ));
            }
            let mut offset = 0;
            while offset < value.len() {
                let Some(captures) = assignment().captures_at(value, offset) else {
                    break;
                };
                let Some(matched) = captures.get(0) else {
                    break;
                };
                let key = captures.get(1).map(|m| m.as_str()).unwrap_or_default();
                let item = captures.get(2).map(|m| m.as_str()).unwrap_or_default();
                if secret_key(key) {
                    let literal = item
                        .trim()
                        .trim_end_matches('}')
                        .trim()
                        .trim_matches(['"', '\'']);
                    if !matches!(literal.to_ascii_lowercase().as_str(), "null" | "none" | "~")
                        && !placeholder(&Value::String(literal.to_string()))
                        && !placeholder(&Value::String(
                            item.trim().trim_matches(['"', '\'']).to_string(),
                        ))
                    {
                        return Err(LifecycleRecordsError(
                            "secret-bearing configuration assignment is not evidence".to_string(),
                        ));
                    }
                }
                offset = next_char_boundary(value, matched.start() + 1);
            }
        }
        _ => {}
    }
    Ok(())
}

fn normalize_json(value: &Value) -> Result<Value, LifecycleRecordsError> {
    match value {
        Value::Array(items) => Ok(Value::Array(
            items
                .iter()
                .map(normalize_json)
                .collect::<Result<Vec<_>, _>>()?,
        )),
        Value::Object(object) => {
            let mut normalized = Map::new();
            let mut keys: Vec<&String> = object.keys().collect();
            keys.sort();
            for key in keys {
                normalized.insert(key.clone(), normalize_json(&object[key])?);
            }
            Ok(Value::Object(normalized))
        }
        Value::Null | Value::Bool(_) | Value::String(_) | Value::Number(_) => Ok(value.clone()),
    }
}

fn path_error() -> LifecycleRecordsError {
    LifecycleRecordsError("path must be a normalized relative file path".to_string())
}

fn secret_key(key: &str) -> bool {
    let separated = camel_boundary().replace_all(key, "${1}_${2}");
    let normalized: String = key
        .to_ascii_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect();
    secret_key_regex().is_match(&separated.replace(['.', ' '], "_"))
        || matches!(
            normalized.as_str(),
            "apikey"
                | "accesstoken"
                | "clientsecret"
                | "connectionstring"
                | "accountkey"
                | "sharedaccesssignature"
                | "credentials"
        )
}

fn next_char_boundary(value: &str, start: usize) -> usize {
    let mut offset = start.min(value.len());
    while offset < value.len() && !value.is_char_boundary(offset) {
        offset += 1;
    }
    offset
}

fn placeholder(value: &Value) -> bool {
    matches!(value, Value::Null)
        || value
            .as_str()
            .is_some_and(|value| value.is_empty() || placeholder_regex().is_match(value))
}

fn camel_boundary() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"([a-z0-9])([A-Z])").unwrap())
}

fn secret_key_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)(^|[_-])(password|passwd|secret|token|api[_-]?key|authorization|credential)($|[_-])",
        )
        .unwrap()
    })
}

fn secret_value() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)-----BEGIN .*PRIVATE KEY-----|Bearer\s+\S+|(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})|https?://[^/\s:@]+:[^/\s@]+@",
        )
        .unwrap()
    })
}

fn assignment() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r#"(?im)(?:^|[,{;#\n])\s*["']?([A-Za-z][A-Za-z0-9_. -]*?)["']?\s*[:=]\s*([^\n,]+)"#,
        )
        .unwrap()
    })
}

fn placeholder_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^(?:\$\{[A-Z_][A-Z0-9_]*\}|<redacted>|\[redacted\])$").unwrap()
    })
}

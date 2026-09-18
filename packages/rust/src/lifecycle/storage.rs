use crate::model::LifecycleStorageRuntime;
use serde_json::{Map, Value};
use std::fs;
use std::io::{ErrorKind, Write};
use std::path::Path;
use uuid::Uuid;

use super::records::{
    canonical_json, normalize_record, record_id, reject_json_duplicate_keys, LifecycleRecordsError,
};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaLifecycleStorageRuntime;

#[async_trait::async_trait]
impl LifecycleStorageRuntime for CastiaLifecycleStorageRuntime {
    fn put_artifact(&self, root: &String, record: &Value) -> String {
        put_artifact(root, record).unwrap_or_else(|error| panic!("{}", error.0))
    }

    fn get_artifact(&self, root: &String, id: &String) -> Value {
        get_artifact(root, id).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleStorageError(String);

impl std::fmt::Display for LifecycleStorageError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for LifecycleStorageError {}

impl From<LifecycleRecordsError> for LifecycleStorageError {
    fn from(value: LifecycleRecordsError) -> Self {
        Self(value.to_string())
    }
}

pub fn put_artifact(
    root: impl AsRef<Path>,
    record: &Value,
) -> Result<String, LifecycleStorageError> {
    let root = root.as_ref();
    fs::create_dir_all(root).map_err(cannot_read)?;
    let validated = normalize_record(record)?;
    let identity = record_id(&validated)
        .map_err(|_| LifecycleStorageError("invalid artifact content".to_string()))?;
    let payload = object([
        ("id", Value::String(identity.clone())),
        ("record", validated),
    ]);
    immutable_write(&root.join(format!("{identity}.json")), &payload)?;
    get_artifact(root, &identity)?;
    Ok(identity)
}

pub fn get_artifact(
    root: impl AsRef<Path>,
    identity: &str,
) -> Result<Value, LifecycleStorageError> {
    validate_identity(identity)?;
    let payload = read_json(&root.as_ref().join(format!("{identity}.json")))?;
    if payload.len() != 2
        || payload.get("id").and_then(Value::as_str) != Some(identity)
        || !payload.contains_key("record")
    {
        return Err(LifecycleStorageError(
            "artifact envelope identity mismatch".to_string(),
        ));
    }
    let record = normalize_record(payload.get("record").unwrap())
        .map_err(|_| LifecycleStorageError("invalid artifact content".to_string()))?;
    if record_id(&record)
        .map_err(|_| LifecycleStorageError("invalid artifact content".to_string()))?
        != identity
    {
        return Err(LifecycleStorageError(
            "artifact content hash mismatch".to_string(),
        ));
    }
    Ok(record)
}

fn read_json(path: &Path) -> Result<Map<String, Value>, LifecycleStorageError> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| cannot_read("cannot read evidence JSON"))?;
    if metadata.file_type().is_symlink() {
        return Err(LifecycleStorageError(
            "symlinked evidence is not allowed".to_string(),
        ));
    }
    let raw = fs::read_to_string(path).map_err(|_| cannot_read("cannot read evidence JSON"))?;
    reject_json_duplicate_keys(&raw).map_err(|_| cannot_read("cannot read evidence JSON"))?;
    let value: Value =
        serde_json::from_str(&raw).map_err(|_| cannot_read("cannot read evidence JSON"))?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| cannot_read("cannot read evidence JSON"))
}

fn immutable_write(path: &Path, payload: &Value) -> Result<(), LifecycleStorageError> {
    let Some(parent) = path.parent() else {
        return Err(cannot_read("cannot read evidence JSON"));
    };
    fs::create_dir_all(parent).map_err(cannot_read)?;
    let raw = format!("{}\n", canonical_json(payload)?).into_bytes();
    let staging = parent.join(format!(".write-{}", Uuid::new_v4().simple()));
    let write_result = (|| {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)
            .map_err(cannot_read)?;
        file.write_all(&raw).map_err(cannot_read)?;
        file.sync_all().map_err(cannot_read)?;
        match fs::hard_link(&staging, path) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == ErrorKind::AlreadyExists => {
                if fs::symlink_metadata(path)
                    .map_err(cannot_read)?
                    .file_type()
                    .is_symlink()
                    || fs::read(path).map_err(cannot_read)? != raw
                {
                    Err(LifecycleStorageError(
                        "immutable evidence already exists with different bytes".to_string(),
                    ))
                } else {
                    Ok(())
                }
            }
            Err(error) => Err(cannot_read(error)),
        }
    })();
    let _ = fs::remove_file(staging);
    write_result
}

fn validate_identity(identity: &str) -> Result<(), LifecycleStorageError> {
    if identity.len() == 64
        && identity
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    {
        return Ok(());
    }
    Err(LifecycleStorageError(
        "id must be a lowercase SHA-256 digest".to_string(),
    ))
}

fn object<const N: usize>(pairs: [(&str, Value); N]) -> Value {
    Value::Object(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

fn cannot_read(error: impl std::fmt::Display) -> LifecycleStorageError {
    LifecycleStorageError(error.to_string())
}

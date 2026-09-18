use crate::model::LifecycleStorageRuntime;
use serde_json::{json, Map, Value};
use std::fs;
use std::future::Future;
use std::io::{ErrorKind, Write};
use std::path::Path;
use uuid::Uuid;

use super::records::{
    canonical_json, content_hash, normalize_record, record_id, reject_json_duplicate_keys,
    LifecycleRecordsError,
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

    fn journal_read(&self, root: &String) -> Value {
        journal_read(root).unwrap_or_else(|error| panic!("{}", error.0))
    }
}

#[derive(Debug, Clone)]
pub struct LifecycleStorageError(String);

impl LifecycleStorageError {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

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

pub async fn journal_promote<Deploy, DeployFuture, Verify, VerifyFuture>(
    root: impl AsRef<Path>,
    candidate: &Value,
    decision: &Value,
    deploy: Deploy,
    verify: Verify,
    expected_revision: i64,
) -> Result<Value, LifecycleStorageError>
where
    Deploy: Fn(Value) -> DeployFuture,
    DeployFuture: Future<Output = Result<Value, LifecycleStorageError>>,
    Verify: Fn(Value, Value) -> VerifyFuture,
    VerifyFuture: Future<Output = Result<bool, LifecycleStorageError>>,
{
    let root = root.as_ref();
    let candidate = normalize_record(candidate)?;
    let decision = normalize_record(decision)?;
    accepted(&candidate, &decision)?;
    let _guard = JournalLock::new(root)?;
    let state = check_revision(root, expected_revision)?;
    if state.get("remote_state").and_then(Value::as_str) == Some("unknown") {
        return Err(LifecycleStorageError(
            "remote state is unknown; explicitly rollback or reconcile recovery before promotion"
                .to_string(),
        ));
    }
    let candidate_id = record_id(&candidate)?;
    if state.get("known_good").and_then(Value::as_str) == Some(candidate_id.as_str()) {
        return Err(LifecycleStorageError(
            "candidate is already the known-good deployment".to_string(),
        ));
    }
    if let Some(known_good) = state.get("known_good").and_then(Value::as_str) {
        let previous = get_artifact(root.join("artifacts"), known_good)?;
        if previous.get("kind").and_then(Value::as_str) != Some("candidate")
            || previous.get("agent_id").and_then(Value::as_str)
                != candidate.get("baseline_id").and_then(Value::as_str)
        {
            return Err(LifecycleStorageError(
                "candidate baseline must match known-good agent".to_string(),
            ));
        }
    }
    put_artifact(root.join("artifacts"), &candidate)?;
    let decision_id = put_artifact(root.join("artifacts"), &decision)?;
    handoff(
        root,
        &state,
        &candidate,
        "promote",
        Some(decision_id),
        deploy,
        verify,
    )
    .await
}

pub async fn journal_rollback<Deploy, DeployFuture, Verify, VerifyFuture>(
    root: impl AsRef<Path>,
    deploy: Deploy,
    verify: Verify,
    expected_revision: i64,
) -> Result<Value, LifecycleStorageError>
where
    Deploy: Fn(Value) -> DeployFuture,
    DeployFuture: Future<Output = Result<Value, LifecycleStorageError>>,
    Verify: Fn(Value, Value) -> VerifyFuture,
    VerifyFuture: Future<Output = Result<bool, LifecycleStorageError>>,
{
    let root = root.as_ref();
    let _guard = JournalLock::new(root)?;
    let state = check_revision(root, expected_revision)?;
    let target = if state.get("remote_state").and_then(Value::as_str) == Some("unknown") {
        state.get("known_good").and_then(Value::as_str)
    } else {
        state
            .get("previous_good")
            .and_then(Value::as_array)
            .and_then(|values| values.last())
            .and_then(Value::as_str)
    }
    .ok_or_else(|| {
        LifecycleStorageError("no previous verified deployment is available".to_string())
    })?;
    let candidate = get_artifact(root.join("artifacts"), target)?;
    if candidate.get("kind").and_then(Value::as_str) != Some("candidate") {
        return Err(LifecycleStorageError(
            "rollback reference must be a candidate".to_string(),
        ));
    }
    handoff(root, &state, &candidate, "rollback", None, deploy, verify).await
}

pub async fn journal_reconcile<Verify, VerifyFuture>(
    root: impl AsRef<Path>,
    verify: Verify,
    expected_revision: i64,
) -> Result<Value, LifecycleStorageError>
where
    Verify: Fn(Value) -> VerifyFuture,
    VerifyFuture: Future<Output = Result<Value, LifecycleStorageError>>,
{
    let root = root.as_ref();
    let _guard = JournalLock::new(root)?;
    integer(expected_revision, "expected_revision")?;
    let state = journal_read(root)?;
    if state.get("revision").and_then(Value::as_i64) != Some(expected_revision) {
        return Err(LifecycleStorageError("stale journal revision".to_string()));
    }
    if state.get("pending_cleanup").and_then(Value::as_bool) != Some(true) {
        return Err(LifecycleStorageError(
            "there is no outstanding handoff to reconcile".to_string(),
        ));
    }
    let prior = read_event(root, expected_revision)?;
    let evidence = reconciliation_evidence(&verify(state.clone()).await?)?;
    append_event(
        root,
        &state,
        "reconcile",
        prior
            .get("candidate_id")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        prior
            .get("decision_id")
            .and_then(Value::as_str)
            .map(str::to_string),
        "reconciled",
        Some(evidence),
    )
}

pub fn journal_read(root: impl AsRef<Path>) -> Result<Value, LifecycleStorageError> {
    let root = root.as_ref();
    fs::create_dir_all(root).map_err(cannot_read)?;
    fs::create_dir_all(root.join("artifacts")).map_err(cannot_read)?;
    let events = root.join("events");
    fs::create_dir_all(&events).map_err(cannot_read)?;
    if fs::symlink_metadata(&events)
        .map_err(cannot_read)?
        .file_type()
        .is_symlink()
    {
        return Err(LifecycleStorageError(
            "symlinked journal is not allowed".to_string(),
        ));
    }

    let mut state = journal_state(
        0,
        Value::Null,
        Vec::new(),
        Value::Null,
        Value::Null,
        "uninitialized",
        false,
    );
    let mut pending: Option<Value> = None;
    let mut last_event: Option<Value> = None;
    let mut paths = fs::read_dir(&events)
        .map_err(cannot_read)?
        .collect::<Result<Vec<_>, _>>()
        .map_err(cannot_read)?;
    paths.retain(|entry| entry.path().extension().and_then(|ext| ext.to_str()) == Some("json"));
    paths.sort_by_key(|entry| entry.path());
    for (index, entry) in paths.into_iter().enumerate() {
        let expected = format!("{:012}.json", index + 1);
        if entry.file_name().to_string_lossy() != expected {
            return Err(LifecycleStorageError(
                "journal sequence gap or unexpected path".to_string(),
            ));
        }
        let envelope = read_json(&entry.path())?;
        if envelope.len() != 2 || !envelope.contains_key("id") || !envelope.contains_key("event") {
            return Err(LifecycleStorageError(
                "invalid journal envelope".to_string(),
            ));
        }
        let event = envelope
            .get("event")
            .and_then(Value::as_object)
            .cloned()
            .ok_or_else(|| LifecycleStorageError("invalid journal event".to_string()))?;
        validate_event(
            &event,
            index as i64 + 1,
            &state,
            envelope.get("id").unwrap(),
        )?;
        let event_value = Value::Object(event.clone());
        if event.get("action").and_then(Value::as_str) == Some("reconcile") {
            if event.get("status").and_then(Value::as_str) != Some("reconciled")
                || state.get("pending_cleanup").and_then(Value::as_bool) != Some(true)
                || last_event.as_ref().is_none_or(|prior| {
                    prior.get("candidate_id") != event_value.get("candidate_id")
                        || prior.get("decision_id") != event_value.get("decision_id")
                })
            {
                return Err(LifecycleStorageError(
                    "reconciliation has no matching uncertain operation".to_string(),
                ));
            }
            reconciliation_evidence(event.get("reconciliation").unwrap_or(&Value::Null)).map_err(
                |_| LifecycleStorageError("invalid reconciliation evidence".to_string()),
            )?;
            state = journal_state(
                index as i64 + 1,
                state.get("known_good").cloned().unwrap_or(Value::Null),
                state
                    .get("previous_good")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default(),
                Value::String("reconciled".to_string()),
                envelope.get("id").cloned().unwrap_or(Value::Null),
                state
                    .get("remote_state")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown"),
                false,
            );
            pending = None;
            last_event = Some(event_value);
            continue;
        }
        if event.get("status").and_then(Value::as_str) == Some("reconciled")
            || !event
                .get("reconciliation")
                .unwrap_or(&Value::Null)
                .is_null()
        {
            return Err(LifecycleStorageError(
                "reconciliation is a separate explicit operation".to_string(),
            ));
        }
        let candidate_id = event
            .get("candidate_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                LifecycleStorageError("invalid journal artifact reference".to_string())
            })?;
        let candidate = get_artifact(root.join("artifacts"), candidate_id)
            .map_err(|_| LifecycleStorageError("invalid journal artifact reference".to_string()))?;
        if candidate.get("kind").and_then(Value::as_str) != Some("candidate") {
            return Err(LifecycleStorageError(
                "invalid journal artifact reference".to_string(),
            ));
        }
        if event.get("action").and_then(Value::as_str) == Some("promote") {
            let decision_id = event
                .get("decision_id")
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    LifecycleStorageError("invalid journal artifact reference".to_string())
                })?;
            let decision = get_artifact(root.join("artifacts"), decision_id).map_err(|_| {
                LifecycleStorageError("invalid journal artifact reference".to_string())
            })?;
            if decision.get("kind").and_then(Value::as_str) != Some("decision") {
                return Err(LifecycleStorageError(
                    "invalid journal artifact reference".to_string(),
                ));
            }
            accepted(&candidate, &decision).map_err(|_| {
                LifecycleStorageError("invalid journal artifact reference".to_string())
            })?;
        } else if !event.get("decision_id").unwrap_or(&Value::Null).is_null() {
            return Err(LifecycleStorageError(
                "rollback cannot fabricate an acceptance decision".to_string(),
            ));
        }

        let mut previous_good = state
            .get("previous_good")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut known_good = state.get("known_good").cloned().unwrap_or(Value::Null);
        match event
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
        {
            "started" => {
                if pending.is_some() {
                    return Err(LifecycleStorageError(
                        "overlapping handoff transitions".to_string(),
                    ));
                }
                if state.get("pending_cleanup").and_then(Value::as_bool) == Some(true) {
                    return Err(LifecycleStorageError(
                        "handoff cannot bypass pending remote-write reconciliation".to_string(),
                    ));
                }
                if event.get("action").and_then(Value::as_str) == Some("promote")
                    && state.get("remote_state").and_then(Value::as_str) == Some("unknown")
                {
                    return Err(LifecycleStorageError(
                        "promotion cannot bypass unresolved remote state".to_string(),
                    ));
                }
                if event.get("action").and_then(Value::as_str) == Some("promote")
                    && !known_good.is_null()
                {
                    let previous = get_artifact(
                        root.join("artifacts"),
                        known_good.as_str().unwrap_or_default(),
                    )
                    .map_err(|_| {
                        LifecycleStorageError("invalid journal artifact reference".to_string())
                    })?;
                    if previous.get("kind").and_then(Value::as_str) != Some("candidate")
                        || candidate.get("baseline_id").and_then(Value::as_str)
                            != previous.get("agent_id").and_then(Value::as_str)
                    {
                        return Err(LifecycleStorageError(
                            "promotion does not extend known-good evidence".to_string(),
                        ));
                    }
                }
                if event.get("action").and_then(Value::as_str) == Some("rollback") {
                    let target =
                        if state.get("remote_state").and_then(Value::as_str) == Some("unknown") {
                            known_good.as_str()
                        } else {
                            previous_good.last().and_then(Value::as_str)
                        };
                    if target.is_none() || Some(candidate_id) != target {
                        return Err(LifecycleStorageError(
                            "rollback is not a verified recovery candidate".to_string(),
                        ));
                    }
                }
                pending = Some(event_value.clone());
            }
            status => {
                if pending.as_ref().is_none_or(|started| {
                    started.get("action") != event_value.get("action")
                        || started.get("candidate_id") != event_value.get("candidate_id")
                        || started.get("decision_id") != event_value.get("decision_id")
                }) {
                    return Err(LifecycleStorageError(
                        "completion has no matching handoff".to_string(),
                    ));
                }
                if status == "verified" {
                    if event.get("action").and_then(Value::as_str) == Some("rollback") {
                        if known_good.as_str() != Some(candidate_id) {
                            previous_good.pop();
                        }
                    } else if !known_good.is_null() {
                        previous_good.push(known_good.clone());
                    }
                    known_good = Value::String(candidate_id.to_string());
                }
                pending = None;
            }
        }
        let status = event
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        state = journal_state(
            index as i64 + 1,
            known_good,
            previous_good,
            Value::String(status.clone()),
            envelope.get("id").cloned().unwrap_or(Value::Null),
            if status == "verified" {
                "verified"
            } else {
                "unknown"
            },
            status != "verified",
        );
        last_event = Some(event_value);
    }
    Ok(state)
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

async fn handoff<Deploy, DeployFuture, Verify, VerifyFuture>(
    root: &Path,
    state: &Value,
    candidate: &Value,
    action: &str,
    decision_id: Option<String>,
    deploy: Deploy,
    verify: Verify,
) -> Result<Value, LifecycleStorageError>
where
    Deploy: Fn(Value) -> DeployFuture,
    DeployFuture: Future<Output = Result<Value, LifecycleStorageError>>,
    Verify: Fn(Value, Value) -> VerifyFuture,
    VerifyFuture: Future<Output = Result<bool, LifecycleStorageError>>,
{
    let candidate_id = record_id(candidate)?;
    let state = append_event(
        root,
        state,
        action,
        &candidate_id,
        decision_id.clone(),
        "started",
        None,
    )?;
    let handoff_result = async {
        let receipt = deploy(candidate.clone()).await?;
        if receipt
            .get("status")
            .and_then(Value::as_str)
            .is_some_and(|status| {
                matches!(
                    status.trim().to_ascii_lowercase().as_str(),
                    "failed" | "failure" | "error" | "cancelled" | "canceled"
                )
            })
        {
            return Err(LifecycleStorageError(
                "deployment callback reported explicit failure".to_string(),
            ));
        }
        if verify(candidate.clone(), receipt).await? != true {
            return Err(LifecycleStorageError(
                "deployment verification did not explicitly succeed".to_string(),
            ));
        }
        Ok(())
    }
    .await;
    match handoff_result {
        Ok(()) => append_event(
            root,
            &state,
            action,
            &candidate_id,
            decision_id,
            "verified",
            None,
        ),
        Err(error) => {
            let _ = error;
            append_event(
                root,
                &state,
                action,
                &candidate_id,
                decision_id,
                "failed",
                None,
            )?;
            Err(LifecycleStorageError(
                "handoff failed; known-good is unchanged".to_string(),
            ))
        }
    }
}

fn check_revision(root: &Path, expected_revision: i64) -> Result<Value, LifecycleStorageError> {
    integer(expected_revision, "expected_revision")?;
    let state = journal_read(root)?;
    if state.get("revision").and_then(Value::as_i64) != Some(expected_revision) {
        return Err(LifecycleStorageError("stale journal revision".to_string()));
    }
    if state.get("last_status").and_then(Value::as_str) == Some("started") {
        return Err(LifecycleStorageError(
            "interrupted handoff requires manual reconciliation".to_string(),
        ));
    }
    if state.get("pending_cleanup").and_then(Value::as_bool) == Some(true) {
        return Err(LifecycleStorageError(
            "remote writes may still be outstanding; reconcile before any handoff".to_string(),
        ));
    }
    Ok(state)
}

fn append_event(
    root: &Path,
    state: &Value,
    action: &str,
    candidate_id: &str,
    decision_id: Option<String>,
    status: &str,
    reconciliation: Option<Value>,
) -> Result<Value, LifecycleStorageError> {
    let revision = state.get("revision").and_then(Value::as_i64).unwrap_or(0) + 1;
    let event = object([
        ("schema_version", Value::from(1)),
        ("revision", Value::from(revision)),
        (
            "previous",
            state.get("head").cloned().unwrap_or(Value::Null),
        ),
        ("action", Value::String(action.to_string())),
        ("candidate_id", Value::String(candidate_id.to_string())),
        (
            "decision_id",
            decision_id.map(Value::String).unwrap_or(Value::Null),
        ),
        ("status", Value::String(status.to_string())),
        ("reconciliation", reconciliation.unwrap_or(Value::Null)),
    ]);
    let identity = content_hash(&event)?;
    immutable_write(
        &root.join("events").join(format!("{revision:012}.json")),
        &object([("id", Value::String(identity)), ("event", event)]),
    )?;
    journal_read(root)
}

fn validate_event(
    event: &Map<String, Value>,
    index: i64,
    state: &Value,
    envelope_id: &Value,
) -> Result<(), LifecycleStorageError> {
    let required = [
        "schema_version",
        "revision",
        "previous",
        "action",
        "candidate_id",
        "decision_id",
        "status",
        "reconciliation",
    ];
    if event.len() != required.len() || required.iter().any(|key| !event.contains_key(*key)) {
        return Err(LifecycleStorageError("invalid journal event".to_string()));
    }
    if event.get("schema_version").and_then(Value::as_i64) != Some(1) {
        return Err(LifecycleStorageError(
            "unsupported journal schema".to_string(),
        ));
    }
    if event.get("revision").and_then(Value::as_i64) != Some(index) {
        return Err(LifecycleStorageError(
            "invalid journal revision".to_string(),
        ));
    }
    if event.get("previous") != state.get("head") {
        return Err(LifecycleStorageError(
            "journal hash chain mismatch".to_string(),
        ));
    }
    if content_hash(&Value::Object(event.clone()))? != envelope_id.as_str().unwrap_or_default() {
        return Err(LifecycleStorageError(
            "journal hash chain mismatch".to_string(),
        ));
    }
    if !matches!(
        event.get("action").and_then(Value::as_str),
        Some("promote" | "rollback" | "reconcile")
    ) || !matches!(
        event.get("status").and_then(Value::as_str),
        Some("started" | "verified" | "failed" | "cancelled" | "reconciled")
    ) {
        return Err(LifecycleStorageError(
            "unknown handoff transition".to_string(),
        ));
    }
    Ok(())
}

fn read_event(root: &Path, revision: i64) -> Result<Value, LifecycleStorageError> {
    let envelope = read_json(&root.join("events").join(format!("{revision:012}.json")))?;
    envelope
        .get("event")
        .cloned()
        .ok_or_else(|| LifecycleStorageError("invalid journal event".to_string()))
}

fn accepted(candidate: &Value, decision: &Value) -> Result<(), LifecycleStorageError> {
    if decision.get("accepted").and_then(Value::as_bool) != Some(true)
        || candidate.get("agent_id").and_then(Value::as_str)
            != decision.get("candidate_agent_id").and_then(Value::as_str)
        || candidate.get("baseline_id").and_then(Value::as_str)
            != decision.get("baseline_agent_id").and_then(Value::as_str)
    {
        return Err(LifecycleStorageError(
            "candidate must match an accepted comparison decision".to_string(),
        ));
    }
    Ok(())
}

fn reconciliation_evidence(value: &Value) -> Result<Value, LifecycleStorageError> {
    let object = value.as_object().ok_or_else(|| {
        LifecycleStorageError("reconciliation must return structured evidence".to_string())
    })?;
    if object.len() != 2
        || object.get("no_outstanding_writes").and_then(Value::as_bool) != Some(true)
        || !object.contains_key("evidence")
    {
        return Err(LifecycleStorageError(
            "reconciliation must explicitly establish no outstanding writes".to_string(),
        ));
    }
    let evidence = object
        .get("evidence")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            LifecycleStorageError("terminal operation evidence must be nonempty text".to_string())
        })?;
    if evidence.trim().is_empty() {
        return Err(LifecycleStorageError(
            "terminal operation evidence must be nonempty text".to_string(),
        ));
    }
    super::records::check_public(value)?;
    Ok(value.clone())
}

fn journal_state(
    revision: i64,
    known_good: Value,
    previous_good: Vec<Value>,
    last_status: Value,
    head: Value,
    remote_state: &str,
    pending_cleanup: bool,
) -> Value {
    json!({
        "revision": revision,
        "known_good": known_good,
        "previous_good": previous_good,
        "last_status": last_status,
        "head": head,
        "remote_state": remote_state,
        "pending_cleanup": pending_cleanup,
    })
}

struct JournalLock {
    path: std::path::PathBuf,
}

impl JournalLock {
    fn new(root: &Path) -> Result<Self, LifecycleStorageError> {
        fs::create_dir_all(root).map_err(cannot_read)?;
        fs::create_dir_all(root.join("events")).map_err(cannot_read)?;
        let path = root.join(".handoff.lock");
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut file) => {
                write!(file, "{}", std::process::id()).map_err(cannot_read)?;
                file.sync_all().map_err(cannot_read)?;
                Ok(Self { path })
            }
            Err(error) if error.kind() == ErrorKind::AlreadyExists => Err(LifecycleStorageError(
                "handoff locked; reconcile interrupted work before retry".to_string(),
            )),
            Err(error) => Err(cannot_read(error)),
        }
    }
}

impl Drop for JournalLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

fn integer(value: i64, label: &str) -> Result<(), LifecycleStorageError> {
    if value < 0 {
        return Err(LifecycleStorageError(format!(
            "{label} must be an integer >= 0"
        )));
    }
    Ok(())
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

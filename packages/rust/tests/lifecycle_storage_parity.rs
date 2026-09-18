use castia::lifecycle::{
    canonical_json, compare_runs, get_artifact, journal_promote, journal_read, journal_reconcile,
    journal_rollback, put_artifact, record_id,
};
use serde_json::{json, Value};
use std::fs;
use std::sync::{Arc, Barrier};
use std::thread;

fn agent_record() -> serde_json::Value {
    json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [],
        "dependencies": {},
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful."
    })
}

fn agent_with_instructions(instructions: &str, sha256: &str, size: i64) -> Value {
    json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "instructions.md", "sha256": sha256, "size": size}],
        "dependencies": {"castia": "0.6.0"},
        "model": { "deployment": "baseline" },
        "instructions": instructions,
        "tools": []
    })
}

fn candidate_for(agent: &Value, baseline: &Value, content: &str, sha256: &str) -> Value {
    json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": record_id(baseline).unwrap(),
        "agent_id": record_id(agent).unwrap(),
        "files": [{"path": "instructions.md", "content": content, "sha256": sha256}],
        "agent_snapshot": agent
    })
}

fn run_for(agent: &Value, quality: f64) -> Value {
    json!({
        "schema_version": 1,
        "kind": "run",
        "agent_id": record_id(agent).unwrap(),
        "dataset_id": "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2",
        "evaluator": {"name": "rubric", "version": "v1", "configuration": {"judge": "judge-v1"}},
        "split": "heldout",
        "expected_ids": [
            "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20",
            "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"
        ],
        "repeats": 1,
        "results": [
            {"example_id": "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "repetition": 0, "metrics": {"quality": quality, "latency_seconds": 0.1, "cost": 0.001}, "error": null},
            {"example_id": "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3", "repetition": 0, "metrics": {"quality": quality, "latency_seconds": 0.1, "cost": 0.001}, "error": null}
        ]
    })
}

fn baseline_agent() -> Value {
    agent_with_instructions(
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
        8,
    )
}

fn changed_agent() -> Value {
    agent_with_instructions(
        "candidate\r\n",
        "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f",
        11,
    )
}

#[test]
fn artifact_store_roundtrips_and_is_idempotent() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    assert_eq!(
        identity,
        "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59"
    );
    let record = get_artifact(temp.path(), &identity).unwrap();
    assert_eq!(record["tools"], json!([]));
    let original = fs::read_to_string(temp.path().join(format!("{identity}.json"))).unwrap();
    assert_eq!(
        put_artifact(temp.path(), &agent_record()).unwrap(),
        identity
    );
    assert_eq!(
        fs::read_to_string(temp.path().join(format!("{identity}.json"))).unwrap(),
        original
    );
    assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    assert!(fs::read_dir(temp.path()).unwrap().all(|entry| !entry
        .unwrap()
        .file_name()
        .to_string_lossy()
        .starts_with(".write-")));
}

#[test]
fn artifact_store_rejects_tampered_envelopes_and_preserves_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    let path = temp.path().join(format!("{identity}.json"));
    let original = fs::read_to_string(&path).unwrap();

    let mut payload: serde_json::Value = serde_json::from_str(&original).unwrap();
    payload["record"]["instructions"] = json!("tampered");
    let raw = serde_json::to_string(&payload).unwrap();
    fs::write(&path, &raw).unwrap();
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "artifact content hash mismatch"
    );
    assert_eq!(
        put_artifact(temp.path(), &agent_record())
            .unwrap_err()
            .to_string(),
        "immutable evidence already exists with different bytes"
    );
    assert_eq!(fs::read_to_string(path).unwrap(), raw);
}

#[test]
fn artifact_store_rejects_duplicate_keys_and_bad_identities() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    fs::write(
        temp.path().join(format!("{identity}.json")),
        "{\"id\":\"a\",\"id\":\"b\",\"record\":{}}\n",
    )
    .unwrap();
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "cannot read evidence JSON"
    );
    for identity in [
        "../other".to_string(),
        "..\\other".to_string(),
        "A".repeat(64),
    ] {
        assert_eq!(
            get_artifact(temp.path(), &identity)
                .unwrap_err()
                .to_string(),
            "id must be a lowercase SHA-256 digest"
        );
    }
}

#[test]
fn artifact_store_rejects_envelope_identity_mismatch() {
    let temp = tempfile::tempdir().unwrap();
    let identity = "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59";
    let payload = json!({
        "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "record": {
            "schema_version": 1,
            "kind": "agent",
            "source_files": [],
            "dependencies": {},
            "model": { "deployment": "baseline" },
            "instructions": "Be helpful.",
            "tools": []
        }
    });
    fs::write(
        temp.path().join(format!("{identity}.json")),
        format!("{}\n", canonical_json(&payload).unwrap()),
    )
    .unwrap();
    assert_eq!(
        get_artifact(temp.path(), identity).unwrap_err().to_string(),
        "artifact envelope identity mismatch"
    );
}

#[test]
fn artifact_store_rejects_unreadable_and_invalid_artifacts() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    let path = temp.path().join(format!("{identity}.json"));

    assert_eq!(
        get_artifact(
            temp.path(),
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
        .unwrap_err()
        .to_string(),
        "cannot read evidence JSON"
    );

    fs::write(&path, "[]\n").unwrap();
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "cannot read evidence JSON"
    );

    fs::write(&path, format!("{{\"id\":\"{identity}\",\"record\":[]}}\n")).unwrap();
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "invalid artifact content"
    );

    for record_patch in [json!({"schema_version": 99}), json!({"unknown": "extra"})] {
        let mut payload = json!({"id": identity, "record": agent_record()});
        let record = payload["record"].as_object_mut().unwrap();
        for (key, value) in record_patch.as_object().unwrap() {
            record.insert(key.clone(), value.clone());
        }
        fs::write(&path, format!("{}\n", canonical_json(&payload).unwrap())).unwrap();
        assert_eq!(
            get_artifact(temp.path(), &identity)
                .unwrap_err()
                .to_string(),
            "invalid artifact content"
        );
    }
}

#[test]
fn artifact_store_concurrent_puts_are_idempotent() {
    let temp = tempfile::tempdir().unwrap();
    let root = Arc::new(temp.path().to_path_buf());
    let barrier = Arc::new(Barrier::new(32));
    let mut workers = Vec::new();

    for _ in 0..32 {
        let root = Arc::clone(&root);
        let barrier = Arc::clone(&barrier);
        workers.push(thread::spawn(move || {
            barrier.wait();
            put_artifact(&*root, &agent_record()).unwrap()
        }));
    }

    let identities = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect::<Vec<_>>();
    assert!(identities.iter().all(|identity| identity == &identities[0]));
    assert_eq!(fs::read_dir(temp.path()).unwrap().count(), 1);
    assert!(fs::read_dir(temp.path()).unwrap().all(|entry| !entry
        .unwrap()
        .file_name()
        .to_string_lossy()
        .starts_with(".write-")));
}

#[cfg(unix)]
#[test]
fn artifact_store_rejects_symlinked_artifacts() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    let path = temp.path().join(format!("{identity}.json"));
    let link_target = temp.path().join("target.json");
    fs::rename(&path, &link_target).unwrap();
    std::os::unix::fs::symlink(&link_target, &path).unwrap();
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "symlinked evidence is not allowed"
    );
}

#[cfg(windows)]
#[test]
fn artifact_store_rejects_symlinked_artifacts_when_supported() {
    let temp = tempfile::tempdir().unwrap();
    let identity = put_artifact(temp.path(), &agent_record()).unwrap();
    let path = temp.path().join(format!("{identity}.json"));
    let link_target = temp.path().join("target.json");
    fs::rename(&path, &link_target).unwrap();
    if std::os::windows::fs::symlink_file(&link_target, &path).is_err() {
        return;
    }
    assert_eq!(
        get_artifact(temp.path(), &identity)
            .unwrap_err()
            .to_string(),
        "symlinked evidence is not allowed"
    );
}

#[tokio::test]
async fn journal_promote_then_verified_rollback() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let changed_agent = changed_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let candidate = candidate_for(
        &changed_agent,
        &baseline_agent,
        "candidate\r\n",
        "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f",
    );
    let run = run_for(&baseline_agent, 0.8);
    let first = journal_promote(
        root,
        &baseline,
        &compare_runs(&run, &run, &json!({})).unwrap(),
        |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
        |candidate, receipt| async move { Ok(receipt["id"] == record_id(&candidate).unwrap()) },
        0,
    )
    .await
    .unwrap();
    assert_eq!(first["known_good"], json!(record_id(&baseline).unwrap()));
    assert_eq!(first["revision"], json!(2));
    assert_eq!(first["remote_state"], json!("verified"));

    let second = journal_promote(
        root,
        &candidate,
        &compare_runs(&run, &run_for(&changed_agent, 0.9), &json!({})).unwrap(),
        |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
        |candidate, receipt| async move { Ok(receipt["id"] == record_id(&candidate).unwrap()) },
        2,
    )
    .await
    .unwrap();
    assert_eq!(second["known_good"], json!(record_id(&candidate).unwrap()));
    assert_eq!(
        second["previous_good"],
        json!([record_id(&baseline).unwrap()])
    );

    let third = journal_rollback(
        root,
        |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
        |candidate, receipt| async move { Ok(receipt["id"] == record_id(&candidate).unwrap()) },
        4,
    )
    .await
    .unwrap();
    assert_eq!(third["known_good"], json!(record_id(&baseline).unwrap()));
    assert_eq!(third["previous_good"], json!([]));
    assert_eq!(journal_read(root).unwrap(), third);
    assert!(fs::read_dir(root.join("events")).unwrap().all(|entry| {
        !fs::read_to_string(entry.unwrap().path())
            .unwrap()
            .contains("receipt-never-persisted")
    }));
}

#[tokio::test]
async fn journal_failed_verification_requires_reconcile_before_recovery() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let changed_agent = changed_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let candidate = candidate_for(
        &changed_agent,
        &baseline_agent,
        "candidate\r\n",
        "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f",
    );
    let run = run_for(&baseline_agent, 0.8);
    journal_promote(
        root,
        &baseline,
        &compare_runs(&run, &run, &json!({})).unwrap(),
        |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
        |_, _| async { Ok(true) },
        0,
    )
    .await
    .unwrap();
    assert_eq!(
        journal_promote(
            root,
            &candidate,
            &compare_runs(&run, &run_for(&changed_agent, 0.9), &json!({})).unwrap(),
            |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
            |_, _| async { Ok(false) },
            2,
        )
        .await
        .unwrap_err()
        .to_string(),
        "handoff failed; known-good is unchanged"
    );
    let failed = journal_read(root).unwrap();
    assert_eq!(failed["known_good"], json!(record_id(&baseline).unwrap()));
    assert_eq!(failed["last_status"], json!("failed"));
    assert_eq!(failed["remote_state"], json!("unknown"));
    assert_eq!(
        journal_promote(
            root,
            &candidate,
            &compare_runs(&run, &run_for(&changed_agent, 0.9), &json!({})).unwrap(),
            |_| async { Ok(Value::Null) },
            |_, _| async { Ok(true) },
            4,
        )
        .await
        .unwrap_err()
        .to_string(),
        "remote writes may still be outstanding; reconcile before any handoff"
    );
    let reconciled = journal_reconcile(
        root,
        |_| async {
            Ok(json!({"no_outstanding_writes": true, "evidence": "owned operation terminal"}))
        },
        4,
    )
    .await
    .unwrap();
    assert_eq!(reconciled["pending_cleanup"], json!(false));
    let recovered = journal_rollback(
        root,
        |candidate| async move { Ok(json!({"id": record_id(&candidate).unwrap()})) },
        |_, _| async { Ok(true) },
        5,
    )
    .await
    .unwrap();
    assert_eq!(
        recovered["known_good"],
        json!(record_id(&baseline).unwrap())
    );
    assert_eq!(recovered["remote_state"], json!("verified"));
}

#[tokio::test]
async fn journal_read_ignores_leftover_write_staging_files() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let run = run_for(&baseline_agent, 0.8);
    journal_promote(
        root,
        &baseline,
        &compare_runs(&run, &run, &json!({})).unwrap(),
        |_| async { Ok(Value::Null) },
        |_, _| async { Ok(true) },
        0,
    )
    .await
    .unwrap();
    fs::write(root.join("events").join(".write-leftover"), "{}").unwrap();
    assert_eq!(journal_read(root).unwrap()["revision"], json!(2));
}

#[tokio::test]
async fn failed_rollback_preserves_known_good_history() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let changed_agent = changed_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let candidate = candidate_for(
        &changed_agent,
        &baseline_agent,
        "candidate\r\n",
        "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f",
    );
    let run = run_for(&baseline_agent, 0.8);
    journal_promote(
        root,
        &baseline,
        &compare_runs(&run, &run, &json!({})).unwrap(),
        |_| async { Ok(Value::Null) },
        |_, _| async { Ok(true) },
        0,
    )
    .await
    .unwrap();
    journal_promote(
        root,
        &candidate,
        &compare_runs(&run, &run_for(&changed_agent, 0.9), &json!({})).unwrap(),
        |_| async { Ok(Value::Null) },
        |_, _| async { Ok(true) },
        2,
    )
    .await
    .unwrap();
    assert_eq!(
        journal_rollback(
            root,
            |_| async { Ok(Value::Null) },
            |_, _| async { Ok(false) },
            4
        )
        .await
        .unwrap_err()
        .to_string(),
        "handoff failed; known-good is unchanged"
    );
    let failed = journal_read(root).unwrap();
    assert_eq!(failed["known_good"], json!(record_id(&candidate).unwrap()));
    assert_eq!(
        failed["previous_good"],
        json!([record_id(&baseline).unwrap()])
    );
}

#[tokio::test]
async fn journal_rejects_bad_reconciliation_and_stale_revision() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let run = run_for(&baseline_agent, 0.8);
    assert_eq!(
        journal_promote(
            root,
            &baseline,
            &compare_runs(&run, &run, &json!({})).unwrap(),
            |_| async {
                Err(castia::lifecycle::LifecycleStorageError::new(
                    "private failure",
                ))
            },
            |_, _| async { Ok(true) },
            0,
        )
        .await
        .unwrap_err()
        .to_string(),
        "handoff failed; known-good is unchanged"
    );
    assert_eq!(
        journal_reconcile(root, |_| async { Ok(json!(true)) }, 2)
            .await
            .unwrap_err()
            .to_string(),
        "reconciliation must return structured evidence"
    );
    assert_eq!(
        journal_rollback(
            root,
            |_| async { Ok(Value::Null) },
            |_, _| async { Ok(true) },
            0
        )
        .await
        .unwrap_err()
        .to_string(),
        "stale journal revision"
    );
}

#[tokio::test]
async fn journal_rejected_or_mismatched_decision_never_deploys() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let changed_agent = changed_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let mismatched = candidate_for(
        &changed_agent,
        &baseline_agent,
        "candidate\r\n",
        "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f",
    );
    let run = run_for(&baseline_agent, 0.8);
    assert_eq!(
        journal_promote(
            root,
            &baseline,
            &compare_runs(&run, &run_for(&baseline_agent, 0.1), &json!({})).unwrap(),
            |_| async { panic!("deploy must not run") },
            |_, _| async { Ok(true) },
            0,
        )
        .await
        .unwrap_err()
        .to_string(),
        "candidate must match an accepted comparison decision"
    );
    assert_eq!(
        journal_promote(
            root,
            &mismatched,
            &compare_runs(&run, &run, &json!({})).unwrap(),
            |_| async { panic!("deploy must not run") },
            |_, _| async { Ok(true) },
            0,
        )
        .await
        .unwrap_err()
        .to_string(),
        "candidate must match an accepted comparison decision"
    );
    assert_eq!(journal_read(root).unwrap()["revision"], json!(0));
}

#[tokio::test]
async fn journal_tampering_fails_closed() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path();
    let baseline_agent = baseline_agent();
    let baseline = candidate_for(
        &baseline_agent,
        &baseline_agent,
        "baseline",
        "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24",
    );
    let run = run_for(&baseline_agent, 0.8);
    journal_promote(
        root,
        &baseline,
        &compare_runs(&run, &run, &json!({})).unwrap(),
        |_| async { Ok(Value::Null) },
        |_, _| async { Ok(true) },
        0,
    )
    .await
    .unwrap();
    let path = root.join("events").join("000000000001.json");
    let mut payload: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    payload["event"]["status"] = json!("verified");
    fs::write(&path, serde_json::to_string(&payload).unwrap()).unwrap();
    assert_eq!(
        journal_read(root).unwrap_err().to_string(),
        "journal hash chain mismatch"
    );
}

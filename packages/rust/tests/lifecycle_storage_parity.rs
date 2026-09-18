use castia::lifecycle::{canonical_json, get_artifact, put_artifact};
use serde_json::json;
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

use castia::lifecycle::{
    canonical_json, compare_runs, curate_dataset, curate_dataset_with_redactor, dataset_jsonl,
    diff_candidates, evaluate_outcomes, evaluate_with_callback, stage_candidate,
};
use serde_json::{json, Value};
use std::sync::{
    atomic::{AtomicI64, Ordering},
    Arc,
};
use std::time::Duration;

fn trace(index: usize) -> Value {
    json!({
        "trace_id": format!("trace-{index}"),
        "input": format!("question-{index}"),
        "reference": format!("reference-{index}"),
        "reviewer": "human-reviewer",
        "group": format!("conversation-{index}"),
        "approved": true,
        "reference_origin": "human",
        "model_output": format!("generated-{index}")
    })
}

fn traces() -> Vec<Value> {
    (0..6).map(trace).collect()
}

fn agent() -> Value {
    json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{
            "path": "instructions.md",
            "sha256": "5ad131c7cb35b34d24e0110e754475cd7d369fc9b9f3dd0c967aeb35e6fbcb4f",
            "size": 11
        }],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "Be helpful.",
        "tools": []
    })
}

fn dataset() -> Value {
    json!({
        "schema_version": 1,
        "kind": "dataset",
        "examples": [
            {"input": "question-1", "reference": "reference-1", "group": "group-1", "provenance": ["trace-1"], "reviewers": ["human-reviewer"], "reference_origins": ["human"]},
            {"input": "question-0", "reference": "reference-0", "group": "group-0", "provenance": ["trace-0"], "reviewers": ["human-reviewer"], "reference_origins": ["human"]}
        ],
        "train_ids": ["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"],
        "heldout_ids": ["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"],
        "seed": "castia",
        "redaction_version": "v1"
    })
}

fn evaluator() -> Value {
    json!({"name": "rubric", "version": "v2", "configuration": {"judge": "model", "temperature": 0}})
}

struct ActiveGuard(Arc<AtomicI64>);

impl Drop for ActiveGuard {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

#[test]
fn curation_is_deterministic_deduplicated_and_independent() {
    let mut rows = traces();
    let mut duplicate = trace(0);
    duplicate["trace_id"] = json!("trace-duplicate");
    duplicate["reviewer"] = json!("second-reviewer");
    rows.push(duplicate);

    let first = curate_dataset(&rows, "v1", 0.2, "castia").unwrap();
    rows.reverse();
    let second = curate_dataset(&rows, "v1", 0.2, "castia").unwrap();

    assert_eq!(first, second);
    assert_eq!(first["examples"].as_array().unwrap().len(), 6);
    let example = first["examples"]
        .as_array()
        .unwrap()
        .iter()
        .find(|example| example["input"] == "question-0")
        .unwrap();
    assert_eq!(example["provenance"], json!(["trace-0", "trace-duplicate"]));
    assert_eq!(
        example["reviewers"],
        json!(["human-reviewer", "second-reviewer"])
    );
    assert!(first["train_ids"]
        .as_array()
        .unwrap()
        .iter()
        .all(|id| !first["heldout_ids"].as_array().unwrap().contains(id)));
    let canonical = canonical_json(&first).unwrap();
    assert!(!canonical.contains("model_output"));
    assert!(!canonical.contains("generated-0"));

    let changed = curate_dataset(&rows, "v2", 0.2, "castia").unwrap();
    assert_ne!(first, changed);
}

#[tokio::test]
async fn evaluation_is_bounded_versioned_and_normalized() {
    let active = Arc::new(AtomicI64::new(0));
    let peak = Arc::new(AtomicI64::new(0));
    let run = evaluate_with_callback(
        &agent(),
        &dataset(),
        &evaluator(),
        {
            let active = Arc::clone(&active);
            let peak = Arc::clone(&peak);
            move |snapshot, example, repetition| {
                let active = Arc::clone(&active);
                let peak = Arc::clone(&peak);
                async move {
                    assert_eq!(
                        snapshot["instructions"],
                        json!("Be helpful."),
                        "callback receives normalized agent snapshot"
                    );
                    assert_eq!(example["reference"], json!("reference-1"));
                    let now = active.fetch_add(1, Ordering::SeqCst) + 1;
                    peak.fetch_max(now, Ordering::SeqCst);
                    tokio::time::sleep(Duration::from_millis(5)).await;
                    active.fetch_sub(1, Ordering::SeqCst);
                    Ok(json!({"quality": 1.0, "cost": repetition as f64 * 0.001, "latency_seconds": 0.01}))
                }
            }
        },
        "heldout",
        4,
        2,
        1.0,
    )
    .await
    .unwrap();

    assert_eq!(active.load(Ordering::SeqCst), 0);
    assert_eq!(peak.load(Ordering::SeqCst), 2);
    assert_eq!(run["results"].as_array().unwrap().len(), 4);
    assert_eq!(run["evaluator"], evaluator());
    assert_eq!(
        run["agent_id"],
        json!("ed6976555b4b1cd6947ae5f83a2c2f2402a5eda92fdf5f9d87536d834937378d")
    );
}

#[tokio::test]
async fn evaluation_errors_timeouts_and_invalid_metrics_are_safe() {
    let run = evaluate_with_callback(
        &agent(),
        &dataset(),
        &evaluator(),
        |_, _, repetition| async move {
            if repetition == 0 {
                return Err(castia::lifecycle::LifecycleOperationsError::new(
                    "****** never persist exception content",
                ));
            }
            if repetition == 1 {
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
            Ok(json!({"quality": true}))
        },
        "heldout",
        3,
        2,
        0.01,
    )
    .await
    .unwrap();
    assert_eq!(
        run["results"]
            .as_array()
            .unwrap()
            .iter()
            .map(|result| result["error"].as_str().unwrap_or("ok"))
            .collect::<std::collections::BTreeSet<_>>(),
        ["callback_error", "invalid_metrics", "timeout"]
            .into_iter()
            .collect()
    );
    assert!(!canonical_json(&run).unwrap().contains("never persist"));
    assert!(!compare_runs(&run, &run, &json!({})).unwrap()["accepted"]
        .as_bool()
        .unwrap());
}

#[tokio::test]
async fn evaluation_outcome_vectors_match_python_reference_shape() {
    let run = evaluate_outcomes(
        &agent(),
        &dataset(),
        &evaluator(),
        &json!([
            {"example_id": "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "repetition": 0, "metrics": {"quality": 1.0, "cost": 0.0, "latency_seconds": 0.01}},
            {"example_id": "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "repetition": 1, "metrics": {"quality": 1.0, "cost": 0.001, "latency_seconds": 0.01}}
        ]),
        "heldout",
        2,
        1,
        1.0,
    )
    .await
    .unwrap();
    assert_eq!(
        castia::lifecycle::record_id(&run).unwrap(),
        "d272842c227553098121f61b69eb0b93a2a71a835ede266f348bcfc6486150c5"
    );
}

#[tokio::test]
async fn evaluation_accepts_large_finite_timeout_without_panic() {
    let run = evaluate_with_callback(
        &agent(),
        &dataset(),
        &evaluator(),
        |_, _, _| async { Ok(json!({"quality": 1.0, "latency_seconds": 0.01})) },
        "heldout",
        1,
        1,
        1e30,
    )
    .await
    .unwrap();
    assert!(run["results"][0]["error"].is_null());
}

#[tokio::test]
async fn evaluation_rejects_invalid_runner_configuration() {
    for (split, repeats, concurrency, timeout, message) in [
        ("validation", 1, 1, 1.0, "split must be train or heldout"),
        ("heldout", 0, 1, 1.0, "repeats must be an integer >= 1"),
        ("heldout", 1, 0, 1.0, "concurrency must be an integer >= 1"),
        ("heldout", 1, 1, 0.0, "timeout must be positive"),
    ] {
        assert_eq!(
            evaluate_with_callback(
                &agent(),
                &dataset(),
                &evaluator(),
                |_, _, _| async { Ok(json!({"quality": 1.0})) },
                split,
                repeats,
                concurrency,
                timeout,
            )
            .await
            .unwrap_err()
            .to_string(),
            message
        );
    }
}

#[tokio::test]
async fn evaluation_cancellation_aborts_owned_workers() {
    let active = Arc::new(AtomicI64::new(0));
    let task = tokio::spawn({
        let active = Arc::clone(&active);
        async move {
            let agent = agent();
            let dataset = dataset();
            let evaluator = evaluator();
            evaluate_with_callback(
                &agent,
                &dataset,
                &evaluator,
                move |_, _, _| {
                    let active = Arc::clone(&active);
                    async move {
                        active.fetch_add(1, Ordering::SeqCst);
                        let _guard = ActiveGuard(active);
                        tokio::time::sleep(Duration::from_secs(100)).await;
                        Ok(json!({"quality": 1.0}))
                    }
                },
                "heldout",
                4,
                2,
                30.0,
            )
            .await
        }
    });
    while active.load(Ordering::SeqCst) == 0 {
        tokio::task::yield_now().await;
    }
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    assert_eq!(active.load(Ordering::SeqCst), 0);
}

#[test]
fn candidate_stage_hash_diff_and_no_baseline_mutation() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("instructions.md");
    std::fs::write(&path, b"baseline").unwrap();
    let baseline_agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "instructions.md", "sha256": "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24", "size": 8}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "baseline",
        "tools": []
    });
    let baseline = stage_candidate(
        temp.path(),
        &["instructions.md".to_string()],
        &baseline_agent,
        &baseline_agent,
    )
    .unwrap();
    std::fs::write(&path, b"candidate\r\n").unwrap();
    let changed = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "instructions.md", "sha256": "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f", "size": 11}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "candidate\r\n",
        "tools": []
    });
    let candidate = stage_candidate(
        temp.path(),
        &["instructions.md".to_string()],
        &baseline_agent,
        &changed,
    )
    .unwrap();
    assert_eq!(baseline["files"][0]["content"], json!("baseline"));
    assert_eq!(
        candidate["files"][0]["sha256"],
        json!("42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f")
    );
    assert_eq!(
        diff_candidates(&baseline, &candidate).unwrap(),
        json!({"instructions.md": {"before": "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24", "after": "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f"}})
    );
    assert_eq!(diff_candidates(&candidate, &candidate).unwrap(), json!({}));
    assert_eq!(std::fs::read(&path).unwrap(), b"candidate\r\n");
}

#[test]
fn staged_bytes_must_match_evaluated_snapshot() {
    let temp = tempfile::tempdir().unwrap();
    std::fs::write(
        temp.path().join("instructions.md"),
        b"changed after evaluation",
    )
    .unwrap();
    let baseline_agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "instructions.md", "sha256": "8ba8496a2525ae171ffd104d632dede6ef418d9b95962a9d88e2fcdbc8d48d24", "size": 8}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "baseline",
        "tools": []
    });
    let changed = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "instructions.md", "sha256": "42e48c1b0ec8a690433dd65c846d4f8376d469082385efe7594b713bec7bd30f", "size": 11}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "candidate\r\n",
        "tools": []
    });
    assert_eq!(
        stage_candidate(
            temp.path(),
            &["instructions.md".to_string()],
            &baseline_agent,
            &changed
        )
        .unwrap_err()
        .to_string(),
        "staged payload is not bound to the evaluated snapshot; capture files before evaluation"
    );
}

#[test]
fn candidate_staging_rejects_missing_and_credential_files() {
    let temp = tempfile::tempdir().unwrap();
    let candidate_agent = agent();
    for (path, message) in [
        ("missing.md", "explicit evidence file does not exist"),
        (".env", "credential files are not evidence"),
        ("private.pem", "credential files are not evidence"),
    ] {
        if path != "missing.md" {
            std::fs::write(temp.path().join(path), b"placeholder").unwrap();
        }
        assert_eq!(
            stage_candidate(
                temp.path(),
                &[path.to_string()],
                &candidate_agent,
                &candidate_agent
            )
            .unwrap_err()
            .to_string(),
            message
        );
    }
}

#[test]
fn candidate_staging_rejects_symlinked_files_when_supported() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(outside.path(), b"Be helpful.").unwrap();
    let link = temp.path().join("instructions.md");
    #[cfg(windows)]
    let linked = std::os::windows::fs::symlink_file(outside.path(), &link);
    #[cfg(unix)]
    let linked = std::os::unix::fs::symlink(outside.path(), &link);
    if linked.is_err() {
        return;
    }
    assert_eq!(
        stage_candidate(
            temp.path(),
            &["instructions.md".to_string()],
            &agent(),
            &agent()
        )
        .unwrap_err()
        .to_string(),
        "symlinked evidence paths are not allowed"
    );
}

#[test]
fn candidate_diff_reports_added_and_removed_paths() {
    let baseline_agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "a.md", "sha256": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "size": 1}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "a",
        "tools": []
    });
    let candidate_agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{"path": "b.md", "sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d", "size": 1}],
        "dependencies": {"castia": "0.6.0"},
        "model": {"deployment": "gpt-4o"},
        "instructions": "b",
        "tools": []
    });
    let baseline = json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": castia::lifecycle::record_id(&baseline_agent).unwrap(),
        "agent_id": castia::lifecycle::record_id(&baseline_agent).unwrap(),
        "files": [{"path": "a.md", "content": "a", "sha256": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"}],
        "agent_snapshot": baseline_agent
    });
    let candidate = json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": castia::lifecycle::record_id(&baseline["agent_snapshot"]).unwrap(),
        "agent_id": castia::lifecycle::record_id(&candidate_agent).unwrap(),
        "files": [{"path": "b.md", "content": "b", "sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d"}],
        "agent_snapshot": candidate_agent
    });
    assert_eq!(
        diff_candidates(&baseline, &candidate).unwrap(),
        json!({
            "a.md": {"before": "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb", "after": null},
            "b.md": {"before": null, "after": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d"}
        })
    );
}

#[test]
fn curation_rejects_unreviewed_or_model_gold() {
    for mutation in [
        json!({"approved": false}),
        json!({"approved": 1}),
        json!({"reviewer": ""}),
        json!({"reference_origin": "model"}),
        json!({"reference": "generated-0"}),
    ] {
        let mut rows = traces();
        for (key, value) in mutation.as_object().unwrap() {
            rows[0][key] = value.clone();
        }
        assert!(curate_dataset(&rows, "v1", 0.2, "castia").is_err());
    }
    let mut rows = traces();
    rows[0]["approved"] = json!(false);
    assert_eq!(
        curate_dataset(&rows, "v1", 0.2, "castia")
            .unwrap_err()
            .to_string(),
        "approved must be literal true and every trace requires explicit review approval"
    );
    assert_eq!(
        curate_dataset(&traces(), "v1", 0.0, "castia")
            .unwrap_err()
            .to_string(),
        "heldout_fraction must be between zero and one"
    );
}

#[test]
fn curation_preserves_reference_origins_and_exports_jsonl() {
    let rows = traces()
        .into_iter()
        .enumerate()
        .map(|(index, mut row)| {
            row["reference"] = json!(format!("{}", index + index));
            row["reference_origin"] = json!("deterministic");
            row["reviewer"] = json!("fixture-rule-review");
            row["model_output"] = Value::Null;
            row
        })
        .collect::<Vec<_>>();
    let dataset = curate_dataset(&rows, "fixture-v1", 0.2, "castia").unwrap();
    assert!(dataset["examples"]
        .as_array()
        .unwrap()
        .iter()
        .all(|example| example["reference_origins"] == json!(["deterministic"])));

    let heldout = dataset_jsonl(&dataset, "heldout").unwrap();
    let exported = heldout
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert!(exported
        .iter()
        .all(|row| row["reference_origins"] == json!(["deterministic"])));
    assert_eq!(
        dataset_jsonl(&dataset, "validation")
            .unwrap_err()
            .to_string(),
        "split must be train or heldout"
    );
}

#[test]
fn curation_redacts_provenance_before_persistence() {
    let mut rows = traces();
    for row in &mut rows {
        row["trace_id"] = json!("******");
        row["reviewer"] = json!("******");
    }
    let dataset = curate_dataset_with_redactor(
        &rows,
        |row| {
            let mut cleaned = row.clone();
            cleaned["trace_id"] = json!(castia::lifecycle::content_hash(&row["input"]).unwrap());
            cleaned["reviewer"] = json!("reviewed");
            Ok(cleaned)
        },
        "strip-v1",
        0.2,
        "castia",
    )
    .unwrap();
    let canonical = canonical_json(&dataset).unwrap();
    assert!(!canonical.contains("******"));

    assert!(curate_dataset(&rows, "bad-v1", 0.2, "castia").is_err());
}

#[test]
fn curation_rejects_redaction_that_launders_gold_or_origin() {
    assert_eq!(
        curate_dataset_with_redactor(
            &traces(),
            |row| {
                let mut cleaned = row.clone();
                cleaned["reference"] = cleaned["model_output"].clone();
                cleaned["model_output"] = Value::Null;
                Ok(cleaned)
            },
            "bad-policy",
            0.2,
            "castia",
        )
        .unwrap_err()
        .to_string(),
        "redaction cannot turn model output into gold"
    );
    assert_eq!(
        curate_dataset_with_redactor(
            &traces(),
            |row| {
                let mut cleaned = row.clone();
                cleaned["reference_origin"] = json!("deterministic");
                Ok(cleaned)
            },
            "bad-policy",
            0.2,
            "castia",
        )
        .unwrap_err()
        .to_string(),
        "redaction cannot change the reference's authority"
    );
}

#[test]
fn curation_rejects_conflicting_gold_and_provenance() {
    let mut rows = traces();
    let mut conflict = trace(0);
    conflict["trace_id"] = json!("another");
    conflict["reference"] = json!("conflicting");
    rows.push(conflict);
    assert_eq!(
        curate_dataset(&rows, "v1", 0.2, "castia")
            .unwrap_err()
            .to_string(),
        "duplicate input has conflicting references"
    );

    let mut rows = traces();
    rows[1]["trace_id"] = rows[0]["trace_id"].clone();
    assert_eq!(
        curate_dataset(&rows, "v1", 0.2, "castia")
            .unwrap_err()
            .to_string(),
        "trace provenance reused for conflicting inputs"
    );
}

#[test]
fn curation_preserves_original_groups_and_unions_duplicates() {
    let rows = traces()
        .into_iter()
        .map(|mut row| {
            row["group"] = json!("same-conversation");
            row
        })
        .collect::<Vec<_>>();
    assert_eq!(
        curate_dataset_with_redactor(
            &rows,
            |row| {
                let mut cleaned = row.clone();
                cleaned["group"] = cleaned["trace_id"].clone();
                Ok(cleaned)
            },
            "v1",
            0.2,
            "castia",
        )
        .unwrap_err()
        .to_string(),
        "curation requires at least two independent groups"
    );

    let mut rows = traces();
    let mut duplicate = trace(0);
    duplicate["trace_id"] = json!("cross-group");
    duplicate["group"] = rows[1]["group"].clone();
    rows.push(duplicate);
    let dataset = curate_dataset(&rows, "v1", 0.2, "castia").unwrap();
    let matching = dataset["examples"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|example| example["input"] == "question-0" || example["input"] == "question-1")
        .collect::<Vec<_>>();
    assert_eq!(matching[0]["group"], matching[1]["group"]);
    let train = dataset["train_ids"].as_array().unwrap();
    let heldout = dataset["heldout_ids"].as_array().unwrap();
    let ids = matching
        .iter()
        .map(|example| castia::lifecycle::example_id(example).unwrap())
        .collect::<Vec<_>>();
    assert!(
        ids.iter().all(|id| train.contains(&json!(id)))
            || ids.iter().all(|id| heldout.contains(&json!(id)))
    );
}

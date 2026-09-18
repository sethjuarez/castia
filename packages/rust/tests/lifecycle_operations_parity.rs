use castia::lifecycle::{
    canonical_json, curate_dataset, curate_dataset_with_redactor, dataset_jsonl,
};
use serde_json::{json, Value};

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

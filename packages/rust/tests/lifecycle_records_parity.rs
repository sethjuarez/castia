use castia::lifecycle::{
    canonical_json, check_public, content_hash, example_id, normalize_record, record_id, safe_path,
};
use serde_json::json;

#[test]
fn canonical_json_and_content_hash_match_python_vectors() {
    let value = json!({ "b": [2, 1], "a": "é" });
    assert_eq!(canonical_json(&value).unwrap(), "{\"a\":\"é\",\"b\":[2,1]}");
    assert_eq!(
        content_hash(&value).unwrap(),
        "265cdd44ca612f13fd2b8e14f6913a5513adf3142e6c82317e55ba51948f43f2"
    );
    assert_eq!(
        content_hash(&json!({ "input": "question-0" })).unwrap(),
        "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"
    );
}

#[test]
fn safe_path_matches_python_path_gate() {
    assert_eq!(safe_path("src\\main.py").unwrap(), "src/main.py");
    for path in [
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\absolute",
        "C:relative",
        "\\\\server\\share",
        "dir/../escape",
        "dir//file",
        "./file",
        "file:stream",
        "CON.txt",
        "trailing.",
        "space ",
    ] {
        assert_eq!(
            safe_path(path).unwrap_err().to_string(),
            "path must be a normalized relative file path"
        );
    }
    assert_eq!(
        safe_path("").unwrap_err().to_string(),
        "relative path must be nonempty text"
    );
}

#[test]
fn check_public_rejects_secret_shapes_and_allows_placeholders() {
    check_public(&json!({
        "apiKey": "${API_KEY}",
        "nested": { "password": "<redacted>" },
    }))
    .unwrap();
    for value in [
        json!({ "api_key": "private" }),
        json!({ "apiKey": "private" }),
        json!({ "clientSecret": "private" }),
        json!({ "authToken": "private" }),
        json!({ "nested": { "password": "private" } }),
    ] {
        assert_eq!(
            check_public(&value).unwrap_err().to_string(),
            "secret-bearing configuration key is not evidence"
        );
    }
    assert_eq!(
        check_public(&json!("Authorization: ******"))
            .unwrap_err()
            .to_string(),
        "secret-bearing configuration assignment is not evidence"
    );
    assert_eq!(
        check_public(&json!("{\"outer\": {\"password\": \"hunter2\"}}"))
            .unwrap_err()
            .to_string(),
        "secret-bearing configuration assignment is not evidence"
    );
}

#[test]
fn record_identity_and_normalization_match_python_records() {
    let agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [
            { "path": "b.py", "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "size": 2 },
            { "path": "a.py", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "size": 1 }
        ],
        "dependencies": { "castia": "0.5.0" },
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful.",
        "tools": []
    });
    let normalized = normalize_record(&agent).unwrap();
    assert_eq!(normalized["source_files"][0]["path"], "a.py");
    assert_eq!(normalized["source_files"][1]["path"], "b.py");

    let hash_vector = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [
            { "path": "agent.py", "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "size": 0 }
        ],
        "dependencies": { "castia": "0.5.0" },
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful.",
        "tools": []
    });
    assert_eq!(
        record_id(&hash_vector).unwrap(),
        "eda40e3a24313859ab842667d8a65db6033ddf24aee9e8a94f7b7205c0fe0fbe"
    );
}

#[test]
fn record_validation_rejects_python_record_failures() {
    assert_eq!(
        normalize_record(&json!({ "schema_version": 2, "kind": "agent" }))
            .unwrap_err()
            .to_string(),
        "unsupported evidence schema version"
    );
    assert_eq!(
        normalize_record(&json!({
            "schema_version": 1,
            "kind": "agent",
            "source_files": [
                { "path": "agent.py", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "size": 1 },
                { "path": "AGENT.py", "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "size": 1 }
            ],
            "dependencies": { "castia": "0.5.0" },
            "model": { "deployment": "baseline" },
            "instructions": "safe",
            "tools": []
        }))
        .unwrap_err()
        .to_string(),
        "duplicate source paths"
    );
}

#[test]
fn example_and_dataset_validation_match_python_rules() {
    let example = json!({
        "input": "question-0",
        "reference": "reference-0",
        "group": "conversation-0",
        "provenance": ["trace-0"],
        "reviewers": ["human-reviewer"],
        "reference_origins": ["human"]
    });
    let id = example_id(&example).unwrap();
    assert_eq!(
        id,
        "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"
    );
    let other = json!({
        "input": "question-1",
        "reference": "reference-1",
        "group": "conversation-1",
        "provenance": ["trace-1"],
        "reviewers": ["human-reviewer"],
        "reference_origins": ["deterministic", "human"]
    });
    let other_id = example_id(&other).unwrap();
    let dataset = json!({
        "schema_version": 1,
        "kind": "dataset",
        "examples": [other, example],
        "train_ids": [other_id],
        "heldout_ids": [id],
        "seed": "castia",
        "redaction_version": "v1"
    });
    let normalized = normalize_record(&dataset).unwrap();
    assert_eq!(normalized["examples"][0]["input"], "question-1");
    assert_eq!(
        normalized["examples"][0]["reference_origins"],
        json!(["deterministic", "human"])
    );

    let leaked = json!({
        "schema_version": 1,
        "kind": "dataset",
        "examples": [
            {
                "input": "question-0",
                "reference": "reference-0",
                "group": "same",
                "provenance": ["trace-0"],
                "reviewers": ["human-reviewer"],
                "reference_origins": ["human"]
            },
            {
                "input": "question-1",
                "reference": "reference-1",
                "group": "same",
                "provenance": ["trace-1"],
                "reviewers": ["human-reviewer"],
                "reference_origins": ["human"]
            }
        ],
        "train_ids": [other_id],
        "heldout_ids": [id],
        "seed": "castia",
        "redaction_version": "v1"
    });
    assert_eq!(
        normalize_record(&leaked).unwrap_err().to_string(),
        "a provenance group cannot cross splits"
    );
}

#[test]
fn run_candidate_and_decision_validation_match_python_rules() {
    let id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let run = json!({
        "schema_version": 1,
        "kind": "run",
        "agent_id": id,
        "dataset_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "evaluator": { "name": "judge", "version": "1", "configuration": {} },
        "split": "heldout",
        "expected_ids": [id],
        "repeats": 1,
        "results": [
            { "example_id": id, "repetition": 0, "metrics": { "score": 1 }, "error": null },
            { "example_id": id, "repetition": 0, "metrics": { "score": 1 }, "error": null }
        ]
    });
    assert_eq!(
        normalize_record(&run).unwrap_err().to_string(),
        "unexpected or duplicate evaluation result"
    );

    let decision = json!({
        "schema_version": 1,
        "kind": "decision",
        "baseline_run_id": id,
        "candidate_run_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "baseline_agent_id": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "candidate_agent_id": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "accepted": true,
        "reasons": ["regressed"],
        "aggregates": { "score": 1 },
        "regressions": [],
        "gate": { "minimum_score": 0.8 }
    });
    assert_eq!(
        normalize_record(&decision).unwrap_err().to_string(),
        "accepted decisions have no rejection reasons"
    );

    let source_sha = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
    let agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{ "path": "agent.txt", "sha256": source_sha, "size": 5 }],
        "dependencies": {},
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful.",
        "tools": []
    });
    let candidate = json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": id,
        "agent_id": id,
        "files": [],
        "agent_snapshot": agent
    });
    assert_eq!(
        normalize_record(&candidate).unwrap_err().to_string(),
        "candidate agent identity does not match its evaluated snapshot"
    );

    let agent_id = record_id(&candidate["agent_snapshot"]).unwrap();
    let staged = json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": id,
        "agent_id": agent_id,
        "files": [{ "path": "agent.txt", "content": "hellø", "sha256": "" }],
        "agent_snapshot": candidate["agent_snapshot"].clone()
    });
    assert_eq!(
        normalize_record(&staged).unwrap_err().to_string(),
        "staged payload is not bound to the evaluated snapshot; capture files before evaluation"
    );

    let secret_reason = json!({
        "schema_version": 1,
        "kind": "decision",
        "baseline_run_id": id,
        "candidate_run_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "baseline_agent_id": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "candidate_agent_id": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "accepted": false,
        "reasons": ["api_key=supersecretvalue"],
        "aggregates": { "score": 1 },
        "regressions": [],
        "gate": { "minimum_score": 0.8 }
    });
    assert_eq!(
        normalize_record(&secret_reason).unwrap_err().to_string(),
        "secret-bearing configuration assignment is not evidence"
    );
}

#[test]
fn optional_fields_and_config_files_match_python_records() {
    let id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let agent_without_tools = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [],
        "dependencies": {},
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful."
    });
    assert_eq!(
        normalize_record(&agent_without_tools).unwrap()["tools"],
        json!([])
    );

    let run_without_error = json!({
        "schema_version": 1,
        "kind": "run",
        "agent_id": id,
        "dataset_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "evaluator": { "name": "judge", "version": "1", "configuration": {} },
        "split": "heldout",
        "expected_ids": [id],
        "repeats": 1,
        "results": [{ "example_id": id, "repetition": 0, "metrics": { "score": 1 } }]
    });
    assert_eq!(
        normalize_record(&run_without_error).unwrap()["results"][0]["error"],
        json!(null)
    );

    let source_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    let agent = json!({
        "schema_version": 1,
        "kind": "agent",
        "source_files": [{ "path": "notes.md", "sha256": source_sha, "size": 0 }],
        "dependencies": {},
        "model": { "deployment": "baseline" },
        "instructions": "Be helpful.",
        "tools": []
    });
    let agent_id = record_id(&agent).unwrap();
    let empty_markdown = json!({
        "schema_version": 1,
        "kind": "candidate",
        "baseline_id": id,
        "agent_id": agent_id,
        "files": [{ "path": "notes.md", "content": "" }],
        "agent_snapshot": agent
    });
    assert!(normalize_record(&empty_markdown).is_ok());

    for (path, content, message) in [
        (
            "settings.xml",
            "<client_secret>synthetic-secret</client_secret>",
            "unsupported config format; use JSON, YAML, TOML, Markdown, or text",
        ),
        ("tools.json", "{\"value\":NaN}", "expected value"),
        ("dupe.json", "{\"a\":1,\"a\":2}", "duplicate JSON key"),
        (
            "settings.yaml",
            "value: !!python/object:builtins.object {}\n",
            "invalid safe YAML configuration",
        ),
    ] {
        let candidate = json!({
            "schema_version": 1,
            "kind": "candidate",
            "baseline_id": id,
            "agent_id": agent_id,
            "files": [{ "path": path, "content": content }],
            "agent_snapshot": empty_markdown["agent_snapshot"].clone()
        });
        assert!(normalize_record(&candidate)
            .unwrap_err()
            .to_string()
            .contains(message));
    }
}

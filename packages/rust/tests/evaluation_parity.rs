use castia::evaluation::{
    build_generate_argv, build_run_argv, build_update_argv, load_suite, read_rubric, validate_suite,
};
use serde_json::json;
use std::fs;

fn temp_dir(label: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "castia-evaluation-parity-{}-{}",
        label,
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn generate_argv_matches_python_builder() {
    assert_eq!(
        build_generate_argv(&json!({ "outFile": "" })),
        json!(["azd", "ai", "agent", "eval", "generate"])
    );
    assert_eq!(
        build_generate_argv(&json!({
            "agent": "hal",
            "genInstructionFile": ".agent_configs/baseline/instructions.md",
            "evalModel": "gpt-4o",
            "maxSamples": 15,
            "evaluators": ["builtin.task_adherence", "smoke-core"],
            "outFile": "eval.generated.yaml",
            "resetDefaults": true,
            "noWait": true,
            "noPrompt": true,
        })),
        json!([
            "azd",
            "ai",
            "agent",
            "eval",
            "generate",
            "--agent",
            "hal",
            "--gen-instruction-file",
            ".agent_configs/baseline/instructions.md",
            "--eval-model",
            "gpt-4o",
            "--max-samples",
            "15",
            "--evaluator",
            "builtin.task_adherence",
            "--evaluator",
            "smoke-core",
            "--out-file",
            "eval.generated.yaml",
            "--reset-defaults",
            "--no-wait",
            "--no-prompt",
        ])
    );
}

#[test]
fn update_and_run_argv_match_python_builders() {
    assert_eq!(
        build_update_argv(&json!({
            "config": "eval.generated.yaml",
            "evaluatorOnly": true,
            "noPrompt": true,
        })),
        json!([
            "azd",
            "ai",
            "agent",
            "eval",
            "update",
            "--config",
            "eval.generated.yaml",
            "--evaluator-only",
            "--no-prompt",
        ])
    );
    assert_eq!(
        build_run_argv(&json!({
            "config": "eval.yaml",
            "name": "nightly",
            "noWait": true,
        })),
        json!([
            "azd",
            "ai",
            "agent",
            "eval",
            "run",
            "--config",
            "eval.yaml",
            "--name",
            "nightly",
            "--no-wait",
        ])
    );
    assert_eq!(
        build_update_argv(&json!({ "config": null, "evaluatorOnly": "yes" })),
        json!(["azd", "ai", "agent", "eval", "update", "--evaluator-only"])
    );
    assert_eq!(
        build_run_argv(&json!({ "config": 2024, "name": true, "noWait": 1 })),
        json!([
            "azd",
            "ai",
            "agent",
            "eval",
            "run",
            "--config",
            "2024",
            "--name",
            "True",
            "--no-wait",
        ])
    );
}

#[test]
fn read_rubric_matches_python_dimension_rules() {
    assert_eq!(
        read_rubric(&json!([
            { "name": "legacy_slug", "description": "d", "weight": 1 },
            { "id": "wins", "name": "loses", "description": "d" },
            "ignored",
        ])),
        json!([
            { "id": "legacy_slug", "description": "d", "weight": 1, "always_applicable": false },
            { "id": "wins", "description": "d", "weight": null, "always_applicable": false },
        ])
    );
    assert_eq!(
        read_rubric(&json!({ "dimensions": [{ "id": "only", "description": "d", "weight": 2 }] })),
        json!([{ "id": "only", "description": "d", "weight": 2, "always_applicable": false }])
    );
    assert_eq!(read_rubric(&json!({ "notDimensions": [] })), json!([]));
    assert_eq!(
        read_rubric(&json!([
            { "id": "", "name": 2024, "description": 5, "always_applicable": "yes" },
            { "id": null, "name": "fallback", "description": "", "always_applicable": 0 },
        ])),
        json!([
            { "id": "2024", "description": "5", "weight": null, "always_applicable": true },
            { "id": "fallback", "description": "", "weight": null, "always_applicable": false },
        ])
    );
}

#[test]
fn load_suite_matches_python_parser_shapes() {
    let dir = temp_dir("load-suite");
    let path = dir.join("eval.yaml");
    fs::write(
        &path,
        "name: smoke\nagent:\n  name: hal\nevaluators:\n  - builtin.task_adherence\n  - name: smoke-core\n    kind: custom\n    local_uri: rubric.json\n    version: 1\ndataset:\n  local_uri: eval.jsonl\n  version: 2\nvalidation_dataset:\n  dataset_file: validation.jsonl\noptions:\n  eval_model: gpt-4o\n",
    )
    .unwrap();

    let suite = load_suite(&path).unwrap();
    assert_eq!(suite["name"], "smoke");
    assert_eq!(suite["agent"], json!({ "name": "hal" }));
    assert_eq!(
        suite["evaluators"],
        json!([
            { "name": "builtin.task_adherence", "kind": null, "local_uri": null, "version": null, "builtin": true },
            { "name": "smoke-core", "kind": "custom", "local_uri": "rubric.json", "version": "1", "builtin": false },
        ])
    );
    assert_eq!(
        suite["datasets"],
        json!([
            { "role": "dataset", "local_uri": "eval.jsonl", "dataset_file": null, "version": "2" },
            { "role": "validation_dataset", "local_uri": null, "dataset_file": "validation.jsonl", "version": null },
        ])
    );
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn validate_suite_matches_python_referential_integrity_gate() {
    let dir = temp_dir("validate-suite");
    let path = dir.join("eval.yaml");
    fs::write(
        dir.join("rubric.json"),
        r#"[{"id":"correct_outcome","description":"Correct","weight":10},{"id":"general_quality","description":"General","always_applicable":true}]"#,
    )
    .unwrap();
    fs::write(
        &path,
        "agent:\n  name: hal\nevaluators:\n  - name: smoke-core\n    version: '1'\n    local_uri: rubric.json\n",
    )
    .unwrap();
    assert_eq!(
        validate_suite(&path).unwrap(),
        json!({ "ok": true, "problems": [], "notes": [], "rubrics": [{ "name": "smoke-core", "count": 2 }] })
    );

    fs::write(
        &path,
        "agent:\n  name: hal\nevaluators:\n  - name: smoke-core\n    local_uri: missing.json\n",
    )
    .unwrap();
    assert_eq!(
        validate_suite(&path).unwrap(),
        json!({
            "ok": false,
            "problems": ["evaluator 'smoke-core': rubric dimensions file 'missing.json' does not exist"],
            "notes": [],
            "rubrics": [],
        })
    );

    fs::write(&path, "agent:\n  name: hal\n").unwrap();
    assert_eq!(
        validate_suite(&path).unwrap(),
        json!({
            "ok": true,
            "problems": [],
            "notes": ["no evaluators declared -- run 'python -m castia eval generate' to synthesize a rubric, or add builtin.<name>"],
            "rubrics": [],
        })
    );

    fs::write(
        &path,
        "evaluators:\n  - name: smoke-core\n    local_uri: first.json\n  - name: smoke-core\n    local_uri: second.json\n",
    )
    .unwrap();
    fs::write(
        dir.join("first.json"),
        r#"[{"id":"first","description":"First"}]"#,
    )
    .unwrap();
    fs::write(
        dir.join("second.json"),
        r#"[{"id":"second","description":"Second"},{"id":"general","description":"General"}]"#,
    )
    .unwrap();
    assert_eq!(
        validate_suite(&path).unwrap(),
        json!({ "ok": true, "problems": [], "notes": [], "rubrics": [{ "name": "smoke-core", "count": 2 }] })
    );

    fs::write(
        &path,
        "evaluators:\n  - name: smoke-core\n    local_uri: rubric-dir\n",
    )
    .unwrap();
    fs::create_dir_all(dir.join("rubric-dir")).unwrap();
    let plan = validate_suite(&path).unwrap();
    assert_eq!(plan["ok"], false);
    assert!(plan["problems"][0]
        .as_str()
        .unwrap()
        .starts_with("evaluator 'smoke-core': cannot read rubric 'rubric-dir': "));
    let _ = fs::remove_dir_all(dir);
}

use castia::evaluation::{build_generate_argv, build_run_argv, build_update_argv, read_rubric};
use serde_json::json;

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

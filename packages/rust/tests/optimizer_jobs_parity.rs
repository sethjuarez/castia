use castia::optimizing::{
    best_optimizer_candidate_id, optimizer_candidate_apply_plan, optimizer_job_id,
    optimizer_request, optimizer_rest_request, terminal_optimizer_status,
};
use serde_json::json;

#[test]
fn optimizer_request_inlines_baseline_tools_and_dataset() {
    let request = optimizer_request(
        &json!({
            "agent": {"name": "optimizer-smoke"},
            "evaluators": [{"name": "smoke-core", "version": "1"}],
            "dataset": {"localUri": "data.jsonl"},
            "options": {"evalModel": "gpt-4o", "optimizationModel": "gpt-5-mini", "maxCandidates": 2},
        }),
        &json!({
            "model": "gpt-4o",
            "instructions": "Use the web tool carefully.\n",
            "tools": [{"type": "function", "function": {"name": "web"}}],
        }),
        &json!([{"query": "hello", "answer": "world"}]),
        &json!(null),
        None,
        None,
        None,
        None,
        None,
    )
    .unwrap();

    assert_eq!(request["agent"]["agent_name"], "optimizer-smoke");
    assert_eq!(request["agent"]["agent_version"], "");
    assert_eq!(request["options"]["eval_model"], "gpt-4o");
    assert_eq!(request["options"]["optimization_model"], "gpt-5-mini");
    assert_eq!(request["options"]["max_candidates"], 2);
    assert_eq!(
        request["options"]["optimization_config"]["system_prompt"],
        "Use the web tool carefully.\n"
    );
    assert_eq!(
        request["options"]["optimization_config"]["tools"][0]["function"]["name"],
        "web"
    );
    assert_eq!(
        request["train_dataset"],
        json!({"type": "inline", "items": [{"query": "hello", "answer": "world"}]})
    );
}

#[test]
fn optimizer_request_accepts_dataset_references_and_overrides() {
    let request = optimizer_request(
        &json!({
            "agent": {"name": "from-config", "version": "v1"},
            "evaluators": ["smoke-core"],
            "dataset": {"name": "smoke-data", "version": 3},
            "options": {"eval_model": "gpt-old", "optimization_model": "gpt-old-opt"},
        }),
        &json!({}),
        &json!(null),
        &json!(null),
        Some("from-cli"),
        Some("v2"),
        Some("gpt-eval"),
        Some("gpt-opt"),
        Some(4),
    )
    .unwrap();

    assert_eq!(
        request["agent"],
        json!({"agent_name": "from-cli", "agent_version": "v2"})
    );
    assert_eq!(request["options"]["eval_model"], "gpt-eval");
    assert_eq!(request["options"]["optimization_model"], "gpt-opt");
    assert_eq!(request["options"]["max_candidates"], 4);
    assert_eq!(
        request["train_dataset"],
        json!({"type": "reference", "name": "smoke-data", "version": "3"})
    );
}

#[test]
fn optimizer_request_validation_matches_python_errors() {
    assert_eq!(
        optimizer_request(
            &json!({"evaluators": ["smoke"], "dataset": {"name": "data"}, "options": {"evalModel": "gpt-4o", "optimizationModel": "gpt-5-mini"}}),
            &json!({}),
            &json!(null),
            &json!(null),
            None,
            None,
            None,
            None,
            None,
        )
        .unwrap_err()
        .to_string(),
        "agent.name is required (or pass --agent)"
    );
    assert_eq!(
        optimizer_request(
            &json!({"agent": {"name": "a"}, "evaluators": ["smoke"], "dataset": {"name": "data"}, "options": {"evalModel": "gpt-4o", "optimizationModel": "gpt-5-mini", "maxCandidates": 0}}),
            &json!({}),
            &json!(null),
            &json!(null),
            None,
            None,
            None,
            None,
            None,
        )
        .unwrap_err()
        .to_string(),
        "max_candidates must be >= 1"
    );
}

#[test]
fn optimizer_rest_request_paths_match_python_client() {
    assert_eq!(
        optimizer_rest_request(
            "https://example.ai.azure.com/api/projects/p/",
            "start",
            None,
            None,
            &json!({"x": 1}),
        )
        .unwrap(),
        json!({
            "method": "POST",
            "url": "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs?api-version=v1",
            "headers": {
                "Foundry-Features": "AgentsOptimization=V2Preview",
                "Content-Type": "application/json",
            },
            "body": {"inputs": {"x": 1}},
        })
    );
    assert_eq!(
        optimizer_rest_request(
            "https://example.ai.azure.com/api/projects/p",
            "candidate_config",
            Some("opt_1"),
            Some("cand_1"),
            &json!(null),
        )
        .unwrap()["url"],
        "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1/candidates/cand_1/config?api-version=v1"
    );
}

#[test]
fn optimizer_status_extraction_matches_python() {
    assert!(terminal_optimizer_status(Some("cancelled")));
    assert!(terminal_optimizer_status(Some("canceled")));
    assert!(!terminal_optimizer_status(Some("running")));
    assert_eq!(
        best_optimizer_candidate_id(&json!({"result": {"bestCandidateId": "cand_2"}})).as_deref(),
        Some("cand_2")
    );
    assert_eq!(
        best_optimizer_candidate_id(&json!({"result": {"candidates": [
            {"candidate_id": "low", "avg_score": 0.1},
            {"candidateId": "high", "score": 0.9}
        ]}}))
        .as_deref(),
        Some("high")
    );
    assert_eq!(
        optimizer_job_id(&json!({"operationId": "opt_1"})).as_deref(),
        Some("opt_1")
    );
}

#[test]
fn optimizer_candidate_apply_plan_matches_runtime_candidate_layout() {
    assert_eq!(
        optimizer_candidate_apply_plan(
            "cand_1",
            &json!({
                "model": "gpt-5-mini",
                "temperature": 0,
                "system_prompt": "Prefer short answers.",
                "tools": [{"type": "function", "function": {"name": "web"}}],
                "skills": [{"name": "tone", "description": "Voice", "body": "Be crisp."}],
            }),
        ),
        json!({
            "candidateDir": "cand_1",
            "metadata": "model: gpt-5-mini\ntemperature: 0\ninstruction_file: instructions.md\ntool_file: tools.json\ntools_file: tools.json\nskill_dir: skills\n",
            "instructions": "Prefer short answers.",
            "tools": "[\n  {\n    \"function\": {\n      \"name\": \"web\"\n    },\n    \"type\": \"function\"\n  }\n]\n",
            "skillFiles": [{
                "path": "skills/tone/SKILL.md",
                "content": "---\nname: tone\ndescription: Voice\n---\nBe crisp.\n",
            }],
        })
    );
}

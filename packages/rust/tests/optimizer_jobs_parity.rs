use castia::optimizing::{
    best_optimizer_candidate_id, optimizer_candidate_apply_plan, optimizer_job_id,
    optimizer_request, optimizer_rest_request, optimizer_tool_names,
    resolve_optimizer_candidate_config, terminal_optimizer_status,
};
use serde_json::json;

#[test]
fn optimizer_request_inlines_baseline_tools_and_dataset() {
    let request = optimizer_request(
        &json!({
            "agent": {"name": "optimizer-smoke"},
            "evaluators": [{"name": "smoke-core", "version": 1}],
            "dataset": {"localUri": "data.jsonl"},
            "options": {"evalModel": "gpt-4o", "optimizationModel": "gpt-5-mini", "maxCandidates": 2},
        }),
        &json!({
            "model": "gpt-4o",
            "instructions": "Use the web tool carefully.\n",
            "tools": [{"type": "function", "function": {"name": "web"}}],
        }),
        &json!([{"query": "hello", "expected": {"b": 1, "a": [1, 2]}}]),
        &json!(null),
        None,
        None,
        None,
        None,
        None,
    )
    .unwrap();

    assert_eq!(
        request["target_configuration"],
        json!({"type": "foundry_agent", "name": "optimizer-smoke"})
    );
    let opt = &request["optimization_configuration"];
    assert_eq!(opt["candidate_search_configuration"]["max_candidates"], 2);
    assert_eq!(
        opt["evaluation_configuration"]["evaluators"],
        json!([{"name": "smoke-core", "version": "1"}])
    );
    assert_eq!(
        opt["evaluation_configuration"]["training_set"]["source"]["test_cases"],
        json!([{"query": "hello", "ground_truth": "{\"a\": [1, 2], \"b\": 1}"}])
    );
    assert_eq!(
        opt["baseline_agent_configuration"]["tools"][0]["function"]["name"],
        "web"
    );
}

#[test]
fn optimizer_request_builds_skill_optimization_from_traces() {
    let request = optimizer_request(
        &json!({
            "agent": {"name": "optimizer-smoke"},
            "evaluators": ["smoke-core"],
            "traces": {
                "resource_id": " /subscriptions/s/appinsights/a ",
                "window": {"start_time": "2026-01-01 00:00:00Z", "end_time": "2026-01-02T00:00:00.5+02:00", "max_traces": 50},
                "training_split_ratio": 0.8,
            },
            "options": {"eval_model": "gpt-4o", "optimization_model": "gpt-5-mini", "max_concurrent_agent_runs": 3},
        }),
        &json!({}),
        &json!(null),
        &json!(null),
        None,
        None,
        None,
        None,
        None,
    )
    .unwrap();

    assert_eq!(
        request["optimization_configuration"],
        json!({
            "type": "skill_optimization",
            "context": [{
                "type": "agent_traces",
                "trace_connection_configuration": {
                    "type": "application_insights",
                    "resource_id": "/subscriptions/s/appinsights/a",
                    "agent_name": "optimizer-smoke",
                },
                "trace_window": {
                    "type": "fixed",
                    "start_time": "2026-01-01T00:00:00+00:00",
                    "end_time": "2026-01-02T00:00:00.500000+02:00",
                    "max_traces": 50,
                },
            }],
            "evaluation_configuration": {
                "evaluation_model": {"model": "gpt-4o"},
                "evaluators": [{"name": "smoke-core"}],
                "max_concurrent_agent_runs": 3,
            },
            "training_split_ratio": 0.8,
        })
    );
    assert_eq!(
        optimizer_request(
            &json!({
                "agent": {"name": "a"},
                "evaluators": ["smoke"],
                "dataset": {"name": "d", "version": "1"},
                "traces": {"resource_id": "r", "window": {"trace_ids": ["t"], "lookback_hours": 2}},
                "options": {"eval_model": "gpt-4o", "optimization_model": "gpt-5-mini"},
            }),
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
        "traces: runs skill optimization from agent traces and cannot be combined with dataset"
    );
}

#[test]
fn optimizer_tool_names_match_config_helper() {
    assert_eq!(
        optimizer_tool_names(&json!([
            {"type": "function", "function": {"name": "nested"}},
            {"type": "function", "name": "flat"},
            {"type": "mcp", "serverLabel": "graph"},
        ])),
        vec!["nested".to_string(), "flat".to_string()]
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
        request["target_configuration"],
        json!({"type": "foundry_agent", "name": "from-cli", "version": "v2"})
    );
    assert_eq!(
        request["optimization_model_configuration"],
        json!({"model": "gpt-opt"})
    );
    let opt = &request["optimization_configuration"];
    assert_eq!(
        opt["evaluation_configuration"]["evaluation_model"],
        json!({"model": "gpt-eval"})
    );
    assert_eq!(opt["candidate_search_configuration"]["max_candidates"], 4);
    assert_eq!(
        opt["evaluation_configuration"]["training_set"]["source"],
        json!({"type": "dataset_reference", "name": "smoke-data", "version": "3"})
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
            "headers": {"Content-Type": "application/json"},
            "body": {"x": 1},
        })
    );
    assert_eq!(
        optimizer_rest_request(
            "https://example.ai.azure.com/api/projects/p",
            "candidate_config_file",
            Some("opt 1/x"),
            Some("cand 1"),
            &json!(null),
        )
        .unwrap()["url"],
        "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt%201%2Fx/files?path=config.json&candidate_id=cand+1&api-version=v1"
    );
    assert_eq!(
        optimizer_rest_request(
            "https://example.ai.azure.com/api/projects/p",
            "candidate",
            Some("opt_1"),
            None,
            &json!(null),
        )
        .unwrap_err()
        .to_string(),
        "candidateId is required"
    );
}

#[test]
fn optimizer_status_extraction_matches_python() {
    assert!(terminal_optimizer_status(Some("Cancelled")));
    assert!(terminal_optimizer_status(Some("skipped")));
    assert!(!terminal_optimizer_status(Some("canceled")));
    assert!(!terminal_optimizer_status(Some("running")));
    assert_eq!(
        best_optimizer_candidate_id(
            &json!({"result": {"candidate_summary": {"baseline_id": "cand_0", "best_id": "cand_2"}}})
        )
        .as_deref(),
        Some("cand_2")
    );
    assert_eq!(
        best_optimizer_candidate_id(&json!({"result": {"bestCandidateId": "cand_2"}})),
        None
    );
    assert_eq!(
        optimizer_job_id(&json!({"job_id": "opt_1"})).as_deref(),
        Some("opt_1")
    );
    assert_eq!(optimizer_job_id(&json!({"operationId": "opt_1"})), None);
}

#[test]
fn resolve_candidate_config_layers_config_file_and_mutations() {
    let job = json!({"optimization_configuration": {
        "type": "agent_optimization",
        "baseline_agent_configuration": {
            "system_prompt": "base",
            "current_model": "gpt-4o",
            "tools": [{"type": "function", "function": {"name": "web"}}],
            "skills": [],
        },
    }});
    let candidate = json!({
        "candidate_id": "cand_1",
        "status": "completed",
        "output": {"mutations": [
            {"type": "instructions", "value": "ignored when config.json exists"},
            {"type": "tools", "value": [
                {"type": "function", "function": {"name": "web", "description": "new"}},
                {"type": "function", "function": {"name": "search"}},
            ]},
        ]},
    });
    assert_eq!(
        resolve_optimizer_candidate_config(
            &job,
            &candidate,
            Some(&json!({"instructions": "tuned"}))
        )
        .unwrap(),
        json!({
            "instructions": "tuned",
            "model": "gpt-4o",
            "tools": [
                {"type": "function", "function": {"name": "web", "description": "new"}},
                {"type": "function", "function": {"name": "search"}},
            ],
        })
    );
    assert_eq!(
        resolve_optimizer_candidate_config(
            &job,
            &json!({"candidate_id": "cand_0", "status": "completed"}),
            None
        )
        .unwrap_err()
        .to_string(),
        "candidate cand_0 has no config.json and no mutations (it is likely the baseline); nothing to apply"
    );
    assert_eq!(
        resolve_optimizer_candidate_config(
            &job,
            &json!({"candidate_id": "cand_2", "output": {"mutations": [{"type": "temperature", "value": 1}]}}),
            None
        )
        .unwrap_err()
        .to_string(),
        "candidate cand_2: unsupported or malformed mutation 'temperature'"
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

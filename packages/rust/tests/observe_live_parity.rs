use castia::observe::{
    candidate_config, optimizer_status, response_text_result, stream_result, validate_limits,
    validate_live_config,
};
use serde_json::{json, Value};

#[test]
fn limits_are_positive_finite_and_consistent() {
    for limits in [
        json!({"max_requests": 0}),
        json!({"max_requests": true}),
        json!({"max_requests": 1.5}),
        json!({"max_seconds": -1}),
        json!({"max_output_tokens": 0}),
        json!({"max_output_tokens": 2000}),
        json!({"max_total_output_tokens": -1}),
        json!({"max_response_bytes": 0}),
    ] {
        assert!(validate_limits(&limits).is_err(), "{limits}");
    }
}

#[test]
fn live_config_urls_are_explicit_https_and_never_finetuning() {
    for url in [
        "http://example.com",
        "******example.com",
        "https://example.com/#token",
        "https://user:pass@example.com/path",
        "not-url",
        "https://example.com/fine_tuning/jobs",
        "https://example.com/fine-tuning/jobs",
        "https://example.com/fine%5ftuning/jobs",
    ] {
        assert!(
            validate_live_config(&json!({"hosted_responses_url": url})).is_err(),
            "{url}"
        );
    }
    let config = validate_live_config(&json!({})).unwrap();
    assert_eq!(config["allow_optimizer_submit"], false);
    assert_eq!(
        config["project_read_scope"],
        "https://ai.azure.com/.default"
    );
}

#[test]
fn optimizer_submit_requires_opt_in_request_and_candidate_bound() {
    for config in [
        json!({"project_endpoint": "https://project.example/api/projects/demo", "allow_optimizer_submit": true, "optimizer_request": {}}),
        json!({"project_endpoint": "https://project.example/api/projects/demo", "allow_optimizer_submit": true, "optimizer_request": {"options": {"max_candidates": 4}}}),
        json!({"project_endpoint": "https://project.example/api/projects/demo", "allow_optimizer_submit": true, "optimizer_request": {"options": {"max_candidates": true}}}),
    ] {
        assert!(validate_live_config(&config).is_err(), "{config}");
    }
}

#[test]
fn response_text_result_reports_usage_not_content_or_secret_errors() {
    let response = json!({
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "CASTIA_OK"}]}],
        "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
    });
    let result = response_text_result(&response, Some("CASTIA_OK"));
    assert_eq!(result["status"], "pass");
    assert_eq!(result["evidence"]["usage"]["input_tokens"], 7);
    assert!(!result.to_string().contains("CASTIA_OK"));

    let failed = response_text_result(
        &json!({"status": "failed", "error": {"message": "SECRET"}}),
        None,
    );
    assert_eq!(failed["category"], "service");
    assert!(!failed.to_string().contains("SECRET"));
}

#[test]
fn stream_requires_deltas_and_completed_event_without_failures() {
    let events = json!([
        {"type": "response.output_text.delta", "delta": "CASTIA_"},
        {"type": "response.output_text.delta", "delta": "OK"},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}},
    ]);
    assert_eq!(stream_result(&events)["status"], "pass");
    assert_eq!(
        stream_result(&json!([events[0].clone(), events[1].clone()]))["status"],
        "fail"
    );
    let mut failed = events.as_array().unwrap().clone();
    failed.push(json!({"type": "response.failed"}));
    assert_eq!(stream_result(&Value::Array(failed))["status"], "fail");
}

#[test]
fn optimizer_status_validates_schema_status_and_job_aliases() {
    for key in ["operation_id", "operationId", "id", "job_id", "jobId"] {
        assert_eq!(
            optimizer_status(&json!({key: "job", "status": "Completed"}), Some("job")).unwrap(),
            "completed"
        );
    }
    for payload in [
        json!({}),
        json!([]),
        json!({"status": 7}),
        json!({"status": "SECRET"}),
        json!({"status": "Succeeded", "operation_id": "wrong-job"}),
        json!({"status": "Succeeded", "result": []}),
        json!({"status": "Succeeded", "result": {"best": 7}}),
        json!({"status": "Succeeded", "result": {"candidates": ["bad-shape"]}}),
    ] {
        assert!(
            optimizer_status(&payload, Some("known-job")).is_err(),
            "{payload}"
        );
    }
}

#[test]
fn candidate_config_validates_known_fields_without_returning_prompt_content() {
    let valid = candidate_config(&json!({
        "model": "gpt-5-mini",
        "system_prompt": "SECRET",
        "tools": [{"type": "function"}],
        "skills": [],
        "temperature": 0,
    }))
    .unwrap();
    assert_eq!(valid, json!({"valid": true}));
    assert!(!valid.to_string().contains("SECRET"));

    for payload in [
        json!({}),
        json!([]),
        json!({"unexpected": "SECRET"}),
        json!({"model": 1}),
        json!({"model": ""}),
        json!({"system_prompt": []}),
        json!({"tools": "not-tools"}),
        json!({"skills": [7]}),
        json!({"temperature": true}),
        json!({"temperature": 5}),
    ] {
        assert!(candidate_config(&payload).is_err(), "{payload}");
    }
}

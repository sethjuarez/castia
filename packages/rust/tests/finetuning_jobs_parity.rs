use castia::finetuning::{
    deployment_handoff, download_guard, download_limit_exceeded, job_page_request, job_reference,
    page_request, page_slice, parse_job_reference, result_files, status_request, terminal_status,
    validate_request_timeout, watch_poll_timeout, watch_request, watch_sleep_duration,
};
use serde_json::json;

const ENDPOINT: &str = "https://example.services.ai.azure.com/api/projects/test";

#[test]
fn job_reference_matches_python_validation() {
    assert_eq!(
        job_reference(ENDPOINT, "job-1", 1).unwrap(),
        json!({
            "project_endpoint": ENDPOINT,
            "job_id": "job-1",
            "schema_version": 1,
        })
    );
    assert_eq!(
        job_reference("https://example.test/project?x=1", "job-1", 1)
            .unwrap_err()
            .to_string(),
        "project_endpoint must be HTTPS without credentials or query"
    );
    assert_eq!(
        job_reference("https://example.test/project?", "job-1", 1).unwrap()["project_endpoint"],
        json!("https://example.test/project?")
    );
    assert_eq!(
        job_reference(ENDPOINT, "../other", 1)
            .unwrap_err()
            .to_string(),
        "job_id must be a nonempty service identifier"
    );
    assert_eq!(
        job_reference(ENDPOINT, "job-1", 2).unwrap_err().to_string(),
        "unsupported job-reference schema_version"
    );
    assert_eq!(
        parse_job_reference(
            &json!({"project_endpoint": ENDPOINT, "job_id": "job-1", "schema_version": 1})
        )
        .unwrap(),
        job_reference(ENDPOINT, "job-1", 1).unwrap()
    );
    assert_eq!(
        parse_job_reference(
            &json!({"project_endpoint": ENDPOINT, "job_id": "job-1", "schema_version": 1, "token": "secret"})
        )
        .unwrap_err()
        .to_string(),
        "invalid fine-tuning job reference"
    );
}

#[test]
fn requests_are_explicit_and_bounded() {
    assert_eq!(
        page_request(&json!(2), Some("job-old")).unwrap(),
        json!({"limit": 2, "after": "job-old"})
    );
    assert_eq!(
        page_request(&json!(20), None).unwrap(),
        json!({"limit": 20})
    );
    assert_eq!(
        job_page_request("job-1", &json!(1), Some("cursor-1")).unwrap(),
        json!({"job_id": "job-1", "limit": 1, "after": "cursor-1"})
    );
    assert_eq!(
        job_page_request("bad/path", &json!(0), None)
            .unwrap_err()
            .to_string(),
        "limit must be an integer between 1 and 100"
    );
    assert_eq!(
        page_slice(&json!(["a", "b", "c"]), &json!(2)).unwrap(),
        json!(["a", "b"])
    );
    assert_eq!(
        page_slice(&json!({"not": "array"}), &json!(1))
            .unwrap_err()
            .to_string(),
        "page data must be an array"
    );
    assert_eq!(
        page_request(&json!(true), None).unwrap_err().to_string(),
        "limit must be an integer between 1 and 100"
    );
    assert_eq!(
        page_request(&json!(20), Some("bad/path"))
            .unwrap_err()
            .to_string(),
        "after must be a nonempty service identifier"
    );
    assert_eq!(
        status_request("job-1", 30.0, Some(1.0)).unwrap(),
        json!({"job_id": "job-1", "timeout": 1})
    );
    assert_eq!(validate_request_timeout(30.0).unwrap(), json!(30));
    assert_eq!(
        status_request("job-1", 30.0, Some(0.0))
            .unwrap_err()
            .to_string(),
        "timeout must be finite and positive"
    );
    assert_eq!(
        watch_request("job-1", 300.0, 10.0).unwrap(),
        json!({"job_id": "job-1", "timeout": 300, "poll_interval": 10})
    );
    assert_eq!(watch_poll_timeout(30.0, 1.0).unwrap(), json!(1));
    assert_eq!(watch_sleep_duration(10.0, 3.0).unwrap(), json!(3));
    assert_eq!(watch_sleep_duration(10.0, -1.0).unwrap(), json!(0));
}

#[test]
fn terminal_status_matches_python_watch_loop() {
    for status in ["succeeded", "failed", "cancelled", "canceled"] {
        assert!(terminal_status(status));
    }
    assert!(!terminal_status("running"));
}

#[test]
fn result_file_guards_do_not_download_or_deploy() {
    let job = json!({"result_files": ["file-1", "file-2"]});
    assert_eq!(result_files(&job), vec!["file-1", "file-2"]);
    assert_eq!(
        download_guard(&job, "file-1", &json!(4 * 1024 * 1024)).unwrap(),
        json!({"file_id": "file-1", "max_bytes": 4 * 1024 * 1024})
    );
    assert_eq!(
        download_guard(&job, "other-file", &json!(4 * 1024 * 1024))
            .unwrap_err()
            .to_string(),
        "file_id is not a result file of the specified job"
    );
    assert_eq!(
        download_guard(&job, "file-1", &json!(0))
            .unwrap_err()
            .to_string(),
        "max_bytes must be a positive integer"
    );
    assert!(!download_limit_exceeded(&json!(4), &json!(4)).unwrap());
    assert!(download_limit_exceeded(&json!(5), &json!(4)).unwrap());
}

#[test]
fn deployment_handoff_preserves_external_deploy_boundary() {
    let job = json!({
        "status": "succeeded",
        "id": "job-1",
        "fine_tuned_model": "ft:model:1",
        "result_files": ["file-result"],
    });
    assert_eq!(
        deployment_handoff(Some(ENDPOINT), &job).unwrap(),
        json!({
            "schema_version": 1,
            "job_id": "job-1",
            "project_endpoint": ENDPOINT,
            "fine_tuned_model": "ft:model:1",
            "result_files": ["file-result"],
            "deployment_status": "not_deployed",
            "next_step": "Deploy the selected model externally, then evaluate the deployment.",
        })
    );
    assert_eq!(
        deployment_handoff(Some(ENDPOINT), &json!({"status": "running"}))
            .unwrap_err()
            .to_string(),
        "deployment handoff requires a succeeded job with a model"
    );
    assert_eq!(
        deployment_handoff(
            Some(ENDPOINT),
            &json!({"status": "succeeded", "fine_tuned_model": "ft:model:1"})
        )
        .unwrap_err()
        .to_string(),
        "deployment handoff requires a succeeded job with a model"
    );
}

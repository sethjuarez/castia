use castia::building::{project_test_report, validate_test_timeout};
use serde_json::json;

#[test]
fn project_test_report_matches_python_status_mapping() {
    assert_eq!(
        project_test_report("agent", 0, "2 passed", false),
        json!({
            "root": "agent",
            "status": "pass",
            "exit_code": 0,
            "output": "2 passed",
            "ok": true,
            "scope": "offline",
        })
    );
    assert_eq!(
        project_test_report("agent", 1, "failure", false)["status"],
        "fail"
    );
    assert_eq!(
        project_test_report("agent", 2, "error", false)["status"],
        "error"
    );
    assert_eq!(
        project_test_report("agent", 99, "ignored", true),
        json!({
            "root": "agent",
            "status": "timeout",
            "exit_code": 124,
            "output": "Local tests exceeded the timeout.",
            "ok": false,
            "scope": "offline",
        })
    );
}

#[test]
fn test_timeout_requires_positive_finite_seconds() {
    assert_eq!(validate_test_timeout(60.0).unwrap(), 60.0);
    for timeout in [0.0, -1.0, f64::INFINITY, f64::NAN] {
        assert!(validate_test_timeout(timeout).is_err(), "{timeout:?}");
    }
}

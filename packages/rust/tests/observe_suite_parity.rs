use castia::observe::{compare_reports, feature_catalog, run_suite, validate_suite};
use serde_json::json;

fn selected_status(report: &serde_json::Value, feature_id: &str) -> serde_json::Value {
    report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["selected"] == true && row["feature_id"] == feature_id)
        .unwrap()["status"]
        .clone()
}

fn report_rows(rows: &[(&str, &str)]) -> serde_json::Value {
    json!({
        "schema_version": 1,
        "results": rows.iter().map(|(feature_id, status)| {
            json!({"feature_id": feature_id, "selected": true, "status": status})
        }).collect::<Vec<_>>()
    })
}

#[test]
fn catalog_has_unique_ids_and_all_surfaces_without_finetune_submit() {
    let catalog = feature_catalog();
    let features = catalog["features"].as_array().unwrap();
    let ids = features
        .iter()
        .map(|feature| feature["id"].as_str().unwrap())
        .collect::<Vec<_>>();
    let unique = ids
        .iter()
        .copied()
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(ids.len(), unique.len());
    for surface in [
        "project",
        "runtime",
        "teams",
        "identity",
        "graph",
        "model",
        "tools",
        "telemetry",
        "eval",
        "optimizer",
        "finetune",
        "deploy",
    ] {
        assert!(ids.iter().any(|id| id.starts_with(&format!("{surface}."))));
    }
    assert!(!ids
        .iter()
        .any(|id| id.starts_with("finetune.") && id.contains("submit")));
}

#[test]
fn suite_rejects_invalid_coverage_and_unrecognized_controls() {
    for suite in [
        json!({"name": "daily", "features": []}),
        json!({"name": "daily", "features": ["model.respond", "model.respond"]}),
        json!({"name": "daily", "features": ["unknown"]}),
        json!({"name": "daily", "features": "model.respond"}),
        json!({"name": "daily", "features": [1]}),
        json!({"name": "daily", "features": ["model.respond"], "submit_finetune": true}),
        json!({"name": "daily", "features": ["model.respond"], "schema_version": 2}),
    ] {
        assert!(validate_suite(&suite).is_err());
    }
}

#[test]
fn missing_prerequisite_blocks_before_probe_and_no_probe_is_uncovered() {
    let report = run_suite(
        &json!({"name": "daily", "features": ["teams.direct", "model.respond", "graph.read"]}),
        &json!({
            "teams.direct": {"status": "pass", "diagnostic": "must not win"},
            "model.respond": {"status": "pass", "diagnostic": "ok"}
        }),
        &json!({"model": "model", "model_url": "url", "graph_fixture": true}),
        None,
        "2026-09-15T00:00:00Z",
    )
    .unwrap();
    assert_eq!(selected_status(&report, "teams.direct"), "blocked");
    assert_eq!(selected_status(&report, "model.respond"), "pass");
    assert_eq!(selected_status(&report, "graph.read"), "uncovered");
    assert_eq!(report["passed"], false);
}

#[test]
fn non_probe_results_and_probe_errors_are_categorized() {
    let invalid = run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({"project.read": true}),
        &json!({"project_read_url": "url"}),
        None,
        "2026-09-15T00:00:00Z",
    )
    .unwrap();
    let result = invalid["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["feature_id"] == "project.read")
        .unwrap();
    assert_eq!(result["status"], "fail");
    assert_eq!(result["category"], "invalid_response");
    assert_eq!(
        result["diagnostic"],
        "The service response did not match its contract."
    );

    let blocked = run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({"project.read": {"raises": {"category": "authorization", "diagnostic": "No access."}}}),
        &json!({"project_read_url": "url"}),
        None,
        "2026-09-15T00:00:00Z",
    )
    .unwrap();
    assert_eq!(selected_status(&blocked, "project.read"), "blocked");
}

#[test]
fn comparison_reports_regressions_and_lost_coverage() {
    let comparison = compare_reports(
        &report_rows(&[("model.respond", "pass")]),
        &report_rows(&[("model.respond", "pass"), ("model.stream", "pass")]),
    )
    .unwrap();
    assert_eq!(comparison["regressions"], json!(["model.stream"]));
    assert_eq!(comparison["lost_coverage"], json!(["model.stream"]));
    assert_eq!(
        comparison["changed"],
        json!([{"feature_id": "model.stream", "before": "pass", "after": null}])
    );
}

#[test]
fn baseline_regression_prevents_passed_report() {
    let report = run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({"project.read": {"status": "pass", "diagnostic": "ok"}}),
        &json!({"project_read_url": "url"}),
        Some(&report_rows(&[
            ("project.read", "pass"),
            ("model.respond", "pass"),
        ])),
        "2026-09-15T00:00:00Z",
    )
    .unwrap();
    assert_eq!(report["counts"]["pass"], 1);
    assert_eq!(
        report["comparison"]["lost_coverage"],
        json!(["model.respond"])
    );
    assert_eq!(
        report["comparison"]["regressions"],
        json!(["model.respond"])
    );
    assert_eq!(report["passed"], false);
}

#[test]
fn invalid_baseline_is_rejected_before_report_run() {
    assert!(run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({"project.read": {"status": "pass", "diagnostic": "ok"}}),
        &json!({"project_read_url": "url"}),
        Some(&json!({})),
        "2026-09-15T00:00:00Z",
    )
    .is_err());
}

#[test]
fn duplicate_baseline_rows_are_rejected_even_when_unselected() {
    assert!(compare_reports(
        &report_rows(&[("model.respond", "pass")]),
        &json!({
            "schema_version": 1,
            "results": [
                {"feature_id": "model.respond", "selected": false, "status": "uncovered"},
                {"feature_id": "model.respond", "selected": true, "status": "pass"}
            ]
        }),
    )
    .is_err());
}

#[test]
fn generated_at_and_mapping_inputs_are_validated() {
    assert!(run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({}),
        &json!({}),
        None,
        "",
    )
    .is_err());
    assert!(run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!(true),
        &json!({}),
        None,
        "2026-09-15T00:00:00Z",
    )
    .is_err());
    assert!(run_suite(
        &json!({"name": "daily", "features": ["project.read"]}),
        &json!({}),
        &json!(true),
        None,
        "2026-09-15T00:00:00Z",
    )
    .is_err());
}

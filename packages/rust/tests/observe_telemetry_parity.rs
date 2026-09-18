use castia::observe::{http_error, trace_query_kql, verify_probe};
use serde_json::json;

fn query(extra: serde_json::Value) -> serde_json::Value {
    let mut base = json!({
        "agent_name": "agent",
        "start": "2026-09-15T00:00:00Z",
        "end": "2026-09-15T01:00:00Z",
    });
    base.as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    base
}

#[test]
fn trace_query_agent_version_tag_bound_and_content_projection() {
    let kql = trace_query_kql(&query(json!({
        "limit": 19,
        "agent_version": "3",
        "probe_tag": "test-tag"
    })))
    .unwrap();
    assert!(kql.contains("requests, dependencies, customEvents"));
    assert!(kql.contains("timestamp >=") && kql.contains("timestamp <"));
    assert!(kql.contains("agent_name == \"agent\""));
    assert!(kql.contains("agent_id == \"agent:3\""));
    assert!(kql.contains("probe_tag == \"test-tag\""));
    assert!(kql.ends_with("| take 19"));
    assert!(kql.contains("customDimensions=bag_pack("));
    assert!(!kql.contains("gen_ai.input.messages"));
    assert!(trace_query_kql(&query(json!({"include_content": true})))
        .unwrap()
        .contains("gen_ai.input.messages"));
}

#[test]
fn trace_query_selects_hosted_requests_then_correlates_downstream_spans() {
    let kql = trace_query_kql(&query(json!({"agent_version": "3"}))).unwrap();
    let (selected_requests, child_query) = kql.split_once(";\n").unwrap();
    assert!(selected_requests.starts_with("let scoped_operations = requests"));
    assert!(selected_requests.contains("customDimensions[\"azure.ai.agentserver.agent_name\"]"));
    assert!(selected_requests.contains("agent_name == \"agent\" or hosted_agent_name == \"agent\""));
    assert!(selected_requests.contains("| where isnotempty(operation_Id)"));
    assert!(selected_requests.contains("summarize arg_max("));
    assert!(child_query.starts_with(
        "union isfuzzy=true withsource=castia_table_name requests, dependencies, customEvents"
    ));
    assert!(child_query.contains("| join kind=inner (scoped_operations) on operation_Id"));
    assert!(!child_query.contains("| where agent_name"));
    assert!(!child_query.contains("| where hosted_agent_name"));
    assert!(child_query.contains("agent_name=\"agent\", agent_version=root_agent_version"));
}

#[test]
fn tag_filter_applies_after_correlation_and_accepts_inherited_root_tag() {
    let kql = trace_query_kql(&query(json!({"probe_tag": "probe"}))).unwrap();
    let (selected_requests, child_query) = kql.split_once(";\n").unwrap();
    assert!(!selected_requests.contains("| where probe_tag == \"probe\""));
    assert!(child_query
        .contains("probe_tag=coalesce(tostring(customDimensions[\"castia.probe_tag\"]),"));
    assert!(child_query.contains("tostring(customDimensions[\"probe_id\"]), root_probe_tag)"));
    assert!(child_query.contains("| where probe_tag == \"probe\""));
    assert!(!selected_requests.contains("| take"));
    assert!(child_query.ends_with("| take 100"));
}

#[test]
fn trace_query_escapes_untrusted_agent_and_tag() {
    let name = "agent\"\n| take 100000\n//";
    let kql = trace_query_kql(&json!({
        "agent_name": name,
        "start": "2026-09-15T00:00:00Z",
        "end": "2026-09-15T01:00:00Z",
        "probe_tag": name
    }))
    .unwrap();
    assert!(kql.contains(&serde_json::to_string(name).unwrap()));
    assert!(!kql.contains("\n| take 100000"));
    let unicode = trace_query_kql(&json!({
        "agent_name": "café😀",
        "start": "2026-09-15T00:00:00Z",
        "end": "2026-09-15T01:00:00Z"
    }))
    .unwrap();
    assert!(unicode.contains("caf\\u00e9"));
    assert!(unicode.contains("\\ud83d\\ude00"));
}

#[test]
fn trace_query_preserves_subsecond_windows() {
    let kql = trace_query_kql(&json!({
        "agent_name": "agent",
        "start": "2026-09-15T00:00:00.500000Z",
        "end": "2026-09-15T01:00:00.250000Z"
    }))
    .unwrap();
    assert!(kql.contains(
        "| where timestamp >= datetime(2026-09-15T00:00:00.500000+00:00) and timestamp < datetime(2026-09-15T01:00:00.250000+00:00)"
    ));
}

#[test]
fn trace_query_rejects_invalid_options() {
    for query in [
        query(json!({"limit": 0})),
        query(json!({"limit": 10001})),
        query(json!({"include_content": "true"})),
        query(json!({"anchor": "any"})),
        query(json!({"anchor": 7})),
        query(json!({"probe_tag": ""})),
        query(json!({"agent_version": ""})),
        query(json!({"trace_id": ""})),
        query(json!({"start": "2026-09-15T00:00:00"})),
        query(json!({"end": "2026-09-15T00:00:00Z"})),
    ] {
        assert!(trace_query_kql(&query).is_err());
    }
}

#[test]
fn local_delivery_query_anchors_named_invoke_agent_spans() {
    let kql = trace_query_kql(&query(json!({
        "anchor": "invoke_agent",
        "trace_id": "trace-id",
        "probe_tag": "probe-id"
    })))
    .unwrap();
    let (anchor_query, child_query) = kql.split_once(";\n").unwrap();
    assert!(anchor_query
        .starts_with("let scoped_operations = union isfuzzy=true dependencies, customEvents"));
    assert!(
        anchor_query.contains("customDimensions[\"gen_ai.operation.name\"]) == \"invoke_agent\"")
    );
    assert!(anchor_query.contains("operation_Id == \"trace-id\""));
    assert!(child_query.contains("castia.probe_id"));
    assert!(child_query.contains("probe_tag == \"probe-id\""));
    assert!(!kql.contains("withsource=source "));
}

#[test]
fn http_errors_are_classified_without_raw_content() {
    for (status, category) in [
        (401, "authentication"),
        (403, "authorization"),
        (404, "not_found"),
        (429, "throttled"),
        (500, "service"),
        (400, "request"),
    ] {
        let error = http_error(status);
        assert_eq!(error.category, category);
        assert_eq!(error.message, format!("Service returned HTTP {status}."));
    }
}

#[test]
fn tag_verification_ignores_unrelated_rows_and_waits_bounded() {
    let result = verify_probe(
        &json!([
            [{"probe_tag": "unrelated"}],
            [{"probe_tag": "unrelated"}],
            [{"probe_tag": "wanted"}]
        ]),
        "wanted",
        60.0,
        2.0,
        12,
    )
    .unwrap();
    assert_eq!(result["status"], "pass");
    assert_eq!(result["attempts"], 3);
    assert_eq!(result["elapsed_seconds"], 4.0);
    assert_eq!(result["matched_records"], 1);
}

#[test]
fn tag_verification_missing_is_blocked_not_fake_pass() {
    let result = verify_probe(&json!([[], []]), "wanted", 3.0, 2.0, 12).unwrap();
    assert_eq!(result["status"], "blocked");
    assert_eq!(result["attempts"], 2);
    assert_eq!(result["elapsed_seconds"], 3.0);
}

use castia::observe::{normalize_record, summarize};
use serde_json::json;

#[test]
fn normalizes_identity_status_usage_and_minimizes_content() {
    let row = json!({
        "timestamp": "2026-09-15T08:00:00-07:00",
        "source": "requests",
        "name": "chat model",
        "operation_Id": "trace",
        "id": "span",
        "operation_ParentId": "parent",
        "success": "true",
        "duration": 42.5,
        "customDimensions": {
            "gen_ai.agent.id": "test-agent:3",
            "gen_ai.usage.input_tokens": "7",
            "gen_ai.usage.output_tokens": 3,
            "gen_ai.tool.name": "lookup",
            "castia.probe_tag": "probe",
            "gen_ai.input.messages": "PRIVATE PROMPT",
            "Authorization": "SECRET"
        }
    });
    let record = normalize_record(&row, false).unwrap();
    assert_eq!(record["timestamp"], "2026-09-15T15:00:00Z");
    assert_eq!(record["agent_name"], "test-agent");
    assert_eq!(record["agent_version"], "3");
    assert_eq!(record["trace_id"], "trace");
    assert_eq!(record["span_id"], "span");
    assert_eq!(record["parent_id"], "parent");
    assert_eq!(record["status"], "success");
    assert_eq!(record["latency_ms"], 42.5);
    assert_eq!(record["input_tokens"], 7);
    assert_eq!(record["output_tokens"], 3);
    assert_eq!(record["total_tokens"], 10);
    assert_eq!(record["tool_name"], "lookup");
    assert_eq!(record["probe_tag"], "probe");
    assert!(record.get("content").is_none());

    let content = normalize_record(&row, true).unwrap();
    assert_eq!(
        content["content"],
        json!({"gen_ai.input.messages": "PRIVATE PROMPT"})
    );
}

#[test]
fn supports_json_dimensions_measurements_and_workspace_shape() {
    let record = normalize_record(
        &json!({
            "timestamp": "2026-09-15T15:00:00Z",
            "itemType": "customEvent",
            "customDimensions": "{\"gen_ai.agent.name\":\"agent\", \"otel.status_code\":\"ERROR\"}",
            "customMeasurements": {"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 12}
        }),
        false,
    )
    .unwrap();
    assert_eq!(record["source"], "customEvent");
    assert_eq!(record["agent_name"], "agent");
    assert_eq!(record["status"], "error");
    assert_eq!(record["input_tokens"], 0);
    assert_eq!(record["total_tokens"], 12);

    let workspace = normalize_record(
        &json!({
            "TimeGenerated": "2026-09-15T15:00:00Z",
            "DurationMs": 3,
            "OperationId": "trace",
            "ParentId": "parent",
            "Success": false,
            "Properties": {"attributes": {"gen_ai.agent.name": "workspace-agent"}}
        }),
        false,
    )
    .unwrap();
    assert_eq!(workspace["latency_ms"], 3.0);
    assert_eq!(workspace["status"], "error");
    assert_eq!(workspace["agent_name"], "workspace-agent");
    assert_eq!(workspace["trace_id"], "trace");
}

#[test]
fn empty_dimensions_fall_back_to_workspace_properties() {
    let record = normalize_record(
        &json!({
            "customDimensions": {},
            "customMeasurements": "",
            "Properties": {"attributes": {"gen_ai.agent.name": "workspace-agent"}},
            "Measurements": {"gen_ai.usage.input_tokens": " 7 "}
        }),
        false,
    )
    .unwrap();
    assert_eq!(record["agent_name"], "workspace-agent");
    assert_eq!(record["input_tokens"], 7);
}

#[test]
fn falsy_error_type_does_not_invent_error_status() {
    for value in [json!(false), json!(0), json!("")] {
        let record =
            normalize_record(&json!({"customDimensions": {"error.type": value}}), false).unwrap();
        assert_eq!(record["status"], "unknown");
    }
}

#[test]
fn preserves_correlated_request_identity_over_downstream_class_identity() {
    let record = normalize_record(
        &json!({
            "agent_name": "hal-autopilot",
            "agent_version": null,
            "probe_tag": "probe",
            "customDimensions": {
                "gen_ai.agent.name": "ChatAgent",
                "gen_ai.agent.version": "sdk-version",
                "gen_ai.agent.id": "ChatAgent:sdk-version",
                "gen_ai.usage.input_tokens": 13
            }
        }),
        false,
    )
    .unwrap();
    assert_eq!(record["agent_name"], "hal-autopilot");
    assert_eq!(record["agent_version"], serde_json::Value::Null);
    assert_eq!(record["probe_tag"], "probe");
    assert_eq!(record["input_tokens"], 13);
}

#[test]
fn bad_tokens_and_timestamps_are_unknown() {
    for value in [
        json!(null),
        json!(""),
        json!(-1),
        json!(true),
        json!("garbage"),
        json!(1.5),
    ] {
        let record = normalize_record(&json!({"input_tokens": value}), false).unwrap();
        assert_eq!(record["input_tokens"], serde_json::Value::Null);
        assert_eq!(record["total_tokens"], serde_json::Value::Null);
    }
    for value in [
        json!(null),
        json!(""),
        json!("garbage"),
        json!("2026-09-15T12:00:00"),
        json!("x—abcd"),
    ] {
        let record = normalize_record(&json!({"timestamp": value}), false).unwrap();
        assert_eq!(record["timestamp"], serde_json::Value::Null);
    }
}

#[test]
fn timestamps_preserve_microseconds_and_accept_workspace_separator() {
    let fractional = normalize_record(
        &json!({"timestamp": "2026-09-15T08:00:00.123456-07:00"}),
        false,
    )
    .unwrap();
    assert_eq!(fractional["timestamp"], "2026-09-15T15:00:00.123456Z");

    let workspace =
        normalize_record(&json!({"TimeGenerated": "2026-09-15 08:00:00+0000"}), false).unwrap();
    assert_eq!(workspace["timestamp"], "2026-09-15T08:00:00Z");
}

#[test]
fn summarize_distinguishes_unknowns_and_zeroes() {
    let rows = vec![
        json!({"status": "success", "latency_ms": 0, "input_tokens": 0, "tool_name": "tool"}),
        json!({"status": "error", "latency_ms": 100, "input_tokens": 10, "tool_name": "tool"}),
        json!({"status": "unknown"}),
    ];
    let summary = summarize(&rows).unwrap();
    assert_eq!(summary["error_rate"], 0.5);
    assert_eq!(summary["unknown_status_count"], 1);
    assert_eq!(summary["latency_ms"]["p50"], 50.0);
    assert_eq!(summary["latency_ms"]["p95"], 95.0);
    assert_eq!(summary["tool_errors"], json!({"tool": 1}));
    assert_eq!(
        summary["usage"]["input_tokens"],
        json!({"observed_sum": 10, "known_records": 2, "unknown_records": 1})
    );
    assert_eq!(
        summary["usage"]["output_tokens"],
        json!({"observed_sum": null, "known_records": 0, "unknown_records": 3})
    );
}

#[test]
fn summary_does_not_turn_unknown_tool_status_into_zero_errors() {
    let summary = summarize(&[json!({"tool_name": "tool"})]).unwrap();
    assert_eq!(summary["tool_errors"]["tool"], serde_json::Value::Null);
    assert_eq!(summary["error_count"], serde_json::Value::Null);
    assert_eq!(
        summary["tool_status_counts"]["tool"],
        json!({"known": 0, "unknown": 1})
    );
}

#[test]
fn rejects_malformed_normalized_measurements() {
    for values in [
        json!({"status": "garbage"}),
        json!({"input_tokens": -1}),
        json!({"output_tokens": true}),
        json!({"total_tokens": 1.5}),
        json!({"latency_ms": -1}),
        json!({"cost": -1}),
    ] {
        assert!(summarize(&[values]).is_err());
    }
}

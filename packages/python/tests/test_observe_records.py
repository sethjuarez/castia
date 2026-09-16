from datetime import UTC, datetime

import pytest

from castia.observe import ExecutionRecord, normalize_record, summarize


@pytest.mark.parametrize("source", ["requests", "dependencies", "customEvents"])
def test_normalizes_identity_status_usage_and_content_minimization(source):
    row = {
        "timestamp": "2026-09-15T08:00:00-07:00",
        "source": source, "name": "chat model", "operation_Id": "trace",
        "id": "span", "operation_ParentId": "parent", "success": "true",
        "duration": 42.5,
        "customDimensions": {
            "gen_ai.agent.id": "test-agent:3",
            "gen_ai.usage.input_tokens": "7",
            "gen_ai.usage.output_tokens": 3,
            "gen_ai.tool.name": "lookup", "castia.probe_tag": "probe",
            "gen_ai.input.messages": "PRIVATE PROMPT",
            "Authorization": "SECRET",
        },
    }
    record = normalize_record(row)
    assert record.timestamp == "2026-09-15T15:00:00Z"
    assert (record.agent_name, record.agent_version) == ("test-agent", "3")
    assert (record.trace_id, record.span_id, record.parent_id) == ("trace", "span", "parent")
    assert record.status == "success"
    assert record.latency_ms == 42.5
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (7, 3, 10)
    assert record.tool_name == "lookup" and record.probe_tag == "probe"
    assert "content" not in record.to_dict()
    assert "PRIVATE PROMPT" not in str(record.to_dict())
    assert "SECRET" not in str(normalize_record(row, include_content=True).to_dict())
    assert normalize_record(row, include_content=True).content == {"gen_ai.input.messages": "PRIVATE PROMPT"}


def test_custom_event_json_dimensions_and_measurements():
    record = normalize_record({
        "timestamp": datetime(2026, 9, 15, tzinfo=UTC),
        "itemType": "customEvent",
        "customDimensions": '{"gen_ai.agent.name":"agent", "otel.status_code":"ERROR"}',
        "customMeasurements": {"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 12},
    })
    assert record.source == "customEvent"
    assert record.agent_name == "agent"
    assert record.status == "error"
    assert record.input_tokens == 0 and record.total_tokens == 12


def test_workspace_shape_and_nested_attributes():
    record = normalize_record({
        "TimeGenerated": "2026-09-15T15:00:00Z", "DurationMs": 3,
        "OperationId": "trace", "ParentId": "parent", "Success": False,
        "Properties": {"attributes": {"gen_ai.agent.name": "agent"}},
    })
    assert record.latency_ms == 3 and record.status == "error"
    assert record.agent_name == "agent"
    assert record.trace_id == "trace"


@pytest.mark.parametrize("value", [None, "", -1, True, "garbage", float("nan"), float("inf"), 1.5])
def test_bad_or_missing_tokens_are_unknown(value):
    record = normalize_record({"input_tokens": value})
    assert record.input_tokens is None
    assert record.total_tokens is None
    assert record.output_tokens is None


@pytest.mark.parametrize("value", [None, "", "garbage", "2026-09-15T12:00:00"])
def test_bad_or_naive_timestamp_is_unknown(value):
    assert normalize_record({"timestamp": value}).timestamp is None


@pytest.mark.parametrize(
    ("dimensions", "expected"),
    [
        ({}, "unknown"), ({"success": False}, "error"), ({"success": True}, "success"),
        ({"otel.status_code": 2}, "error"), ({"otel.status_code": 1}, "success"),
        ({"otel.status_code": 0}, "unknown"), ({"error.type": "ValueError"}, "error"),
        ({"success": True, "error.type": "ValueError"}, "error"),
    ],
)
def test_status_is_not_assumed_success(dimensions, expected):
    assert normalize_record({"customDimensions": dimensions}).status == expected


def test_empty_summary_has_unknown_rates_latency_usage_cost():
    summary = summarize([])
    assert summary["error_rate"] is None
    assert summary["latency_ms"]["p95"] is None
    assert summary["usage"]["total_tokens"]["observed_sum"] is None
    assert summary["cost"] is None
    assert summary["error_count"] is None
    assert summary["observed_error_count"] == 0


def test_summary_distinguishes_unknowns_and_zero():
    rows = [
        ExecutionRecord(status="success", latency_ms=0, input_tokens=0, tool_name="tool"),
        ExecutionRecord(status="error", latency_ms=100, input_tokens=10, tool_name="tool"),
        ExecutionRecord(status="unknown"),
    ]
    summary = summarize(iter(rows))
    assert summary["error_rate"] == .5
    assert summary["unknown_status_count"] == 1
    assert summary["latency_ms"]["p50"] == 50
    assert summary["latency_ms"]["p95"] == 95
    assert summary["latency_ms"]["unknown_count"] == 1
    assert summary["tool_errors"] == {"tool": 1}
    assert summary["usage"]["input_tokens"] == {"observed_sum": 10, "known_records": 2, "unknown_records": 1}
    assert summary["usage"]["output_tokens"]["observed_sum"] is None


def test_content_recording_does_not_accept_string_flags():
    with pytest.raises(TypeError):
        normalize_record({"customDimensions": {"gen_ai.prompt": "secret"}}, include_content="false")


@pytest.mark.parametrize(
    "values",
    [
        {"status": "garbage"}, {"input_tokens": -1}, {"output_tokens": True},
        {"total_tokens": 1.5}, {"latency_ms": -1}, {"cost": float("nan")},
    ],
)
def test_execution_record_rejects_malformed_normalized_measurements(values):
    with pytest.raises(ValueError):
        ExecutionRecord(**values)


def test_hosted_request_agentserver_identity_is_recognized():
    record = normalize_record({"customDimensions": {
        "azure.ai.agentserver.agent_name": "hal-autopilot",
        "azure.ai.agentserver.agent_version": "3",
    }})
    assert record.agent_name == "hal-autopilot" and record.agent_version == "3"


def test_correlated_request_identity_overrides_downstream_class_names():
    record = normalize_record({
        "agent_name": "hal-autopilot", "agent_version": "3", "probe_tag": "probe",
        "customDimensions": {
            "gen_ai.agent.name": "ChatAgent", "gen_ai.agent.version": "sdk-version",
            "gen_ai.usage.input_tokens": 13,
        },
    })
    assert record.agent_name == "hal-autopilot" and record.agent_version == "3"
    assert record.probe_tag == "probe" and record.input_tokens == 13


def test_missing_correlated_request_version_does_not_become_downstream_sdk_version():
    record = normalize_record({
        "agent_name": "hal-autopilot", "agent_version": None,
        "customDimensions": {
            "gen_ai.agent.name": "ChatAgent", "gen_ai.agent.version": "sdk-version",
            "gen_ai.agent.id": "ChatAgent:sdk-version",
        },
    })
    assert record.agent_name == "hal-autopilot" and record.agent_version is None


def test_unknown_tool_status_is_not_zero_tool_errors():
    summary = summarize([ExecutionRecord(tool_name="tool")])
    assert summary["tool_errors"]["tool"] is None
    assert summary["error_count"] is None
    assert summary["tool_status_counts"]["tool"] == {"known": 0, "unknown": 1}


@pytest.mark.parametrize("key", ["castia.probe_tag", "castia.probe_id", "probe_id"])
def test_content_free_probe_id_aliases_normalize_as_tags(key):
    record = normalize_record({"customDimensions": {key: "tagged-delivery"}})
    assert record.probe_tag == "tagged-delivery"

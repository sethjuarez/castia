"""GenAI semantic-convention telemetry: server tool spans, tool-error gate,
trace correlation, human evaluations and content/parameter attributes.

Pure shapes are driven by the language-neutral fixture
``spec/conformance/observe/genai_semconv.json``, which the Rust SDK also reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from castia.evaluation.tool_errors import tool_error_gate
from castia.observe import genai, tool_capture
from castia.observe.correlation import (
    inject_traceparent,
    record_human_evaluation,
    trace_http_client,
)
from castia.observe.server_tools import record_server_tools
from castia.observe.tracing import (
    record_request_parameters,
    record_system_instructions,
    record_turn_input,
    record_turn_output,
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "spec" / "conformance" / "observe" / "genai_semconv.json"
    ).read_text(encoding="utf-8")
)
CONTENT_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        trace, "get_tracer", lambda name, *a, **kw: provider.get_tracer(name, *a, **kw)
    )
    yield SimpleNamespace(exporter=exporter, tracer=provider.get_tracer("test"))
    provider.shutdown()


# --------------------------------------------------------------------------- fixture


def test_fixture_pins_semconv_version():
    assert genai.GENAI_SEMCONV_VERSION == FIXTURE["semconv_version"]
    assert genai.GENAI_SEMCONV_SCHEMA_URL == FIXTURE["schema_url"]
    assert genai.PROVIDER == FIXTURE["provider"]


@pytest.mark.parametrize("case", FIXTURE["text_messages"], ids=lambda c: c["role"])
def test_text_messages_match_fixture(case):
    assert genai.text_messages(
        case["role"], case["text"], finish_reason=case["finish_reason"]
    ) == case["expected"]


def test_system_instructions_match_fixture():
    for case in FIXTURE["system_instructions"]:
        assert genai.system_instructions(case["text"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["request_parameters"], ids=lambda c: c["name"])
def test_request_parameters_match_fixture(case):
    assert genai.request_parameter_attributes(case["params"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["server_tools"], ids=lambda c: c["name"])
def test_server_tool_shapes_match_fixture(case):
    calls = genai.server_tool_calls(case["output"])
    assert calls == case["expected_calls"]
    assert [
        genai.server_tool_span(
            call, iteration=case["iteration"], include_content=case["include_content"]
        )
        for call in calls
    ] == case["expected_spans"]


def test_server_tool_calls_accept_sdk_objects():
    item = SimpleNamespace(
        type="mcp_call", id="mcp_9", name="search", server_label="kb",
        arguments="{}", output="ok", error=None, status="completed",
    )
    assert genai.server_tool_calls([item])[0]["call_id"] == "mcp_9"


@pytest.mark.parametrize("case", FIXTURE["tool_error_gate"], ids=lambda c: c["name"])
def test_tool_error_gate_matches_fixture(case):
    result = tool_error_gate(
        case["records"], max_error_rate=case["max_error_rate"], min_calls=case["min_calls"]
    )
    assert result.to_dict() == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["human_evaluation"], ids=lambda c: c["name"])
def test_human_evaluation_attributes_match_fixture(case):
    payload = dict(case["input"])
    name = payload.pop("name")
    if "error" in case:
        with pytest.raises(ValueError):
            genai.human_evaluation_attributes(name, **payload)
    else:
        assert genai.human_evaluation_attributes(name, **payload) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["traceparent"], ids=lambda c: c["value"][:8])
def test_parse_traceparent_matches_fixture(case):
    expected = case["expected"]
    assert genai.parse_traceparent(case["value"]) == (
        tuple(expected) if expected else None
    )


# --------------------------------------------------------------------------- item 14


def test_record_server_tools_emits_child_spans_with_error_status(spans, monkeypatch):
    monkeypatch.delenv(CONTENT_ENV, raising=False)
    output = FIXTURE["server_tools"][1]["output"]
    with spans.tracer.start_as_current_span("castia.model.respond_with_tools") as parent:
        calls = record_server_tools(output, iteration=2, started_ns=1)
    [tool, _] = spans.exporter.get_finished_spans()
    assert calls[0]["error_type"] == "http_402"
    assert tool.name == "execute_tool knowledge_base_retrieve"
    assert tool.parent.span_id == parent.get_span_context().span_id
    assert tool.status.status_code is StatusCode.ERROR
    assert tool.attributes["error.type"] == "http_402"
    assert tool.attributes["castia.tool.timing"] == "request_window"
    assert tool.start_time == 1
    assert "gen_ai.tool.call.arguments" not in tool.attributes


def test_record_server_tools_links_tool_capture(spans, monkeypatch):
    import asyncio

    monkeypatch.setenv(CONTENT_ENV, "true")
    documents = []

    async def turn():
        async with tool_capture.tool_capture_scope(sink=documents.append):
            record_server_tools(FIXTURE["server_tools"][1]["output"], iteration=1)

    asyncio.run(turn())
    [span] = spans.exporter.get_finished_spans()
    [call] = documents[0]["tool_calls"]
    assert call["status"] == "error"
    assert call["error"]["type"] == "http_402"
    assert call["span_id"] == f"{span.context.span_id:016x}"
    assert span.attributes["gen_ai.tool.call.arguments"] == "{}"


def test_record_server_tools_ignores_responses_without_server_tools(spans):
    assert record_server_tools([{"type": "message"}]) == []
    assert record_server_tools(None) == []
    assert spans.exporter.get_finished_spans() == ()


# --------------------------------------------------------------------------- item 18


def test_inject_traceparent_keeps_existing_and_creates_when_missing():
    existing = {"TraceParent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
    assert inject_traceparent(existing) == existing
    created = inject_traceparent()
    assert genai.parse_traceparent(created["traceparent"]) is not None
    assert "traceparent" not in inject_traceparent({}, create_if_missing=False)


def test_inject_traceparent_uses_current_span(spans):
    with spans.tracer.start_as_current_span("client") as span:
        headers = inject_traceparent()
    trace_id, span_id, _ = genai.parse_traceparent(headers["traceparent"])
    assert int(trace_id, 16) == span.get_span_context().trace_id
    assert int(span_id, 16) == span.get_span_context().span_id


def test_trace_http_client_injects_header_without_overwriting():
    seen = []

    def handler(request):
        seen.append(request.headers.get("traceparent"))
        return httpx.Response(200)

    with trace_http_client(httpx.Client(transport=httpx.MockTransport(handler))) as client:
        client.get("https://example.test/")
        client.get("https://example.test/", headers={"traceparent": "keep-me"})
    assert genai.parse_traceparent(seen[0]) is not None
    assert seen[1] == "keep-me"


def test_trace_http_client_supports_async_clients():
    import asyncio

    seen = []

    def handler(request):
        seen.append(request.headers.get("traceparent"))
        return httpx.Response(200)

    async def call():
        client = trace_http_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        async with client:
            await client.get("https://example.test/")

    asyncio.run(call())
    assert genai.parse_traceparent(seen[0]) is not None


def test_record_human_evaluation_emits_parented_event(monkeypatch):
    from opentelemetry import _logs
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import (
        InMemoryLogRecordExporter,
        SimpleLogRecordProcessor,
    )

    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(
        _logs, "get_logger", lambda name, *a, **kw: provider.get_logger(name, *a, **kw)
    )
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    attributes = record_human_evaluation(
        "helpfulness", score=0, label="fail", traceparent=traceparent, reviewer="qa"
    )
    [data] = exporter.get_finished_logs()
    record = data.log_record
    assert record.event_name == "gen_ai.evaluation.result"
    assert f"{record.trace_id:032x}" == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert f"{record.span_id:016x}" == "00f067aa0ba902b7"
    assert dict(record.attributes) == attributes
    assert attributes["gen_ai.evaluation.score.value"] == 0.0
    provider.shutdown()


def test_record_human_evaluation_rejects_bad_trace_reference():
    with pytest.raises(ValueError):
        record_human_evaluation("x", score=1, traceparent="nope")
    with pytest.raises(ValueError):
        record_human_evaluation("x", score=1, trace_id="abc")


# --------------------------------------------------------------------------- item 19


def test_content_attributes_use_parts_schema_behind_opt_in(spans, monkeypatch):
    monkeypatch.delenv(CONTENT_ENV, raising=False)
    with spans.tracer.start_as_current_span("off") as span:
        record_turn_input(span, "hi")
        record_system_instructions("secret policy")
    monkeypatch.setenv(CONTENT_ENV, "true")
    with spans.tracer.start_as_current_span("on") as span:
        record_turn_input(span, "hi")
        record_turn_output(span, "hello")
        record_system_instructions("Be terse.")
    off, on = spans.exporter.get_finished_spans()
    assert not any(key.startswith("gen_ai.") for key in off.attributes)
    assert json.loads(on.attributes["gen_ai.input.messages"]) == genai.text_messages("user", "hi")
    assert json.loads(on.attributes["gen_ai.output.messages"]) == genai.text_messages(
        "assistant", "hello", finish_reason="stop"
    )
    assert json.loads(on.attributes["gen_ai.system_instructions"]) == [
        {"type": "text", "content": "Be terse."}
    ]


def test_record_request_parameters_keeps_zero(spans):
    with spans.tracer.start_as_current_span("chat"):
        record_request_parameters(temperature=0, top_p=None, seed=0)
    [span] = spans.exporter.get_finished_spans()
    assert span.attributes["gen_ai.request.temperature"] == 0
    assert span.attributes["gen_ai.request.seed"] == 0
    assert "gen_ai.request.top_p" not in span.attributes


def test_tracer_reports_pinned_schema_url(spans):
    from castia.observe.tracing import execute_tool

    with execute_tool("lookup"):
        pass
    [span] = spans.exporter.get_finished_spans()
    assert span.instrumentation_scope.schema_url == genai.GENAI_SEMCONV_SCHEMA_URL

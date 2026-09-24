from __future__ import annotations

import json
from typing import ClassVar

import castia.prompty as castia_prompty
from castia.observe.tracing import clear_trace_sinks, register_trace_sink


class FakeTracer:
    calls: ClassVar[list] = []

    @classmethod
    def add(cls, name, tracer):
        cls.calls.append((name, tracer))


class FakePrompty:
    Tracer = FakeTracer


def setup_function():
    FakeTracer.calls.clear()
    clear_trace_sinks()


def teardown_function():
    clear_trace_sinks()


def test_prompty_trace_sink_registration_filters_content_without_opt_in(monkeypatch):
    records = []
    register_trace_sink("memory", records.append)
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    assert castia_prompty.register_prompty_trace_sinks() is True
    assert FakeTracer.calls and FakeTracer.calls[0][0] == "castia"

    with FakeTracer.calls[0][1]("turn_async") as add:
        add("signature", "prompty.core.turn_async")
        add("inputs", {"text": "private prompt"})
        add("result", "private answer")

    assert len(records) == 1
    record = records[0]
    assert record.name == "prompty turn_async"
    assert record.kind == "prompty"
    assert record.attributes["castia.prompty.span.name"] == "turn_async"
    assert record.attributes["castia.prompty.signature"] == "prompty.core.turn_async"
    assert "castia.prompty.inputs" not in record.attributes
    assert "castia.prompty.result" not in record.attributes
    assert "gen_ai.input.messages" not in record.attributes
    assert "gen_ai.output.messages" not in record.attributes


def test_prompty_trace_sink_records_content_when_enabled(monkeypatch):
    records = []
    register_trace_sink("memory", records.append)
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)

    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=True,
        include_internal=False,
    )

    with backend("turn_async") as add:
        add("inputs", {"text": "recorded prompt"})
        add("result", "recorded answer")

    attrs = records[0].attributes
    assert attrs["castia.prompty.inputs"] == {"text": "recorded prompt"}
    assert attrs["castia.prompty.result"] == "recorded answer"
    assert attrs["gen_ai.input.messages"] == [
        {"role": "user", "content": "recorded prompt"}
    ]
    assert attrs["gen_ai.output.messages"] == [
        {"role": "assistant", "content": "recorded answer"}
    ]


def test_prompty_trace_sink_explicit_false_overrides_content_env(monkeypatch):
    records = []
    register_trace_sink("memory", records.append)
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    assert castia_prompty.register_prompty_trace_sinks(
        enable_content_recording=False
    ) is True
    with FakeTracer.calls[0][1]("turn_async") as add:
        add("inputs", {"text": "private prompt"})
        add("result", "private answer")

    attrs = records[0].attributes
    assert "castia.prompty.inputs" not in attrs
    assert "castia.prompty.result" not in attrs
    assert "gen_ai.input.messages" not in attrs
    assert "gen_ai.output.messages" not in attrs


def test_prompty_trace_sink_respects_internal_span_gate(monkeypatch):
    records = []
    register_trace_sink("memory", records.append)
    monkeypatch.delenv("CASTIA_PROMPTY_TRACE_INTERNAL", raising=False)
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    assert castia_prompty.register_prompty_trace_sinks(name="local") is True
    with FakeTracer.calls[0][1]("prepare_async") as add:
        add("signature", "prompty.core.prepare_async")
    assert records == []

    FakeTracer.calls.clear()
    monkeypatch.setenv("CASTIA_PROMPTY_TRACE_INTERNAL", "true")
    assert castia_prompty.register_prompty_trace_sinks(name="local") is True
    with FakeTracer.calls[0][1]("prepare_async") as add:
        add("signature", "prompty.core.prepare_async")
    assert records[0].name == "prompty prepare_async"


def test_prompty_trace_sink_sanitizes_non_content_attributes():
    records = []
    register_trace_sink("memory", records.append)
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=False,
        include_internal=True,
    )

    with backend("prepare_async") as add:
        add(
            "configuration",
            {
                "api_key": "secret-token",
                "nested": {"clientSecret": "nested-secret"},
                "safe": "visible",
            },
        )

    configuration = records[0].attributes["castia.prompty.configuration"]
    assert configuration["api_key"] != "secret-token"
    assert configuration["nested"]["clientSecret"] != "nested-secret"
    assert configuration["safe"] == "visible"


def test_prompty_trace_sink_fallback_sanitizer_matches_secret_key_patterns():
    value = castia_prompty._fallback_sanitize_prompty_trace_attribute(
        "configuration",
        {
            "password": "password-value",
            "access_token": "token-value",
            "authorization": "auth-value",
            "cookie": "cookie-value",
            "tokens": "not-a-secret-key-name",
            "authors": "not-a-secret-key-name",
            "safe": "visible",
        },
    )

    assert value["password"] != "password-value"
    assert value["access_token"] != "token-value"
    assert value["authorization"] != "auth-value"
    assert value["cookie"] != "cookie-value"
    assert value["tokens"] == "not-a-secret-key-name"
    assert value["authors"] == "not-a-secret-key-name"
    assert value["safe"] == "visible"


def test_prompty_trace_sink_records_exception_result():
    records = []
    register_trace_sink("memory", records.append)
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=False,
        include_internal=False,
    )

    with backend("turn_async") as add:
        add(
            "result",
            {
                "exception": {
                    "type": "ValueError",
                    "message": "bad prompt",
                    "traceback": ["stack"],
                }
            },
        )

    attrs = records[0].attributes
    assert attrs["castia.prompty.status"] == "error"
    assert attrs["castia.prompty.exception.type"] == "ValueError"
    assert "castia.prompty.exception.message" not in attrs
    assert "castia.prompty.result" not in attrs


def test_prompty_trace_sink_records_exception_message_with_content_opt_in():
    records = []
    register_trace_sink("memory", records.append)
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=True,
        include_internal=False,
    )

    with backend("turn_async") as add:
        add("result", {"exception": {"type": "ValueError", "message": "bad prompt"}})

    assert records[0].attributes["castia.prompty.exception.message"] == "bad prompt"


def test_prompty_trace_sink_suppresses_raised_exception_message_without_content():
    records = []
    register_trace_sink("memory", records.append)
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=False,
        include_internal=False,
    )

    try:
        with backend("turn_async") as add:
            add("signature", "prompty.core.turn_async")
            raise RuntimeError("prompt payload leaked in exception")
    except RuntimeError:
        pass

    assert records[0].status == "error"
    assert records[0].error_type == "RuntimeError"
    assert records[0].error_message is None
    assert records[0].attributes["castia.prompty.signature"] == "prompty.core.turn_async"


def test_prompty_trace_sink_records_raised_exception_message_with_content():
    records = []
    register_trace_sink("memory", records.append)
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=True,
        include_internal=False,
    )

    try:
        with backend("turn_async"):
            raise RuntimeError("visible failure")
    except RuntimeError:
        pass

    assert records[0].error_message == "visible failure"


def test_prompty_trace_sink_can_be_used_with_jsonl_sink(tmp_path):
    from castia.observe.tracing import jsonl_trace_sink

    path = tmp_path / "prompty.jsonl"
    register_trace_sink("jsonl", jsonl_trace_sink(path))
    backend = castia_prompty._prompty_trace_sink_backend(
        include_content=False,
        include_internal=False,
    )

    with backend("run_async") as add:
        add("signature", "prompty.core.run_async")

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["name"] == "prompty run_async"
    assert rows[0]["attributes"]["castia.prompty.signature"] == "prompty.core.run_async"

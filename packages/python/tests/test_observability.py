"""Unit tests for GenAI content-recording gating in observability.

Pure precedence logic -- no cloud, creds, or network. The Foundry GenAI
instrumentor is mocked so we can assert exactly what ``enable_content_recording``
value ``_enable_genai_tracing`` forwards to
``AIProjectInstrumentor().instrument(...)`` under each arg/env/default case.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import pytest

from castia.observe import configuration as observability

_CONTENT_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_GENAI_ENV = "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"
_TRACE_ASGI_INTERNAL_ENV = "CASTIA_OTEL_TRACE_ASGI_INTERNAL"
_TRACE_ASGI_SEND_ENV = "CASTIA_OTEL_TRACE_ASGI_SEND"


@pytest.mark.parametrize("helper", ["invoke_agent", "execute_tool"])
def test_operation_spans_use_trace_api(helper):
    from castia.observe import tracing

    api = mock.MagicMock()
    expected = api.get_tracer.return_value.start_as_current_span.return_value.__enter__.return_value
    with mock.patch("opentelemetry.trace.get_tracer", api.get_tracer), getattr(tracing, helper)("example") as span:
        assert span is expected

    api.get_tracer.assert_called_once_with("castia")
    assert api.get_tracer.return_value.start_as_current_span.call_args.args == (
        f"{helper} example",
    )


@contextmanager
def _patched_instrumentor():
    """Patch the instrumentor + guard so only resolution logic runs."""
    instrumentor = mock.MagicMock()
    fake_module = mock.MagicMock(AIProjectInstrumentor=instrumentor)
    with mock.patch.dict(
        "sys.modules", {"azure.ai.projects.telemetry": fake_module}
    ), mock.patch.object(observability, "_guard_instrumentor_recording"):
        yield instrumentor


def _recorded_content_flag(instrumentor: mock.MagicMock) -> bool:
    instrumentor.return_value.instrument.assert_called_once()
    return instrumentor.return_value.instrument.call_args.kwargs[
        "enable_content_recording"
    ]


# -- _resolve_flag: arg > env > default -------------------------------------


def test_resolve_flag_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    assert observability._resolve_flag(False, _CONTENT_ENV, True) is False
    assert observability._resolve_flag(True, _CONTENT_ENV, False) is True


def test_resolve_flag_env_used_when_arg_none(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "TRUE")  # case-insensitive
    assert observability._resolve_flag(None, _CONTENT_ENV, False) is True
    monkeypatch.setenv(_CONTENT_ENV, "false")
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is False
    monkeypatch.setenv(_CONTENT_ENV, "nonsense")  # any non-"true" is False
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is False


def test_resolve_flag_default_when_arg_and_env_absent(monkeypatch):
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    assert observability._resolve_flag(None, _CONTENT_ENV, False) is False
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is True


# -- content recording forwarded to the instrumentor ------------------------


def test_content_recording_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing(enable_content_recording=False)
    assert _recorded_content_flag(instrumentor) is False


def test_content_recording_from_env_when_arg_none(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    assert _recorded_content_flag(instrumentor) is True


def test_content_recording_defaults_off(monkeypatch):
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    assert _recorded_content_flag(instrumentor) is False


# -- genai tracing gate -----------------------------------------------------


def test_genai_tracing_disabled_skips_instrumentation(monkeypatch):
    monkeypatch.delenv(_GENAI_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing(enable_genai_tracing=False)
    instrumentor.return_value.instrument.assert_not_called()
    assert observability.os.environ[_GENAI_ENV] == "false"


def test_genai_tracing_default_instruments_and_sets_env(monkeypatch):
    monkeypatch.delenv(_GENAI_ENV, raising=False)
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    instrumentor.return_value.instrument.assert_called_once()
    assert observability.os.environ[_GENAI_ENV] == "true"


def test_configure_observability_suppresses_asgi_internal_spans_by_default(monkeypatch):
    monkeypatch.delenv(_TRACE_ASGI_INTERNAL_ENV, raising=False)
    monkeypatch.delenv(_TRACE_ASGI_SEND_ENV, raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(
            observability, "_build_agent_identity_processors", return_value=[]
        ),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    options = use_otel.call_args.kwargs["instrumentation_options"]
    assert options["fastapi"] == {"exclude_spans": ["send", "receive"]}
    assert options["openai_agents"] == {"enabled": False}


def test_configure_observability_can_trace_asgi_internal_spans(monkeypatch):
    monkeypatch.setenv(_TRACE_ASGI_INTERNAL_ENV, "true")
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(
            observability, "_build_agent_identity_processors", return_value=[]
        ),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    options = use_otel.call_args.kwargs["instrumentation_options"]
    assert options["fastapi"] == {}


def test_configure_observability_accepts_asgi_send_compat_flag(monkeypatch):
    monkeypatch.delenv(_TRACE_ASGI_INTERNAL_ENV, raising=False)
    monkeypatch.setenv(_TRACE_ASGI_SEND_ENV, "true")
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(
            observability, "_build_agent_identity_processors", return_value=[]
        ),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    options = use_otel.call_args.kwargs["instrumentation_options"]
    assert options["fastapi"] == {}


def test_agent_identity_processor_uses_azure_project_id_fallback(monkeypatch):
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", "prompty-agent")
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", "2")
    monkeypatch.delenv("FOUNDRY_PROJECT_RESOURCE_ID", raising=False)
    monkeypatch.delenv("AZURE_AI_PROJECT_RESOURCE_ID", raising=False)
    monkeypatch.setenv("AZURE_AI_PROJECT_ID", "/subscriptions/123/projects/demo")

    processors = observability._build_agent_identity_processors()

    assert len(processors) == 1
    span = mock.MagicMock()
    processors[0].on_start(span)
    span.set_attributes.assert_called_once_with(
        {
            "gen_ai.agent.name": "prompty-agent",
            "gen_ai.agent.version": "2",
            "gen_ai.agent.id": "prompty-agent:2",
            "microsoft.foundry.project.id": "/subscriptions/123/projects/demo",
            "gen_ai.azure_ai_project.id": "/subscriptions/123/projects/demo",
        }
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

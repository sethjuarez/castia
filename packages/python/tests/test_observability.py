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
_TRACE_MSI_TOKEN_ENV = "CASTIA_OTEL_TRACE_MSI_TOKEN"
_APP_INSIGHTS_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
_AZURE_MONITOR_ENV = "AZURE_MONITOR_CONNECTION_STRING"


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


@pytest.mark.parametrize(
    ("arg", "env", "expected"),
    [(None, None, "true"), (False, None, "false"), (None, "false", "false"), (True, "false", "true")],
)
def test_genai_flag_published_before_distro_setup(monkeypatch, arg, env, expected):
    """The distro instruments azure-ai-projects inside its own setup; the env
    flag must already reflect Castia's decision then, or the SDK warns."""
    monkeypatch.delenv(_APP_INSIGHTS_ENV, raising=False)
    monkeypatch.delenv(_AZURE_MONITOR_ENV, raising=False)
    if env is None:
        monkeypatch.delenv(_GENAI_ENV, raising=False)
    else:
        monkeypatch.setenv(_GENAI_ENV, env)
    seen = {}

    def fake_distro(**_kwargs):
        seen["flag"] = observability.os.environ.get(_GENAI_ENV)

    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry", side_effect=fake_distro),
        mock.patch.object(observability, "_build_agent_identity_processors", return_value=[]),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability(enable_genai_tracing=arg)
    assert seen["flag"] == expected


def test_distro_failure_never_breaks_startup(monkeypatch, caplog):
    import logging

    monkeypatch.delenv(_APP_INSIGHTS_ENV, raising=False)
    monkeypatch.delenv(_AZURE_MONITOR_ENV, raising=False)
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry", side_effect=RuntimeError("boom")),
        mock.patch.object(observability, "_build_agent_identity_processors", return_value=[]),
        mock.patch.object(observability, "_enable_genai_tracing") as genai,
        caplog.at_level(logging.WARNING, logger="agent"),
    ):
        observability.configure_observability()
    genai.assert_not_called()
    assert "OpenTelemetry setup failed" in caplog.text
    assert not logging.getLogger(observability._AI_PROJECT_INSTRUMENTOR_LOGGER).filters


def test_sdk_genai_disabled_warning_filtered_only_during_distro_setup(caplog):
    import logging

    sdk_logger = logging.getLogger(observability._AI_PROJECT_INSTRUMENTOR_LOGGER)
    noise = "GenAI tracing is not enabled. Set environment variable ..."
    with caplog.at_level(logging.WARNING):
        with observability._quiet_genai_disabled_warning():
            sdk_logger.warning(noise)
            sdk_logger.warning("some other SDK warning")
        sdk_logger.warning(noise)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["some other SDK warning", noise]
    assert not sdk_logger.filters


def test_configure_observability_suppresses_asgi_internal_spans_by_default(monkeypatch):
    monkeypatch.delenv(_TRACE_ASGI_INTERNAL_ENV, raising=False)
    monkeypatch.delenv(_TRACE_ASGI_SEND_ENV, raising=False)
    monkeypatch.delenv(_APP_INSIGHTS_ENV, raising=False)
    monkeypatch.delenv(_AZURE_MONITOR_ENV, raising=False)
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
    assert use_otel.call_args.kwargs["enable_azure_monitor"] is False
    assert use_otel.call_args.kwargs["azure_monitor_connection_string"] is None


def test_configure_observability_forwards_app_insights_connection_string(monkeypatch):
    connection_string = "InstrumentationKey=test-key;IngestionEndpoint=https://example.test/"
    monkeypatch.delenv(_AZURE_MONITOR_ENV, raising=False)
    monkeypatch.setenv(_APP_INSIGHTS_ENV, connection_string)
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(
            observability, "_build_agent_identity_processors", return_value=[]
        ),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    assert use_otel.call_args.kwargs["enable_azure_monitor"] is True
    assert (
        use_otel.call_args.kwargs["azure_monitor_connection_string"]
        == connection_string
    )


def test_configure_observability_prefers_azure_monitor_connection_string(monkeypatch):
    monkeypatch.setenv(
        _APP_INSIGHTS_ENV,
        "InstrumentationKey=app-insights;IngestionEndpoint=https://example.test/",
    )
    connection_string = "InstrumentationKey=azure-monitor;IngestionEndpoint=https://example.test/"
    monkeypatch.setenv(_AZURE_MONITOR_ENV, connection_string)
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(
            observability, "_build_agent_identity_processors", return_value=[]
        ),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    assert use_otel.call_args.kwargs["enable_azure_monitor"] is True
    assert (
        use_otel.call_args.kwargs["azure_monitor_connection_string"]
        == connection_string
    )


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


def test_azure_core_tracing_filters_msi_token_spans_by_default(monkeypatch):
    from opentelemetry.trace import NonRecordingSpan

    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    implementation = observability._azure_core_tracing_implementation()

    span = implementation(name="GET /msi/token")

    assert isinstance(span.span_instance, NonRecordingSpan)
    assert implementation.__name__ == "CastiaOpenTelemetrySpan"


def test_azure_core_tracing_can_keep_msi_token_spans(monkeypatch):
    from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan

    monkeypatch.setenv(_TRACE_MSI_TOKEN_ENV, "true")

    assert observability._azure_core_tracing_implementation() is OpenTelemetrySpan


def _http_span(
    *,
    name: str = "GET /msi/token",
    status_code: int = 200,
    duration_ms: int = 371,
    url: str = "http://100.64.100.2/msi/token",
):
    return mock.MagicMock(
        name=name,
        attributes={"http.status_code": status_code, "url.full": url},
        start_time=0,
        end_time=duration_ms * 1_000_000,
    )


def test_msi_token_filter_suppresses_successful_fast_spans(monkeypatch):
    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    delegate = mock.MagicMock()
    processor = observability._MsiTokenFilteringSpanProcessor(delegate)

    processor.on_end(_http_span(status_code=200, duration_ms=371))

    delegate.on_end.assert_not_called()


def test_msi_token_filter_suppresses_fast_spans_without_http_status(monkeypatch):
    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    delegate = mock.MagicMock()
    processor = observability._MsiTokenFilteringSpanProcessor(delegate)
    span = mock.MagicMock(
        name="GET /msi/token",
        attributes={},
        start_time=0,
        end_time=798 * 1_000_000,
    )

    processor.on_end(span)

    delegate.on_end.assert_not_called()


def test_msi_token_filter_suppresses_successful_imds_noise_spans(monkeypatch):
    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    delegate = mock.MagicMock()
    processor = observability._MsiTokenFilteringSpanProcessor(delegate)

    processor.on_end(
        _http_span(
            name="GET /metadata/instance/compute",
            url="http://169.254.169.254/metadata/instance/compute",
            duration_ms=412,
        )
    )
    processor.on_end(
        _http_span(
            name="GET /AzMonSDKDynamicConfiguration",
            url="https://dc.services.visualstudio.com/AzMonSDKDynamicConfiguration",
            duration_ms=126,
        )
    )

    delegate.on_end.assert_not_called()


def test_msi_token_filter_keeps_failures_and_slow_spans(monkeypatch):
    from opentelemetry.trace import Status, StatusCode

    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    delegate = mock.MagicMock()
    processor = observability._MsiTokenFilteringSpanProcessor(delegate)
    failed = _http_span(status_code=400, duration_ms=350)
    slow = _http_span(status_code=200, duration_ms=2500)
    imds_failure = _http_span(
        name="GET /metadata/instance/compute",
        status_code=500,
        url="http://169.254.169.254/metadata/instance/compute",
        duration_ms=350,
    )
    transport_error = mock.MagicMock(
        name="GET /msi/token",
        attributes={},
        events=(),
        start_time=0,
        end_time=250 * 1_000_000,
        status=Status(StatusCode.ERROR, "connection refused"),
    )
    exception_attribute = mock.MagicMock(
        name="GET /AzMonSDKDynamicConfiguration",
        attributes={"exception.type": "ConnectionError"},
        events=(),
        start_time=0,
        end_time=250 * 1_000_000,
    )
    exception_event = mock.MagicMock(
        name="GET /metadata/instance/compute",
        attributes={},
        events=(mock.MagicMock(name="exception"),),
        start_time=0,
        end_time=250 * 1_000_000,
    )
    exception_event.events[0].name = "exception"

    processor.on_end(failed)
    processor.on_end(slow)
    processor.on_end(imds_failure)
    processor.on_end(transport_error)
    processor.on_end(exception_attribute)
    processor.on_end(exception_event)

    assert delegate.on_end.call_args_list == [
        mock.call(failed),
        mock.call(slow),
        mock.call(imds_failure),
        mock.call(transport_error),
        mock.call(exception_attribute),
        mock.call(exception_event),
    ]


def test_msi_token_span_filter_wraps_existing_processors(monkeypatch):
    monkeypatch.delenv(_TRACE_MSI_TOKEN_ENV, raising=False)
    processor = object()
    active_processor = mock.MagicMock(_span_processors=(processor,))
    provider = mock.MagicMock(_active_span_processor=active_processor)

    with mock.patch("opentelemetry.trace.get_tracer_provider", return_value=provider):
        observability._install_msi_token_span_filter()

    wrapped = active_processor._span_processors
    assert len(wrapped) == 1
    assert isinstance(wrapped[0], observability._MsiTokenFilteringSpanProcessor)


def test_msi_token_span_filter_preserves_operator_opt_in(monkeypatch):
    monkeypatch.setenv(_TRACE_MSI_TOKEN_ENV, "true")
    processor = mock.MagicMock()
    active_processor = mock.MagicMock(_span_processors=(processor,))
    provider = mock.MagicMock(_active_span_processor=active_processor)

    with mock.patch("opentelemetry.trace.get_tracer_provider", return_value=provider):
        observability._install_msi_token_span_filter()

    assert active_processor._span_processors == (processor,)


def test_agent_identity_processor_uses_azure_project_id_fallback(monkeypatch):
    from castia.observe.tracing import invoke_agent

    monkeypatch.setenv("FOUNDRY_AGENT_NAME", "prompty-agent")
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", "2")
    monkeypatch.delenv("FOUNDRY_PROJECT_RESOURCE_ID", raising=False)
    monkeypatch.delenv("AZURE_AI_PROJECT_RESOURCE_ID", raising=False)
    monkeypatch.setenv("AZURE_AI_PROJECT_ID", "/subscriptions/123/projects/demo")

    processors = observability._build_agent_identity_processors()

    assert len(processors) == 1
    span = mock.MagicMock()
    processors[0].on_start(span)
    span.set_attributes.assert_not_called()

    with invoke_agent():
        processors[0].on_start(span)

    span.set_attributes.assert_called_once_with(
        {
            "gen_ai.agent.name": "prompty-agent",
            "gen_ai.agent.version": "2",
            "gen_ai.agent.id": "prompty-agent:2",
            "microsoft.foundry.project.id": "/subscriptions/123/projects/demo",
            "gen_ai.azure_ai_project.id": "/subscriptions/123/projects/demo",
            "castia.telemetry.source": "castia",
        }
    )


def test_agent_identity_processor_marks_client_chat_scope(monkeypatch):
    from castia.observe.tracing import invoke_agent

    processor = observability._AgentIdentitySpanProcessor(
        {"castia.telemetry.source": "castia"}
    )
    span = mock.MagicMock(name="chat gpt-5.5")
    span.name = "chat gpt-5.5"

    with invoke_agent():
        processor.on_start(span)

    span.set_attributes.assert_called_once_with({"castia.telemetry.source": "castia"})
    span.set_attribute.assert_called_once_with(
        "castia.telemetry.scope",
        "client_roundtrip",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Configure Microsoft OpenTelemetry for Foundry and Agent 365."""

from __future__ import annotations

import contextlib
import functools
import logging
import os
from collections.abc import Iterator
from typing import Any

from microsoft.opentelemetry import use_microsoft_opentelemetry
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor

_logger = logging.getLogger("agent")
_TRACE_ASGI_INTERNAL_ENV = "CASTIA_OTEL_TRACE_ASGI_INTERNAL"
_TRACE_ASGI_SEND_ENV = "CASTIA_OTEL_TRACE_ASGI_SEND"
_TRACE_MSI_TOKEN_ENV = "CASTIA_OTEL_TRACE_MSI_TOKEN"
_IMDS_NOISE_FAST_THRESHOLD_MS = 2000
_PORTAL_NOISE_SPAN_PATHS = (
    "/azmonsdkdynamicconfiguration",
    "/metadata/identity/oauth2/token",
    "/metadata/instance/compute",
    "/msi/token",
)
_APP_INSIGHTS_CONNECTION_STRING_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"
_AZURE_MONITOR_CONNECTION_STRING_ENV = "AZURE_MONITOR_CONNECTION_STRING"
_DISTRO_A365_EXPORT_ENV = "ENABLE_A365_OBSERVABILITY_EXPORTER"


def configure_observability(
    *,
    enable_content_recording: bool | None = None,
    enable_genai_tracing: bool | None = None,
) -> None:
    """Enable Azure Monitor and Agent 365 telemetry before app imports.

    :param enable_content_recording: Whether the GenAI instrumentor records the
        prompt/response **content** (input/output text) onto its spans. This is
        what makes an agent's traces *evaluable* -- trace-based evaluators read
        the input/output content from the GenAI spans, which is only present when
        recording is on. It is **off by default** because it writes prompt and
        response text to Application Insights; enable it deliberately. Resolution
        order: this argument (when not ``None``) wins; otherwise the
        ``AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED`` environment variable
        (``"true"``/``"false"``); otherwise the default (off).
    :param enable_genai_tracing: Whether to enable the Foundry GenAI instrumentor
        at all. Castia enables client-side GenAI tracing by default so the SDK
        owns an app-level trace for every protocol, even when hosted Foundry also
        emits platform-side Responses spans. Resolution order: this argument
        (when not ``None``) wins; otherwise the
        ``AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING`` environment variable;
        otherwise the default (on).
    """
    logging.getLogger("agent").setLevel(logging.INFO)

    # Sample every span. The Microsoft distro defaults to a rate-limited sampler
    # (5 spans/sec). A single turn emits many Agents-SDK transport spans first,
    # which exhausts that budget, so the later model/GenAI span gets sampled out
    # and never reaches the Foundry Traces UI. A dropped span is also an
    # OpenTelemetry NonRecordingSpan, which the Foundry responses instrumentor
    # then trips over (see _guard_instrumentor_recording). always_on keeps every
    # span recording so model calls both surface in the portal and stay safe.
    # setdefault keeps it operator-overridable via the environment.
    os.environ.setdefault("OTEL_TRACES_SAMPLER", "always_on")

    attributes = {
        "service.name": os.environ.get(
            "FOUNDRY_AGENT_NAME",
            "castia-agent",
        ),
        "service.namespace": "castia",
    }
    agent_version = os.environ.get("FOUNDRY_AGENT_VERSION")
    if agent_version:
        attributes["service.version"] = agent_version

    azure_monitor_connection_string = _azure_monitor_connection_string()
    _configure_azure_core_tracing()
    identity_processors = _build_agent_identity_processors()
    a365_plan = _plan_a365_export()
    # The distro instruments azure-ai-projects during setup; publish the GenAI
    # flag first so that call agrees with Castia's decision instead of warning.
    _publish_genai_tracing_flag(enable_genai_tracing)

    try:
        with _masked_env(
            _DISTRO_A365_EXPORT_ENV if a365_plan.mask_distro_env else None
        ), _quiet_genai_disabled_warning():
            use_microsoft_opentelemetry(
                resource=Resource.create(attributes),
                enable_azure_monitor=bool(azure_monitor_connection_string),
                azure_monitor_connection_string=azure_monitor_connection_string,
                enable_a365=True,
                span_processors=identity_processors,
                instrumentation_options={
                    "fastapi": _fastapi_instrumentation_options(),
                    "openai_agents": {"enabled": False},
                },
                **a365_plan.options,
            )
    except Exception:  # telemetry must never break startup
        _logger.warning("OpenTelemetry setup failed; continuing without telemetry", exc_info=True)
        return
    _install_a365_agent_id_enricher(a365_plan, agent_version)
    _install_msi_token_span_filter()

    _enable_genai_tracing(
        enable_content_recording=enable_content_recording,
        enable_genai_tracing=enable_genai_tracing,
    )


@contextlib.contextmanager
def _masked_env(name: str | None) -> Iterator[None]:
    """Hide one environment variable for the duration of the block."""
    saved = os.environ.pop(name, None) if name else None
    try:
        yield
    finally:
        if name and saved is not None:
            os.environ[name] = saved


_GENAI_TRACING_ENV = "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"
_AI_PROJECT_INSTRUMENTOR_LOGGER = "azure.ai.projects.telemetry._ai_project_instrumentor"


def _publish_genai_tracing_flag(explicit: bool | None) -> bool:
    """Resolve the GenAI tracing flag (arg > env > on) and write it to the env."""
    enabled = _resolve_flag(explicit, _GENAI_TRACING_ENV, True)
    os.environ[_GENAI_TRACING_ENV] = "true" if enabled else "false"
    return enabled


class _GenAIDisabledWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not str(record.msg).startswith("GenAI tracing is not enabled")


@contextlib.contextmanager
def _quiet_genai_disabled_warning() -> Iterator[None]:
    """Drop the SDK's "GenAI tracing is not enabled" warning during distro setup.

    Castia owns that decision (and logs it); when it is off by choice the SDK's
    warning on the distro's own ``instrument()`` call is noise.
    """
    sdk_logger = logging.getLogger(_AI_PROJECT_INSTRUMENTOR_LOGGER)
    log_filter = _GenAIDisabledWarningFilter()
    sdk_logger.addFilter(log_filter)
    try:
        yield
    finally:
        sdk_logger.removeFilter(log_filter)


def _plan_a365_export() -> Any:
    """Choose A365 exporter options; fall back to exporter-off on any error."""
    from castia.observe.a365 import A365ExportPlan, plan_a365_export

    try:
        plan = plan_a365_export()
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.warning("Failed to configure Agent 365 export", exc_info=True)
        return A365ExportPlan(
            {"a365_enable_observability_exporter": False}, mask_distro_env=True
        )
    if plan.identity is not None:
        _logger.info(
            "Agent 365 S2S export enabled (agent instance %s)",
            plan.identity.instance_client_id,
        )
    return plan


def _install_a365_agent_id_enricher(plan: Any, agent_version: str | None) -> None:
    """Map Castia spans to the A365 S2S agent id on the A365 export path only."""
    if plan.identity is None:
        return
    try:
        from castia.observe.a365 import install_agent_id_enricher

        name = os.environ.get("FOUNDRY_AGENT_NAME", "castia-agent")
        foundry_agent_id = f"{name}:{agent_version}" if name and agent_version else None
        install_agent_id_enricher(plan.identity, foundry_agent_id=foundry_agent_id)
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.warning("Failed to install Agent 365 agent-id enricher", exc_info=True)


def _azure_monitor_connection_string() -> str | None:
    """Return the Azure Monitor connection string, accepting both common env names."""
    return (
        os.environ.get(_AZURE_MONITOR_CONNECTION_STRING_ENV)
        or os.environ.get(_APP_INSIGHTS_CONNECTION_STRING_ENV)
        or None
    )


def _resolve_flag(explicit: bool | None, env_var: str, default: bool) -> bool:
    """Resolve a tri-state config flag: explicit arg > env var > default.

    ``explicit`` wins whenever it is not ``None``. Otherwise the environment
    variable is consulted (case-insensitive ``"true"`` -> ``True``, any other
    set value -> ``False``). If the variable is unset, ``default`` is returned.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(env_var)
    if raw is not None:
        return raw.strip().lower() == "true"
    return default


def _fastapi_instrumentation_options() -> dict[str, list[str]]:
    """Keep framework traces readable by hiding internal ASGI event spans.

    OpenTelemetry's FastAPI instrumentation delegates to the ASGI middleware,
    which can emit a child span for every ``send`` and ``receive`` event.
    Streaming Responses turns produce many near-zero-duration ``POST /responses
    http send`` spans, and ``receive`` spans describe framework event handling
    rather than bounded agent work. They obscure the meaningful server, model,
    and tool spans without adding useful latency signal. Operators can opt back
    in when debugging the ASGI transport itself.
    """
    if _resolve_flag(None, _TRACE_ASGI_INTERNAL_ENV, False) or _resolve_flag(
        None, _TRACE_ASGI_SEND_ENV, False
    ):
        return {}
    return {"exclude_spans": ["send", "receive"]}


def _build_agent_identity_processors() -> list[SpanProcessor]:
    """Build the span processor that stamps hosted-agent identity onto spans.

    The Foundry Traces view's run-list query only surfaces an operation when at
    least one of its spans carries the hosted-agent identity --
    ``gen_ai.agent.id == "<name>:<version>"`` or
    (``gen_ai.agent.name`` and ``gen_ai.agent.version``) -- *and* the project
    resource id (``microsoft.foundry.project.id`` / ``gen_ai.azure_ai_project.id``).
    Stock hosted/prompt agents get these stamped from request baggage by the
    distro's own ``A365SpanProcessor``, but a custom (FastAPI-shaped) agent never
    populates that baggage and, for the project id, there is no baggage key at
    all. Without these attributes the portal returns "No runs or traces to
    display" even though valid ``gen_ai`` chat spans reach Application Insights.

    The processor MUST be handed to ``use_microsoft_opentelemetry`` via
    ``span_processors=`` so it is attached to the same TracerProvider that owns
    the Azure Monitor exporter (the distro merges it in ahead of the batch
    exporter, exactly like its own ``A365SpanProcessor``). Adding it afterwards
    with ``trace.get_tracer_provider().add_span_processor(...)`` does NOT work:
    the global provider is not the export provider, so the attributes never reach
    App Insights. Values come from the platform-injected ``FOUNDRY_AGENT_NAME`` /
    ``FOUNDRY_AGENT_VERSION`` and the ``FOUNDRY_PROJECT_RESOURCE_ID`` we set in
    ``azure.yaml``. Best-effort: telemetry must never break startup.
    """
    try:
        name = os.environ.get("FOUNDRY_AGENT_NAME", "castia-agent")
        version = os.environ.get("FOUNDRY_AGENT_VERSION")
        project_id = (
            os.environ.get("FOUNDRY_PROJECT_RESOURCE_ID")
            or os.environ.get("AZURE_AI_PROJECT_RESOURCE_ID")
            or os.environ.get("AZURE_AI_PROJECT_ID")
        )

        span_attributes: dict[str, str] = {}
        span_attributes["castia.telemetry.source"] = "castia"
        if name:
            span_attributes["gen_ai.agent.name"] = name
        if version:
            span_attributes["gen_ai.agent.version"] = version
        if name and version:
            span_attributes["gen_ai.agent.id"] = f"{name}:{version}"
        if project_id:
            span_attributes["microsoft.foundry.project.id"] = project_id
            span_attributes["gen_ai.azure_ai_project.id"] = project_id

        if not span_attributes:
            _logger.warning(
                "Agent identity unavailable; spans will not surface in the "
                "Foundry Traces UI (FOUNDRY_AGENT_VERSION/"
                "FOUNDRY_PROJECT_RESOURCE_ID unset)"
            )
            return []

        _logger.info(
            "Agent identity stamping enabled (%s)",
            span_attributes.get("gen_ai.agent.id", name),
        )
        return [_AgentIdentitySpanProcessor(span_attributes)]
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.warning("Failed to build agent identity processor", exc_info=True)
        return []


class _AgentIdentitySpanProcessor(SpanProcessor):
    """Stamp hosted-agent identity onto invocation spans at start.

    Setting the attributes at ``on_start`` guarantees they are present at export
    for spans created while Castia is handling a turn, including the
    azure-core-bridged ``chat {model}`` span. Spans emitted outside a Castia
    invocation are left alone so platform probes do not appear as standalone
    Foundry traces merely because they share the process.

    Exported type. The Azure-Monitor *dependency type* is computed at export from
    ``SpanKind`` **plus** the span's attributes and written (once, immutably) into
    App Insights. See ``azure/monitor/opentelemetry/exporter/export/trace/_exporter.py``:

    * ``SpanKind.INTERNAL`` -> ``"InProc"`` (renders **In Process**) *unless* the
      span carries ``gen_ai.system``, which overrides the type to a GenAI value.
    * ``SpanKind.CLIENT`` + an ``http.method`` / ``http.request.method``
      attribute -> ``"HTTP"`` (renders the verb nicely); other attribute families
      (db/messaging/rpc/gen_ai) map to their own types.
    * ``SpanKind.SERVER`` / ``CONSUMER`` -> a Request, not a dependency.
    * A ``CLIENT`` span whose recognised attributes are missing matches no branch
      and exports a blank type -> "Other".

    This processor only *adds* neutral agent-identity attributes (it never stamps
    ``gen_ai.system`` and never strips ``http.*``), so it does not change any
    span's exported type. Keeping it that way keeps the App Insights ``type``
    column clean.

    Note on the UI badge. The **kind** badge shown in the Foundry Traces tree is
    NOT read straight from the exported type. The portal's detail query derives it
    and is currently non-deterministic: it unions each span's real type with a
    hardcoded ``span_type = "default"`` row and collapses them with ``any()``, so
    the framework/HTTP badge can flip to "Other" between reads with no data change.
    That flip is portal-side, not from this processor; the exported type in App
    Insights stays correct. See ``packages/python/TRACING.md`` for the full
    write-up and repro.
    """

    def __init__(self, attributes: dict[str, str]) -> None:
        self._attributes = dict(attributes)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        try:
            from castia.observe.tracing import is_agent_invocation_active

            if not is_agent_invocation_active():
                return
            span.set_attributes(self._attributes)
            name = str(getattr(span, "name", "") or "")
            if name.startswith("chat "):
                span.set_attribute("castia.telemetry.scope", "client_roundtrip")
        except Exception:  # pragma: no cover - telemetry must never break a turn
            _logger.debug("Failed to stamp agent identity on span", exc_info=True)


class _MsiTokenFilteringSpanProcessor(SpanProcessor):
    """Skip export for ordinary successful Azure SDK metadata noise."""

    _castia_msi_filter = True

    def __init__(self, delegate: SpanProcessor) -> None:
        self._delegate = delegate

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span: Any) -> None:
        if _should_suppress_msi_token_span(span):
            return
        self._delegate.on_end(span)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis=timeout_millis)


def _enable_genai_tracing(
    *,
    enable_content_recording: bool | None = None,
    enable_genai_tracing: bool | None = None,
) -> None:
    """Enable the official Foundry GenAI instrumentor.

    Without this the agent emits only Agents-SDK transport spans
    (``agents.app.run`` etc.). Those land in App Insights but never surface as
    runs in the Foundry portal's Traces view, which only renders spans that
    follow the GenAI semantic conventions (``gen_ai.operation.name`` /
    ``gen_ai.system``). ``AIProjectInstrumentor`` patches the OpenAI Responses
    API so every model call emits a ``chat {model}`` span carrying those
    attributes, which is exactly what the Foundry UI keys off.

    ``enable_content_recording`` / ``enable_genai_tracing`` follow the same
    arg > env > default resolution documented on :func:`configure_observability`.

    Best-effort: any failure here is logged, never raised. Telemetry must never
    take down startup or a turn.
    """
    try:
        # Reflect the decision back onto the env var the SDK re-checks at
        # instrument() time, so an explicit argument and the SDK's own gate agree.
        genai_enabled = _publish_genai_tracing_flag(enable_genai_tracing)
        if not genai_enabled:
            _logger.info("GenAI tracing instrumentor disabled")
            return

        _configure_azure_core_tracing()
        from azure.ai.projects.telemetry import AIProjectInstrumentor

        # Whether the model call's input/output *content* is recorded onto the
        # GenAI spans -- what makes traces evaluable. Off by default (records
        # prompt/response text to App Insights); overridable by arg or env.
        content_recording = _resolve_flag(
            enable_content_recording,
            "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED",
            False,
        )
        AIProjectInstrumentor().instrument(enable_content_recording=content_recording)
        _guard_instrumentor_recording()
        _logger.info(
            "GenAI tracing instrumentor enabled (content_recording=%s)",
            content_recording,
        )
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.warning("Failed to enable GenAI tracing instrumentor", exc_info=True)


def _guard_instrumentor_recording() -> None:
    """Harden the Foundry responses instrumentor against non-recording spans.

    ``azure-ai-projects==2.5.0`` reads ``span.span_instance.attributes`` inside
    ``_append_to_message_attribute`` without first checking ``is_recording()``.
    When a span is sampled out it is an OpenTelemetry ``NonRecordingSpan``, which
    has no ``attributes`` member, so that read raises ``AttributeError`` and the
    whole agent turn fails ("Sorry, something went wrong handling your message").
    We already force ``always_on`` sampling so the model span records, but wrap
    the method as a second line of defence: telemetry must never break a turn,
    even if an operator overrides the sampler back to a dropping one.
    """
    try:
        from azure.ai.projects.telemetry import _responses_instrumentor as ri

        cls = ri._ResponsesInstrumentorPreview
        original = cls._append_to_message_attribute
        if getattr(original, "_recording_guarded", False):
            return

        @functools.wraps(original)
        def _guarded(self: Any, span: Any, attribute_name: str, new_messages: Any) -> None:
            span_instance = getattr(span, "span_instance", None)
            if span_instance is None or not span_instance.is_recording():
                return None
            return original(self, span, attribute_name, new_messages)

        _guarded._recording_guarded = True  # type: ignore[attr-defined]
        cls._append_to_message_attribute = _guarded
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.warning("Failed to guard GenAI instrumentor", exc_info=True)


def _azure_core_tracing_implementation():
    """Return the Azure SDK OTel span implementation Castia wants by default."""
    from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan

    if _resolve_flag(None, _TRACE_MSI_TOKEN_ENV, False):
        return OpenTelemetrySpan

    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan

    class CastiaOpenTelemetrySpan(OpenTelemetrySpan):
        def __init__(self, span: Any = None, name: str | None = "span", **kwargs: Any) -> None:
            if span is None and _is_metadata_probe_span_name(name):
                self._current_ctxt_manager = None
                self._span_instance = NonRecordingSpan(
                    trace.get_current_span().get_span_context()
                )
                return
            super().__init__(span=span, name=name, **kwargs)

    return CastiaOpenTelemetrySpan


def _install_msi_token_span_filter() -> None:
    """Drop successful/fast managed-identity token spans before export."""
    if _resolve_flag(None, _TRACE_MSI_TOKEN_ENV, False):
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        active_processor = getattr(provider, "_active_span_processor", None)
        processors = getattr(active_processor, "_span_processors", None)
        if not processors:
            return
        filtered_processors = tuple(
            processor
            if getattr(processor, "_castia_msi_filter", False)
            else _MsiTokenFilteringSpanProcessor(processor)
            for processor in processors
        )
        active_processor._span_processors = filtered_processors
    except Exception:  # pragma: no cover - telemetry must never break startup
        _logger.debug("Failed to install MSI token span filter", exc_info=True)


def _should_suppress_msi_token_span(span: Any) -> bool:
    if not _is_portal_noise_export_span(span):
        return False
    if _span_has_exception_evidence(span):
        return False
    duration_ms = _span_duration_ms(span)
    return duration_ms is not None and duration_ms <= _IMDS_NOISE_FAST_THRESHOLD_MS


def _is_portal_noise_export_span(span: Any) -> bool:
    name = str(getattr(span, "name", "") or "").lower()
    attributes = getattr(span, "attributes", {}) or {}
    fields = [
        name,
        str(attributes.get("http.url", "") or "").lower(),
        str(attributes.get("url.full", "") or "").lower(),
        str(attributes.get("http.target", "") or "").lower(),
        str(attributes.get("target", "") or "").lower(),
        str(attributes.get("server.address", "") or "").lower(),
        str(attributes.get("net.peer.name", "") or "").lower(),
        str(attributes.get("http.host", "") or "").lower(),
    ]
    return any(path in field for field in fields for path in _PORTAL_NOISE_SPAN_PATHS)


def _span_has_exception_evidence(span: Any) -> bool:
    attributes = getattr(span, "attributes", {}) or {}
    if any(str(key).startswith("exception.") for key in attributes):
        return True
    events = getattr(span, "events", ()) or ()
    return any(str(getattr(event, "name", "") or "").lower() == "exception" for event in events)


def _span_duration_ms(span: Any) -> float | None:
    start_time = getattr(span, "start_time", None)
    end_time = getattr(span, "end_time", None)
    if start_time is None or end_time is None:
        return None
    return (end_time - start_time) / 1_000_000


def _configure_azure_core_tracing() -> None:
    """Install Castia's Azure SDK tracing implementation before SDK clients run."""
    from azure.core.settings import settings

    current_tracing = settings.tracing_implementation()
    if current_tracing is None or _is_stock_azure_otel_span(current_tracing):
        settings.tracing_implementation = _azure_core_tracing_implementation()


def _is_metadata_probe_span_name(name: str | None) -> bool:
    lowered = str(name or "").lower()
    return any(path in lowered for path in _PORTAL_NOISE_SPAN_PATHS)


def _is_stock_azure_otel_span(implementation: Any) -> bool:
    return (
        getattr(implementation, "__name__", "") == "OpenTelemetrySpan"
        and getattr(implementation, "__module__", "")
        == "azure.core.tracing.ext.opentelemetry_span"
    )


def flush_telemetry(timeout_millis: int = 3000) -> None:
    """Force-export buffered spans and logs before the turn returns.

    Hosted agents can be frozen between requests, which would strand telemetry
    still sitting in the batch processors. Flushing at the end of every turn
    makes delivery to Azure Monitor and Agent 365 reliable regardless of the
    container's lifecycle. Best-effort: any failure is logged, never raised.
    """
    from opentelemetry import trace
    from opentelemetry._logs import get_logger_provider

    for provider in (trace.get_tracer_provider(), get_logger_provider()):
        flush = getattr(provider, "force_flush", None)
        if flush is None:
            continue
        try:
            flush(timeout_millis)
        except Exception:  # pragma: no cover - telemetry must never break a turn
            _logger.debug("Telemetry flush failed", exc_info=True)

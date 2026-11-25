"""Local telemetry and daily feature-drift evidence.

No network calls occur at import or suite construction. Example::

    suite = FunctionalSuite("daily", ("project.read", "model.respond"))
    config = LiveConfig(project_read_url=discovered_project_url,
                        model_url=discovered_responses_url, model="deployment")
    with live_probes(config) as probes:
        report = run_suite(suite, probes=probes,
                           prerequisites=config.prerequisites())
    print(report.to_json())

URLs include discovered protocol paths/API versions. Teams/Graph/identity need
their own fixtures and probes; model success never certifies those integrations.
Hosted Responses accepts ``hosted_responses_body`` and an optional
``hosted_responses_expected_text`` assertion for the selected agent's task.
To observe ingestion, stamp ``castia.probe_tag`` on hosted spans (the HTTP probes
send ``x-castia-probe-tag``, which your app must explicitly map to that attribute)
and pass an agent/time/tag-scoped query callable to ``verify_probe``.
For non-HTTP ``invoke_agent`` delivery tests, select ``TraceQuery(anchor="invoke_agent",
trace_id=...)``; the default anchor remains hosted ``requests``. Probe ID
attributes ``castia.probe_id`` and ``probe_id`` normalize to ``probe_tag`` too.

Reports exclude model/tool content. Telemetry content requires an explicit
``TraceQuery(include_content=True)``. Limits bound outbound calls and requested
output tokens, not input-token usage, hosted internal calls, optimizer compute,
or money. Actual cost is unknown. Optimizer submission is opt-in, reserves a
cancellation request, and never applies a candidate or deploys anything.
Fine-tuning probes can only validate inputs or consume an existing deployment.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from castia.observe.correlation import (
        inject_traceparent,
        new_traceparent,
        record_human_evaluation,
        trace_http_client,
    )
    from castia.observe.live import (
        Limits,
        LiveConfig,
        LiveProbes,
        RestAdapter,
        live_probes,
    )
    from castia.observe.probes import telemetry_probes
    from castia.observe.records import ExecutionRecord, normalize_record, summarize
    from castia.observe.server_tools import record_server_tools
    from castia.observe.suite import (
        FEATURE_CATALOG,
        Feature,
        FeatureResult,
        FunctionalSuite,
        ProbeResult,
        SuiteReport,
        compare_reports,
        run_suite,
    )
    from castia.observe.telemetry import (
        AppInsightsClient,
        ObserveError,
        TraceQuery,
        VerificationResult,
        verify_probe,
    )
    from castia.observe.tool_capture import (
        annotate_tool_capture,
        blob_capture_sink,
        configure_tool_capture,
        file_capture_sink,
        load_tool_captures,
        match_tool_captures,
        tool_capture_health,
        tool_capture_scope,
        verify_tool_capture,
    )
    from castia.observe.tracing import (
        TraceRecord,
        clear_span_filters,
        clear_trace_sinks,
        jsonl_trace_sink,
        otel_trace_sink,
        record_request_parameters,
        record_system_instructions,
        register_span_filter,
        register_trace_sink,
        registered_span_filters,
        registered_trace_sinks,
        remove_span_filter,
        remove_trace_sink,
        suppress_telemetry_spans,
        trace_attribute,
        trace_step,
    )

__all__ = [
    "FEATURE_CATALOG",
    "AppInsightsClient",
    "ExecutionRecord",
    "Feature",
    "FeatureResult",
    "FunctionalSuite",
    "Limits",
    "LiveConfig",
    "LiveProbes",
    "ObserveError",
    "ProbeResult",
    "RestAdapter",
    "SuiteReport",
    "TraceQuery",
    "TraceRecord",
    "VerificationResult",
    "annotate_tool_capture",
    "blob_capture_sink",
    "clear_span_filters",
    "clear_trace_sinks",
    "compare_reports",
    "configure_tool_capture",
    "file_capture_sink",
    "inject_traceparent",
    "jsonl_trace_sink",
    "live_probes",
    "load_tool_captures",
    "match_tool_captures",
    "new_traceparent",
    "normalize_record",
    "otel_trace_sink",
    "record_human_evaluation",
    "record_request_parameters",
    "record_server_tools",
    "record_system_instructions",
    "register_span_filter",
    "register_trace_sink",
    "registered_span_filters",
    "registered_trace_sinks",
    "remove_span_filter",
    "remove_trace_sink",
    "run_suite",
    "summarize",
    "suppress_telemetry_spans",
    "telemetry_probes",
    "tool_capture_health",
    "tool_capture_scope",
    "trace_attribute",
    "trace_http_client",
    "trace_step",
    "verify_probe",
    "verify_tool_capture",
]

_EXPORT_MODULES = {
    **dict.fromkeys((
        "inject_traceparent", "new_traceparent", "record_human_evaluation", "trace_http_client",
    ), "correlation"),
    "record_server_tools": "server_tools",
    **dict.fromkeys(("Limits", "LiveConfig", "LiveProbes", "RestAdapter", "live_probes"), "live"),
    "telemetry_probes": "probes",
    **dict.fromkeys(("ExecutionRecord", "normalize_record", "summarize"), "records"),
    **dict.fromkeys((
        "FEATURE_CATALOG", "Feature", "FeatureResult", "FunctionalSuite",
        "ProbeResult", "SuiteReport", "compare_reports", "run_suite",
    ), "suite"),
    **dict.fromkeys((
        "AppInsightsClient", "ObserveError", "TraceQuery", "VerificationResult", "verify_probe",
    ), "telemetry"),
    **dict.fromkeys((
        "annotate_tool_capture",
        "blob_capture_sink",
        "configure_tool_capture",
        "file_capture_sink",
        "load_tool_captures",
        "match_tool_captures",
        "tool_capture_health",
        "tool_capture_scope",
        "verify_tool_capture",
    ), "tool_capture"),
    **dict.fromkeys((
        "TraceRecord", "clear_span_filters", "clear_trace_sinks", "jsonl_trace_sink",
        "otel_trace_sink", "record_request_parameters", "record_system_instructions",
        "register_span_filter", "register_trace_sink",
        "registered_span_filters", "registered_trace_sinks", "remove_span_filter",
        "remove_trace_sink", "suppress_telemetry_spans", "trace_attribute", "trace_step",
    ), "tracing"),
}


def __getattr__(name: str):
    # Runtime tracing must not load the offline suite or live service clients.
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{_EXPORT_MODULES[name]}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

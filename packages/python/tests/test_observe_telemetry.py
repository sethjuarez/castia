import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from castia.observe import (
    AppInsightsClient,
    ExecutionRecord,
    ObserveError,
    TraceQuery,
    telemetry_probes,
    verify_probe,
)

START = datetime(2026, 9, 15, tzinfo=UTC)


class Credential:
    def get_token(self, scope):
        assert scope == "https://api.applicationinsights.io/.default"
        return SimpleNamespace(token="NEVER_PRINT")


def query(**kwargs):
    return TraceQuery("agent", START, START + timedelta(hours=1), **kwargs)


def test_query_agent_version_tag_bound_and_content_projection():
    kql = query(limit=19, agent_version="3", probe_tag="test-tag").to_kql()
    assert "requests, dependencies, customEvents" in kql
    assert "timestamp >=" in kql and "timestamp <" in kql
    assert 'agent_name == "agent"' in kql and 'agent_id == "agent:3"' in kql
    assert 'probe_tag == "test-tag"' in kql
    assert kql.endswith("| take 19")
    assert "customDimensions=bag_pack(" in kql
    assert "gen_ai.input.messages" not in kql
    assert "gen_ai.input.messages" in query(include_content=True).to_kql()


def test_query_selects_hosted_requests_then_correlates_downstream_spans():
    kql = query(agent_version="3").to_kql()
    selected_requests, child_query = kql.split(";\n", 1)
    assert selected_requests.startswith("let scoped_operations = requests")
    assert 'customDimensions["azure.ai.agentserver.agent_name"]' in selected_requests
    assert 'agent_name == "agent" or hosted_agent_name == "agent"' in selected_requests
    assert "| where isnotempty(operation_Id)" in selected_requests
    assert "summarize arg_max(" in selected_requests
    assert child_query.startswith("union isfuzzy=true withsource=castia_table_name requests, dependencies, customEvents")
    assert "| join kind=inner (scoped_operations) on operation_Id" in child_query
    assert "| where agent_name" not in child_query
    assert "| where hosted_agent_name" not in child_query
    assert 'agent_name="agent", agent_version=root_agent_version' in child_query
    assert "timestamp >=" in selected_requests and "timestamp >=" in child_query


def test_tag_filter_applies_after_correlation_and_accepts_inherited_root_tag():
    kql = query(probe_tag="probe").to_kql()
    selected_requests, child_query = kql.split(";\n", 1)
    assert '| where probe_tag == "probe"' not in selected_requests
    assert 'probe_tag=coalesce(tostring(customDimensions["castia.probe_tag"]),' in child_query
    assert 'tostring(customDimensions["probe_id"]), root_probe_tag)' in child_query
    assert '| where probe_tag == "probe"' in child_query
    assert "| take" not in selected_requests
    assert child_query.endswith("| take 100")


def test_query_escapes_untrusted_agent_and_tag():
    name = 'agent"\n| take 100000\n//'
    kql = TraceQuery(name, START, START + timedelta(hours=1), probe_tag=name).to_kql()
    assert json.dumps(name) in kql
    assert "\n| take 100000" not in kql


@pytest.mark.parametrize("limit", [0, -1, True, 1.2, 10001, float("nan")])
def test_query_rejects_invalid_row_bounds(limit):
    with pytest.raises(ValueError):
        query(limit=limit)


@pytest.mark.parametrize("kwargs", [{"probe_tag": ""}, {"agent_version": ""}, {"include_content": "true"}])
def test_query_rejects_invalid_options(kwargs):
    with pytest.raises((ValueError, TypeError)):
        query(**kwargs)


def test_query_requires_aware_ordered_window():
    with pytest.raises(ValueError):
        TraceQuery("agent", START.replace(tzinfo=None), START)
    with pytest.raises(ValueError):
        TraceQuery("agent", START, START)


def test_adapter_query_normalizes_and_keeps_content_excluded():
    def handle(request):
        assert request.headers["Authorization"] == "Bearer NEVER_PRINT"
        assert request.url.path.endswith("/app-id/query")
        assert "gen_ai.input.messages" not in json.loads(request.content)["query"]
        return httpx.Response(200, json={"tables": [{
            "columns": [{"name": "timestamp"}, {"name": "customDimensions"}],
            "rows": [[START.isoformat(), {
                "gen_ai.agent.name": "agent", "gen_ai.input.messages": "PRIVATE PROMPT",
            }]],
        }]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = AppInsightsClient("app-id", credential=Credential(), client=http)
        result = client.query(query())
        assert result[0].agent_name == "agent"
        assert "PRIVATE PROMPT" not in str(result)


@pytest.mark.parametrize(
    ("status", "category"),
    [(401, "authentication"), (403, "authorization"), (404, "not_found"), (429, "throttled"), (500, "service"), (400, "request")],
)
def test_adapter_classifies_http_errors_without_auth_content(status, category):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text="Bearer NEVER_PRINT PRIVATE PROMPT")
    )) as http:
        client = AppInsightsClient("app-id", credential=Credential(), client=http)
        with pytest.raises(ObserveError) as caught:
            client.query(query())
        assert caught.value.category == category
        assert "NEVER_PRINT" not in str(caught.value)
        assert "PRIVATE PROMPT" not in str(caught.value)
        assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [{}, [], {"tables": []}, {"tables": [{"columns": [{"name": "a"}], "rows": [[1, 2]]}]}],
)
def test_adapter_rejects_malformed_query_results(payload):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as http:
        with pytest.raises(ObserveError) as caught:
            AppInsightsClient("id", credential=Credential(), client=http).query(query())
        assert caught.value.category == "invalid_response"


def test_adapter_rejects_partial_results_even_with_rows():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"error": {"code": "PartialError"}, "tables": []})
    )) as http:
        with pytest.raises(ObserveError) as caught:
            AppInsightsClient("id", credential=Credential(), client=http).query(query())
        assert caught.value.category == "partial_query"


def test_adapter_classifies_timeout():
    def handle(request):
        raise httpx.ReadTimeout("Authorization: NEVER_PRINT", request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ObserveError) as caught:
            AppInsightsClient("id", credential=Credential(), client=http).query(query())
        assert caught.value.category == "timeout"
        assert "NEVER_PRINT" not in str(caught.value)


class Clock:
    value = 0.

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_tag_verification_ignores_unrelated_rows_and_waits_bounded():
    clock = Clock()
    calls = []
    def fetch():
        calls.append(True)
        return [ExecutionRecord(probe_tag="wanted" if len(calls) == 3 else "unrelated")]
    result = verify_probe(fetch, probe_tag="wanted", clock=clock, sleep=clock.sleep, poll_seconds=2)
    assert result.status == "pass"
    assert result.attempts == 3 and result.elapsed_seconds == 4
    assert result.matched_records == 1


def test_tag_verification_missing_is_blocked_not_fake_pass_or_agent_failure():
    clock = Clock()
    result = verify_probe(
        list, probe_tag="wanted", timeout_seconds=3, poll_seconds=2,
        clock=clock, sleep=clock.sleep,
    )
    assert result.status == "blocked" and result.attempts == 2
    assert clock.value == 3


def test_tag_verification_has_attempt_bound_even_with_stuck_clock():
    result = verify_probe(
        list, probe_tag="wanted", max_attempts=3, clock=lambda: 0, sleep=lambda _: None,
    )
    assert result.attempts == 3 and result.status == "blocked"


def test_tag_verification_propagates_query_errors():
    def fetch():
        raise ObserveError("authorization", "Not authorized.")
    with pytest.raises(ObserveError, match="Not authorized"):
        verify_probe(fetch, probe_tag="wanted")


def test_telemetry_probes_distinguish_access_from_ingestion_and_always_minimize():
    calls = []
    class Client:
        def query(self, scoped):
            calls.append(scoped)
            return []
    clock = Clock()
    probes = telemetry_probes(
        Client(), query(include_content=True, probe_tag="probe"),
        timeout_seconds=3, poll_seconds=1, clock=clock, sleep=clock.sleep,
    )
    assert not calls
    result = probes["telemetry.query"]()
    assert result.status == "pass" and result.evidence["summary"]["record_count"] == 0
    result = probes["telemetry.ingestion"]()
    assert result.status == "blocked"
    assert all(not call.include_content and call.probe_tag == "probe" for call in calls)


def test_telemetry_ingestion_probe_rejects_missing_tag_without_querying():
    class Client:
        def query(self, scoped):
            raise AssertionError("must not query")
    result = telemetry_probes(Client(), query())["telemetry.ingestion"]()
    assert result.status == "blocked"


def test_app_insights_credentials_failure_is_authentication():
    class BrokenCredential:
        def get_token(self, scope):
            raise RuntimeError("SECRET")
    with pytest.raises(ObserveError) as caught:
        AppInsightsClient("id", credential=BrokenCredential()).query(query())
    assert caught.value.category == "authentication"
    assert "SECRET" not in str(caught.value)


def test_local_delivery_query_explicitly_anchors_named_invoke_agent_spans():
    kql = query(anchor="invoke_agent", trace_id="trace-id", probe_tag="probe-id").to_kql()
    anchor_query, child_query = kql.split(";\n", 1)
    assert anchor_query.startswith("let scoped_operations = union isfuzzy=true dependencies, customEvents")
    assert 'customDimensions["gen_ai.operation.name"]) == "invoke_agent"' in anchor_query
    assert '| where operation_Id == "trace-id"' in anchor_query
    assert 'agent_name == "agent"' in anchor_query
    assert "castia.probe_id" in child_query
    assert 'probe_tag == "probe-id"' in child_query
    assert "| join kind=inner (scoped_operations) on operation_Id" in child_query


@pytest.mark.parametrize("kwargs", [{"anchor": "any"}, {"trace_id": ""}, {"trace_id": 7}])
def test_query_rejects_invalid_anchor_or_trace_id(kwargs):
    with pytest.raises(ValueError):
        query(**kwargs)


def test_union_table_provenance_does_not_collide_with_requests_source_column():
    kql = query().to_kql()
    # requests already has a standard `source` column; withsource=source is SEM0001.
    assert "withsource=source " not in kql
    assert "withsource=castia_table_name " in kql
    assert "| project timestamp, source=castia_table_name," in kql

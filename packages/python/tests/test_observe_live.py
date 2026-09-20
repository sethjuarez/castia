import json
from types import SimpleNamespace

import httpx
import pytest

from castia.observe import (
    FunctionalSuite,
    Limits,
    LiveConfig,
    ObserveError,
    RestAdapter,
    live_probes,
    run_suite,
)

URL = "https://project.example/api/projects/demo"
MODEL_URL = URL + "/openai/responses?api-version=explicit-version"


class Adapter:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def token(self, scope):
        return "SECRET"

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    value = 0.

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def text_response(text="CASTIA_OK"):
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
    }


def report_for(features, adapter, **kwargs):
    config = LiveConfig(model_url=MODEL_URL, model="deployment", **kwargs)
    with live_probes(config, adapter=adapter) as probes:
        report = run_suite(
            FunctionalSuite("daily", tuple(features)), probes=probes, prerequisites=config.prerequisites(),
        )
    return report


def selected(report, feature):
    return next(row for row in report.results if row.feature_id == feature)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_requests": 0}, {"max_requests": True}, {"max_requests": 1.5},
        {"max_seconds": -1}, {"max_seconds": float("nan")}, {"max_seconds": float("inf")},
        {"max_output_tokens": 0}, {"max_output_tokens": 2000},
        {"max_total_output_tokens": -1}, {"max_response_bytes": 0},
    ],
)
def test_limits_are_positive_finite_and_consistent(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


@pytest.mark.parametrize("url", ["http://example.com", "https://user:pass@example.com", "https://example.com/#token", "not-url"])
def test_urls_must_be_explicit_https_without_embedded_auth(url):
    with pytest.raises(ValueError):
        LiveConfig(hosted_responses_url=url)


def test_construction_does_no_network_and_never_registers_finetune_submission():
    adapter = Adapter()
    probes = live_probes(LiveConfig(), adapter=adapter)
    assert not adapter.calls
    assert "optimizer.run" in probes
    assert not any("finetune" in key or "deploy" in key or "apply" in key for key in probes)


def test_real_model_request_has_caps_and_metadata_only_report():
    adapter = Adapter(text_response())
    report = report_for(["model.respond"], adapter, probe_tag="tag")
    assert report.passed
    method, url, request = adapter.calls[0]
    assert method == "POST" and url == MODEL_URL
    assert request["json_body"]["max_output_tokens"] == 128
    assert request["json_body"]["model"] == "deployment"
    assert request["headers"]["x-castia-probe-tag"] == "tag"
    assert request["timeout_seconds"] <= 120
    assert "CASTIA_OK" not in report.to_json()
    assert selected(report, "model.respond").evidence["usage"]["input_tokens"] == 7


def test_model_text_wrong_or_empty_is_not_pass():
    for response in [text_response(""), text_response("incorrect"), {"error": {"message": "SECRET"}}]:
        report = report_for(["model.respond"], Adapter(response))
        assert not report.passed
        assert "SECRET" not in report.to_json()


def test_stream_requires_deltas_and_completed_event():
    events = [
        {"type": "response.output_text.delta", "delta": "CASTIA_"},
        {"type": "response.output_text.delta", "delta": "OK"},
        {"type": "response.completed", "response": text_response()},
    ]
    report = report_for(["model.stream"], Adapter(events))
    assert report.passed
    report = report_for(["model.stream"], Adapter(events[:-1]))
    assert not report.passed
    report = report_for(["model.stream"], Adapter(events + [{"type": "response.failed"}]))
    assert not report.passed


def test_function_call_executes_then_sends_matching_output():
    adapter = Adapter(
        {"output": [{"type": "function_call", "name": "castia_probe_add", "call_id": "call-1", "arguments": '{"a":2,"b":3}'}]},
        text_response("5"),
    )
    report = report_for(["model.function_call"], adapter)
    assert report.passed and len(adapter.calls) == 2
    second = adapter.calls[1][2]["json_body"]
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "call-1", "output": "5"}
    assert second["tool_choice"] == "none"
    assert selected(report, "model.function_call").evidence["function_executed"] is True


@pytest.mark.parametrize(
    "call", [
        {"type": "function_call", "name": "unknown", "call_id": "1", "arguments": "{}"},
        {"type": "function_call", "name": "castia_probe_add", "call_id": "1", "arguments": '{"a":1,"b":3}'},
        {"type": "function_call", "name": "castia_probe_add", "call_id": "1", "arguments": "bad json"},
    ],
)
def test_wrong_function_never_executed(call):
    adapter = Adapter({"output": [call]})
    report = report_for(["model.function_call"], adapter)
    assert not report.passed and len(adapter.calls) == 1


def test_budget_is_shared_across_probes_and_no_additional_requests_occur():
    adapter = Adapter({"id": "project"}, text_response())
    report = report_for(
        ["project.read", "model.respond"], adapter,
        project_read_url=URL, limits=Limits(max_requests=1),
    )
    assert len(adapter.calls) == 1
    assert selected(report, "project.read").status == "pass"
    assert selected(report, "model.respond").status == "blocked"
    assert selected(report, "model.respond").category == "budget"


def test_output_token_budget_blocks_second_function_round_trip():
    adapter = Adapter({"output": [{
        "type": "function_call", "name": "castia_probe_add", "call_id": "1", "arguments": '{"a":2,"b":3}',
    }]})
    report = report_for(
        ["model.function_call"], adapter,
        limits=Limits(max_output_tokens=128, max_total_output_tokens=128),
    )
    assert len(adapter.calls) == 1
    assert selected(report, "model.function_call").category == "budget"


def test_hosted_protocol_urls_are_preserved_not_guessed():
    explicit = "https://endpoint.example/unusual/deployment/response?api-version=chosen"
    adapter = Adapter(text_response())
    report = report_for(["runtime.responses"], adapter, hosted_responses_url=explicit)
    assert report.passed
    assert adapter.calls[0][1] == explicit
    assert not report_for(["runtime.responses"], Adapter()).passed


def test_invocations_needs_discovered_body_and_response_contract():
    config = {
        "hosted_invocations_url": "https://endpoint.example/custom/invoke",
        "hosted_invocations_body": {"messages": [{"role": "user", "content": "hello"}]},
        "hosted_invocations_output_field": "answer",
    }
    adapter = Adapter({"answer": "hello"})
    report = report_for(["runtime.invocations"], adapter, **config)
    assert report.passed and adapter.calls[0][2]["json_body"] == config["hosted_invocations_body"]
    missing = report_for(["runtime.invocations"], Adapter(), hosted_invocations_url=config["hosted_invocations_url"])
    assert selected(missing, "runtime.invocations").status == "blocked"


def test_model_does_not_prove_teams_or_graph():
    report = report_for(["model.respond", "teams.direct", "graph.read"], Adapter(text_response()))
    assert selected(report, "model.respond").status == "pass"
    assert selected(report, "teams.direct").status == "blocked"
    assert selected(report, "graph.read").status == "blocked"


def test_toolbox_requires_explicit_tools_and_checks_actual_execution():
    adapter = Adapter({"output": [{"type": "mcp_call", "name": "web", "output": "SECRET_CONTENT"}]})
    report = report_for(
        ["tools.toolbox"], adapter, toolbox_url=URL + "/toolboxes/known/mcp?api-version=v1",
        toolbox_tools=("web",), toolbox_prompt="Use web to read example.com.",
    )
    assert report.passed
    spec = adapter.calls[0][2]["json_body"]["tools"][0]
    assert spec["allowed_tools"] == ["web"]
    assert spec["headers"]["Authorization"] == "Bearer SECRET"
    assert "SECRET" not in report.to_json()
    assert all(not key.startswith("x-castia-") for key in spec)


@pytest.mark.parametrize(
    "response", [
        text_response(),
        {"output": [{"type": "mcp_call", "name": "web", "error": "SECRET"}]},
        {"output": [{"type": "mcp_call", "name": "unknown", "output": "text"}]},
    ],
)
def test_toolbox_model_answer_without_tool_evidence_is_failure(response):
    report = report_for(
        ["tools.toolbox"], Adapter(response), toolbox_url=URL + "/toolboxes/known/mcp?api-version=v1",
        toolbox_tools=("web",), toolbox_prompt="Use web to read example.com.",
    )
    assert not report.passed and "SECRET" not in report.to_json()


def test_optimizer_reads_only_selected_job_candidate_and_never_applies():
    adapter = Adapter({"status": "Completed"}, {"instructions": "PRIVATE CONTENT"})
    report = report_for(
        ["optimizer.status", "optimizer.candidate"], adapter, project_endpoint=URL,
        optimizer_job_id="job/escaped", optimizer_candidate_id="candidate",
    )
    assert report.passed
    assert all(call[0] == "GET" for call in adapter.calls)
    assert "job%2Fescaped" in adapter.calls[0][1]
    assert adapter.calls[1][1].endswith("/candidates/candidate/config?api-version=v1")
    assert adapter.calls[0][2]["headers"]["Foundry-Features"] == "AgentsOptimization=V2Preview"
    assert "PRIVATE CONTENT" not in report.to_json()


def optimizer_config(**kwargs):
    return LiveConfig(
        project_endpoint=URL, allow_optimizer_submit=True,
        optimizer_request={"options": {"max_candidates": 1}},
        optimizer_timeout_seconds=4, optimizer_poll_seconds=2,
        limits=Limits(max_requests=5, max_seconds=15), **kwargs,
    )


def test_optimizer_submit_requires_opt_in_and_valid_candidate_bound():
    probes = live_probes(LiveConfig(), adapter=Adapter())
    with pytest.raises(ObserveError) as caught:
        probes["optimizer.run"]()
    assert caught.value.category == "prerequisite"
    for request in [{}, {"options": {"max_candidates": 4}}, {"options": {"max_candidates": True}}]:
        with pytest.raises(ValueError):
            LiveConfig(project_endpoint=URL, allow_optimizer_submit=True, optimizer_request=request)


def test_optimizer_bounded_poll_timeout_cancels_and_reports_uncertain_remote_cost():
    clock = Clock()
    adapter = Adapter({"id": "job"}, {"status": "Running"}, {"status": "Running"}, {})
    probes = live_probes(optimizer_config(), adapter=adapter, clock=clock, sleep=clock.sleep)
    result = probes["optimizer.run"]()
    assert result.status == "fail" and result.category == "timeout"
    assert result.evidence["cancellation"] == "requested_not_confirmed"
    assert result.evidence["cleanup_pending"] is True
    assert result.evidence["remote_job_may_be_running"] is True
    assert adapter.calls[-1][1].endswith("/job:cancel?api-version=v1")
    assert adapter.calls[0][2]["json_body"] == {"inputs": {"options": {"max_candidates": 1}}}
    assert len(adapter.calls) == 4
    assert result.evidence["cost"] is None


def test_optimizer_success_does_not_cancel_or_apply():
    adapter = Adapter({"id": "job"}, {"status": "Completed"})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "pass" and len(adapter.calls) == 2
    assert result.evidence["applied"] is False and result.evidence["deployed"] is False
    assert result.evidence["cleanup_pending"] is False


def test_optimizer_poll_failure_still_cancels():
    adapter = Adapter({"id": "job"}, httpx.ConnectError("Bearer SECRET"), {})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "fail" and result.category == "network"
    assert result.evidence["cancellation"] == "requested_not_confirmed"
    assert "SECRET" not in str(result)


def test_optimizer_cancellation_failure_is_not_swallowed():
    adapter = Adapter({"id": "job"}, httpx.ConnectError("SECRET"), httpx.ConnectError("SECRET"))
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.category == "cleanup"
    assert result.evidence["cancellation"] == "failed"
    assert result.evidence["cleanup_category"] == "network"
    assert result.evidence["cleanup_pending"] is True
    assert "SECRET" not in str(result)


def test_optimizer_request_budget_reserves_cancellation():
    config = LiveConfig(
        project_endpoint=URL, allow_optimizer_submit=True,
        optimizer_request={"options": {"max_candidates": 1}},
        limits=Limits(max_requests=3), optimizer_poll_seconds=.001,
    )
    adapter = Adapter({"id": "job"}, {"status": "Running"}, {})
    result = live_probes(config, adapter=adapter)["optimizer.run"]()
    assert len(adapter.calls) == 3
    assert result.category == "budget"
    assert adapter.calls[-1][1].endswith(":cancel?api-version=v1")


class Credential:
    def get_token(self, scope):
        return SimpleNamespace(token="SECRET")


def test_rest_adapter_http_and_sse_are_bounded_and_authenticated():
    def handle(request):
        assert request.headers["Authorization"] == "Bearer SECRET"
        return httpx.Response(200, text=(
            'event: response.output_text.delta\r\ndata: {"type":"response.output_text.delta","delta":"OK"}\r\n\r\n'
            'data: {"type":"response.completed","response":{}}\n\ndata: [DONE]\n\n'
        ))
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        adapter = RestAdapter(credential=Credential(), client=http)
        result = adapter.request(
            "POST", MODEL_URL, scope="scope", json_body={}, headers={}, timeout_seconds=5,
            max_response_bytes=1024, stream=True,
        )
    assert len(result) == 2
    assert result[0]["delta"] == "OK"


@pytest.mark.parametrize(
    ("status", "category"), [(403, "authorization"), (429, "throttled"), (500, "service"), (302, "request")]
)
def test_rest_errors_redact_body_tokens_and_do_not_follow_redirects(status, category):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, headers={"Location": "https://attacker.example"}, text="SECRET")
    )) as http, pytest.raises(ObserveError) as caught:
        RestAdapter(credential=Credential(), client=http).request(
            "GET", URL, scope="scope", json_body=None, headers={}, timeout_seconds=5,
            max_response_bytes=1024,
        )
    assert caught.value.category == category and "SECRET" not in str(caught.value)


def test_rest_adapter_limits_response_bytes():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": "x" * 100})
    )) as http, pytest.raises(ObserveError) as caught:
        RestAdapter(credential=Credential(), client=http).request(
            "GET", URL, scope="scope", json_body=None, headers={}, timeout_seconds=5,
            max_response_bytes=10,
        )
    assert caught.value.category == "budget"


def test_credentials_failure_is_authentication_and_sanitized():
    class BrokenCredential:
        def get_token(self, scope):
            raise RuntimeError("Authorization SECRET")
    adapter = RestAdapter(credential=BrokenCredential())
    with pytest.raises(ObserveError) as caught:
        adapter.token("scope")
    assert caught.value.category == "authentication"
    assert "SECRET" not in str(caught.value)


def test_report_json_is_serializable_for_live_outcomes():
    report = report_for(["model.respond"], Adapter(text_response()))
    assert json.loads(report.to_json())["passed"] is True


@pytest.mark.parametrize("id_key", ["operation_id", "operationId", "id", "job_id", "jobId"])
def test_optimizer_recognizes_existing_client_job_id_aliases(id_key):
    adapter = Adapter({id_key: "job"}, {"status": "Completed"})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "pass" and result.evidence["job_id"] == "job"


def test_optimizer_missing_job_id_warns_about_untracked_remote_job():
    adapter = Adapter({})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "fail"
    assert result.evidence["cancellation"] == "unavailable_no_job_id"
    assert "remote work may exist" in result.diagnostic


def test_optimizer_unknown_status_is_failure_and_still_cancels():
    adapter = Adapter({"id": "job"}, {"status": "Bearer SECRET"}, {})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "fail" and result.category == "invalid_response"
    assert result.evidence["cancellation"] == "requested_not_confirmed"
    assert "SECRET" not in str(result)


def test_toolbox_is_error_inside_output_is_not_passing():
    adapter = Adapter({"output": [{"type": "mcp_call", "name": "web", "output": '{"isError":true}'}]})
    report = report_for(
        ["tools.toolbox"], adapter, toolbox_url=URL + "/toolboxes/known/mcp?api-version=v1",
        toolbox_tools=("web",), toolbox_prompt="Use web to read example.com.",
    )
    assert not report.passed
    assert selected(report, "tools.toolbox").evidence["tool_error_count"] == 1


def test_shared_wall_clock_timeout_does_not_report_late_success():
    clock = Clock()
    class SlowAdapter(Adapter):
        def request(self, *args, **kwargs):
            clock.sleep(5)
            return super().request(*args, **kwargs)
    config = LiveConfig(model_url=MODEL_URL, model="deployment", limits=Limits(max_seconds=1))
    probes = live_probes(config, adapter=SlowAdapter(text_response()), clock=clock, sleep=clock.sleep)
    report = run_suite(
        FunctionalSuite("daily", ("model.respond",)), probes=probes,
        prerequisites=config.prerequisites(),
    )
    assert selected(report, "model.respond").category == "timeout"
    assert not report.passed


@pytest.mark.parametrize("path", ["/fine_tuning/jobs", "/fine-tuning/jobs", "/fine%5ftuning/jobs"])
def test_finetuning_submission_cannot_be_enabled_by_endpoint_configuration(path):
    with pytest.raises(ValueError, match="never submit"):
        LiveConfig(hosted_invocations_url="https://example.com" + path)


@pytest.mark.parametrize(
    "payload",
    [
        {}, [], {"status": 7}, {"status": "SECRET"},
        {"status": "Succeeded", "operation_id": "wrong-job"},
        {"status": "Succeeded", "result": []},
        {"status": "Succeeded", "result": {"best": 7}},
        {"status": "Succeeded", "result": {"candidates": ["bad-shape"]}},
    ],
)
def test_optimizer_status_schema_and_identity_are_validated(payload):
    report = report_for(
        ["optimizer.status"], Adapter(payload), project_endpoint=URL,
        optimizer_job_id="known-job",
    )
    result = selected(report, "optimizer.status")
    assert result.status == "fail" and result.category == "invalid_response"
    assert "SECRET" not in report.to_json()


@pytest.mark.parametrize(
    "payload",
    [
        {}, [], {"unexpected": "SECRET"}, {"model": 1}, {"model": ""},
        {"system_prompt": []}, {"tools": "not-tools"}, {"skills": [7]},
        {"temperature": float("nan")}, {"temperature": True}, {"temperature": 5},
    ],
)
def test_optimizer_candidate_schema_is_validated(payload):
    report = report_for(
        ["optimizer.candidate"], Adapter(payload), project_endpoint=URL,
        optimizer_job_id="known-job", optimizer_candidate_id="known-candidate",
    )
    result = selected(report, "optimizer.candidate")
    assert result.status == "fail" and result.category == "invalid_response"
    assert "SECRET" not in report.to_json()


def test_optimizer_candidate_validates_known_configuration_fields_without_recording_content():
    report = report_for(
        ["optimizer.candidate"], Adapter({
            "model": "gpt-5-mini", "system_prompt": "SECRET",
            "tools": [{"type": "function", "function": {"name": "tool"}}],
            "skills": [], "temperature": 0,
        }), project_endpoint=URL, optimizer_job_id="known-job", optimizer_candidate_id="candidate",
    )
    assert report.passed and "SECRET" not in report.to_json()


def test_optimizer_cancellation_error_envelope_is_diagnostic_failure():
    adapter = Adapter({"id": "job"}, httpx.ConnectError("SECRET"), {"error": {"message": "SECRET"}})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "fail" and result.category == "cleanup"
    assert result.evidence["cancellation"] == "failed"
    assert result.evidence["cleanup_category"] == "invalid_response"
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_rest_adapter_rejects_nonfinite_or_invalid_timeout_before_auth(timeout):
    class NoCredential:
        def get_token(self, scope):
            raise AssertionError("must validate before acquiring credentials")
    with pytest.raises(ValueError):
        RestAdapter(credential=NoCredential()).request(
            "GET", URL, scope="scope", json_body=None, headers={}, timeout_seconds=timeout,
            max_response_bytes=1024,
        )


def test_hosted_task_body_and_expected_text_are_explicit_and_token_cap_cannot_be_overridden():
    adapter = Adapter(text_response("4"))
    report = report_for(
        ["runtime.responses"], adapter, hosted_responses_url="https://example.com/custom/responses",
        hosted_responses_body={"input": "What is 2+2?", "max_output_tokens": 999999},
        hosted_responses_expected_text="4",
    )
    assert report.passed
    assert adapter.calls[0][2]["json_body"] == {"input": "What is 2+2?", "max_output_tokens": 128}


def test_hosted_protocol_default_accepts_valid_output_without_assuming_agents_task():
    report = report_for(
        ["runtime.responses"], Adapter(text_response("4")),
        hosted_responses_url="https://example.com/custom/responses",
    )
    assert report.passed


def test_optimizer_confirmed_cancellation_does_not_turn_failure_into_success():
    adapter = Adapter({"id": "job"}, httpx.ConnectError("SECRET"), {"status": "Cancelled"})
    result = live_probes(optimizer_config(), adapter=adapter)["optimizer.run"]()
    assert result.status == "fail"
    assert result.evidence["cancellation"] == "confirmed_terminal"
    assert result.evidence["cleanup_pending"] is False
    assert result.evidence["remote_job_may_be_running"] is False


def test_failure_shaped_injected_probe_response_does_not_pass_by_truthiness():
    report = report_for(["model.respond"], Adapter({"status": "failed", "output_text": "CASTIA_OK"}))
    assert not report.passed

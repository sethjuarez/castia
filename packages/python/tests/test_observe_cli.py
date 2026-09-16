import argparse
import json
from pathlib import Path

import pytest

from castia.observe import ExecutionRecord, ObserveError, cli
from castia.observe.live import live_probes


def invoke(*arguments):
    parser = argparse.ArgumentParser()
    cli.register(parser.add_subparsers(required=True))
    args = parser.parse_args(["observe", *arguments])
    return args.func(args)


def files(monkeypatch, data):
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: json.dumps(data[str(self)]))


def test_registrar_features_has_all_inventory_and_no_network(capsys):
    assert invoke("features") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == 1
    assert any(feature["id"] == "teams.direct" for feature in output["features"])
    assert cli.register_commands is cli.register


def test_suite_init_and_check_are_explicitly_offline(monkeypatch, capsys):
    assert invoke("suite", "init", "--name", "daily") == 0
    suite = json.loads(capsys.readouterr().out)
    assert suite["name"] == "daily"
    files(monkeypatch, {"suite.json": suite})
    assert invoke("suite", "check", "--suite", "suite.json") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["check"] == "suite_schema"
    assert output["live_coverage_established"] is False


def test_suite_check_rejects_unknown_feature(monkeypatch, capsys):
    files(monkeypatch, {"suite.json": {"name": "daily", "features": ["bogus"]}})
    assert invoke("suite", "check", "--suite", "suite.json") == 2
    assert json.loads(capsys.readouterr().out)["category"] == "configuration"


def test_suite_dry_run_reports_missing_prerequisites_without_fake_coverage(monkeypatch, capsys):
    files(monkeypatch, {"suite.json": {"name": "daily", "features": ["teams.direct", "model.respond"]}})
    assert invoke("suite", "run", "--suite", "suite.json", "--dry-run") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["live"] is False and output["coverage_established"] is False
    assert output["coverage_plan"]["teams.direct"]["missing_prerequisites"] == ["teams_fixture"]
    assert "model_url" in output["coverage_plan"]["model.respond"]["missing_prerequisites"]


def test_suite_run_requires_explicit_live_opt_in(monkeypatch, capsys):
    files(monkeypatch, {"suite.json": {"name": "daily", "features": ["model.respond"]}})
    assert invoke("suite", "run", "--suite", "suite.json") == 2
    assert json.loads(capsys.readouterr().out)["category"] == "prerequisite"


def test_suite_live_with_unconfigured_features_returns_nonzero(monkeypatch, capsys):
    files(monkeypatch, {"suite.json": {"name": "daily", "features": ["teams.direct"]}})
    assert invoke("suite", "run", "--suite", "suite.json", "--live") == 1
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is False and output["counts"]["blocked"] == 1


def test_suite_live_uses_explicit_config_and_canonical_report(monkeypatch, capsys):
    class Adapter:
        def request(self, method, url, **kwargs):
            assert method == "POST" and url == "https://example.com/custom/responses"
            assert kwargs["json_body"]["max_output_tokens"] == 64
            return {"output_text": "CASTIA_OK"}
    monkeypatch.setattr(cli, "live_probes", lambda config: live_probes(config, adapter=Adapter()))
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["model.respond"]},
        "config.json": {
            "model": "deployment", "model_url": "https://example.com/custom/responses",
            "limits": {"max_output_tokens": 64, "max_total_output_tokens": 128},
        },
    })
    assert invoke("suite", "run", "--suite", "suite.json", "--config", "config.json", "--live") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["passed"] is True and "CASTIA_OK" not in str(output)


def test_saved_config_does_not_silently_authorize_optimizer_submission(monkeypatch, capsys):
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["optimizer.run"]},
        "config.json": {
            "allow_optimizer_submit": True,
            "project_endpoint": "https://example.com/projects/project",
            "optimizer_request": {"options": {"max_candidates": 1}},
        },
    })
    assert invoke("suite", "run", "--suite", "suite.json", "--config", "config.json", "--dry-run") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["optimizer_submission_enabled"] is False
    assert "allow_optimizer_submit" in output["coverage_plan"]["optimizer.run"]["missing_prerequisites"]


def trace_args():
    return [
        "--agent", "agent", "--app-id", "app-id",
        "--start", "2026-09-15T00:00:00Z", "--end", "2026-09-16T00:00:00Z",
    ]


def test_trace_dry_run_has_exact_query_scope_and_content_opt_in(capsys):
    assert invoke("traces", *trace_args(), "--dry-run", "--limit", "9") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["live"] is False and output["content_included"] is False
    assert output["query"].endswith("| take 9")
    assert "gen_ai.prompt" not in output["query"]


def test_trace_command_uses_adapter_and_excludes_content(monkeypatch, capsys):
    class Client:
        def __init__(self, app_id, **kwargs):
            assert app_id == "app-id"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def query(self, query):
            assert query.include_content is False
            return [ExecutionRecord(agent_name=query.agent_name, status="success")]
    monkeypatch.setattr(cli, "AppInsightsClient", Client)
    assert invoke("traces", *trace_args()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["records"][0]["agent_name"] == "agent"
    assert "content" not in output["records"][0]


def test_query_error_is_categorized_without_raw_auth(monkeypatch, capsys):
    class Client:
        def __init__(self, *args, **kwargs):
            raise ObserveError("authorization", "Service returned HTTP 403.")
    monkeypatch.setattr(cli, "AppInsightsClient", Client)
    assert invoke("traces", *trace_args()) == 2
    assert json.loads(capsys.readouterr().out)["category"] == "authorization"


def test_verify_requires_tag_at_parse_time():
    with pytest.raises(SystemExit) as caught:
        invoke("verify", *trace_args())
    assert caught.value.code == 2


def test_summarize_is_offline_and_preserves_unknowns(monkeypatch, capsys):
    files(monkeypatch, {"records.json": {"records": [{"status": "unknown"}]}})
    assert invoke("summarize", "--records", "records.json") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["error_rate"] is None and output["cost"] is None


def test_canonical_output_file_uses_requested_path(monkeypatch):
    written = {}
    monkeypatch.setattr(Path, "write_text", lambda self, text, **kwargs: written.update({str(self): text}))
    assert invoke("suite", "init", "--out", "daily.json") == 0
    text = written["daily.json"]
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=2) + "\n"


@pytest.mark.parametrize("limit", ["0", "-1", "10001"])
def test_invalid_trace_row_bounds_fail_before_network(limit, capsys):
    assert invoke("traces", *trace_args(), "--limit", limit, "--dry-run") == 2
    assert json.loads(capsys.readouterr().out)["category"] == "configuration"


def test_cli_never_offers_finetuning_submission(capsys):
    with pytest.raises(SystemExit):
        invoke("suite", "run", "--help")
    assert "finetune" not in capsys.readouterr().out


def test_requested_top_level_commands_are_registered(capsys):
    with pytest.raises(SystemExit):
        invoke("--help")
    help_text = capsys.readouterr().out
    for command in ("traces", "summary", "verify", "drift", "compare", "catalog"):
        assert command in help_text


def test_catalog_and_summary_names(monkeypatch, capsys):
    assert invoke("catalog") == 0
    assert json.loads(capsys.readouterr().out)["features"]
    files(monkeypatch, {"records.json": []})
    assert invoke("summary", "--records", "records.json") == 0
    assert json.loads(capsys.readouterr().out)["record_count"] == 0


def test_drift_requires_explicit_live_authorization(monkeypatch, capsys):
    files(monkeypatch, {"suite.json": {"name": "daily", "features": ["teams.direct"]}})
    assert invoke("drift", "--suite", "suite.json") == 2
    assert json.loads(capsys.readouterr().out)["category"] == "prerequisite"
    assert invoke("drift", "--suite", "suite.json", "--dry-run") == 0
    assert json.loads(capsys.readouterr().out)["live"] is False
    assert invoke("drift", "--suite", "suite.json", "--live") == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


def report_rows(*rows):
    return {
        "schema_version": 1,
        "results": [
            {"feature_id": identifier, "selected": True, "status": status}
            for identifier, status in rows
        ],
    }


@pytest.mark.parametrize("status", ["fail", "blocked", "uncovered"])
def test_compare_never_passes_nonpassing_selected_coverage(monkeypatch, capsys, status):
    files(monkeypatch, {
        "baseline.json": report_rows(("model.respond", status)),
        "current.json": report_rows(("model.respond", status)),
    })
    assert invoke("compare", "--baseline", "baseline.json", "--candidate", "current.json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False and data["status"] == status


def test_compare_reports_regressions_and_lost_coverage(monkeypatch, capsys):
    files(monkeypatch, {
        "baseline.json": report_rows(("model.respond", "pass"), ("model.stream", "pass")),
        "current.json": report_rows(("model.respond", "pass")),
    })
    assert invoke("compare", "--baseline", "baseline.json", "--current", "current.json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "fail"
    assert data["comparison"]["lost_coverage"] == ["model.stream"]


def test_compare_deterministic_json_and_success(monkeypatch, capsys):
    files(monkeypatch, {
        "baseline.json": report_rows(("model.respond", "pass")),
        "current.json": report_rows(("model.respond", "pass")),
    })
    assert invoke("compare", "--baseline", "baseline.json", "--candidate", "current.json") == 0
    first = capsys.readouterr().out
    assert invoke("compare", "--baseline", "baseline.json", "--candidate", "current.json") == 0
    assert capsys.readouterr().out == first
    assert json.loads(first)["passed"] is True


def test_compare_no_selected_features_is_uncovered(monkeypatch, capsys):
    files(monkeypatch, {"baseline.json": report_rows(), "current.json": report_rows()})
    assert invoke("compare", "--baseline", "baseline.json", "--candidate", "current.json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "uncovered"


def test_drift_lost_coverage_is_nonzero_even_when_remaining_probe_passes(monkeypatch, capsys):
    class Adapter:
        def request(self, method, url, **kwargs):
            return {"output_text": "CASTIA_OK"}
    monkeypatch.setattr(cli, "live_probes", lambda config: live_probes(config, adapter=Adapter()))
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["model.respond"]},
        "config.json": {"model": "deployment", "model_url": "https://example.com/responses"},
        "baseline.json": report_rows(("model.respond", "pass"), ("model.stream", "pass")),
    })
    assert invoke(
        "drift", "--suite", "suite.json", "--config", "config.json",
        "--baseline", "baseline.json", "--live",
    ) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["passed"] is False
    assert data["comparison"]["lost_coverage"] == ["model.stream"]


def test_cli_never_inherits_unrelated_environment_project_or_model(monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://unrelated.example/projects/other")
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://another.example/projects/other")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "unselected-model")
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["optimizer.status", "model.respond"]},
        "config.json": {"optimizer_job_id": "known-job"},
    })
    assert invoke("drift", "--suite", "suite.json", "--config", "config.json", "--dry-run") == 0
    data = json.loads(capsys.readouterr().out)
    assert "project_endpoint" in data["coverage_plan"]["optimizer.status"]["missing_prerequisites"]
    assert "model" in data["coverage_plan"]["model.respond"]["missing_prerequisites"]
    assert "unrelated" not in str(data) and "unselected-model" not in str(data)


def test_cli_selected_endpoint_wins_over_both_ambient_project_variables(monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://unrelated.example/projects/other")
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://another.example/projects/other")
    class Adapter:
        def request(self, method, url, **kwargs):
            assert url.startswith("https://selected.example/projects/selected/")
            assert kwargs["timeout_seconds"] > 0
            return {"status": "Succeeded"}
    monkeypatch.setattr(cli, "live_probes", lambda config: live_probes(config, adapter=Adapter()))
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["optimizer.status"]},
        "config.json": {
            "project_endpoint": "https://selected.example/projects/selected",
            "optimizer_job_id": "known-job",
        },
    })
    assert invoke("drift", "--suite", "suite.json", "--config", "config.json", "--live") == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_deleted_baseline_is_blocked_before_any_live_probe(monkeypatch, capsys):
    def read(path, **kwargs):
        if str(path) == "deleted.json":
            raise FileNotFoundError("deleted baseline")
        return json.dumps({"name": "daily", "features": ["model.respond"]})
    monkeypatch.setattr(Path, "read_text", read)
    def no_probes(config):
        raise AssertionError("must not construct live probes with a missing baseline")
    monkeypatch.setattr(cli, "live_probes", no_probes)
    assert invoke("drift", "--suite", "suite.json", "--baseline", "deleted.json", "--live") == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "blocked" and data["category"] == "configuration"


def test_project_read_only_current_cannot_drop_passing_model_baseline_coverage(monkeypatch, capsys):
    class Adapter:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            return {"id": "selected-project"}
    monkeypatch.setattr(cli, "live_probes", lambda config: live_probes(config, adapter=Adapter()))
    files(monkeypatch, {
        "suite.json": {"name": "daily", "features": ["project.read"]},
        "config.json": {"project_read_url": "https://selected.example/project"},
        "baseline.json": report_rows(("project.read", "pass"), ("model.respond", "pass")),
    })
    exit_code = invoke(
        "drift", "--suite", "suite.json", "--config", "config.json",
        "--baseline", "baseline.json", "--live",
    )
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["pass"] == 1
    assert report["comparison"]["regressions"] == ["model.respond"]
    assert report["comparison"]["lost_coverage"] == ["model.respond"]
    assert report["passed"] is False and exit_code == 1

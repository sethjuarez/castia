"""Scheduled evaluation rules and windowed trace runs: spec vectors plus CLI wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia.__main__ import main
from castia.evaluation import schedule
from castia.lifecycle.output import CLI_SCHEMA

VECTORS = (
    Path(__file__).resolve().parents[3]
    / "spec" / "generated" / ".typra-generated" / "vectors.json"
)
_BUILDERS = {
    "parseWindow": lambda i: schedule.parse_window(i["text"]),
    "traceWindowSource": lambda i: schedule.trace_window_source(**i["request"]),
    "scheduleTrigger": lambda i: schedule.schedule_trigger(**i["request"]),
    "evaluationRule": lambda i: schedule.evaluation_rule(**i["request"]),
}


def _vectors() -> list[tuple[str, dict]]:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    return [
        (v["operation"], v["vector"]) for v in data["vectors"]
        if v["contract"] == "EvaluationScheduleRuntime"
    ]


@pytest.mark.parametrize(("operation", "vector"), _vectors(),
                         ids=lambda x: x["name"] if isinstance(x, dict) else x)
def test_builders_match_spec_vectors(operation, vector):
    build = _BUILDERS[operation]
    if "expectedError" in vector:
        with pytest.raises(ValueError, match=vector["expectedError"]["message"]):
            build(vector["input"])
    else:
        assert build(vector["input"]) == vector["expected"]


def test_every_operation_has_vectors():
    assert {op for op, _ in _vectors()} == set(_BUILDERS)


def test_payloads_round_trip_through_sdk_models():
    models = pytest.importorskip("azure.ai.projects.models")
    rule = schedule.evaluation_rule(
        eval_id="eval_1", agent_name="support", source_kind="traces",
        trigger=schedule.schedule_trigger(daily_hours=[9], time_zone="UTC"), max_traces=5,
    )
    assert models.EvaluationRule(rule).as_dict() == rule
    source = schedule.trace_window_source(agent_name="support", window_seconds=60, end_time=120)
    assert models.TraceSourceEvalRunDataSource(source).as_dict() == source


def _envelope(capsys) -> dict:
    env = json.loads(capsys.readouterr().out)
    assert env["schema"] == CLI_SCHEMA
    return env


def test_schedule_create_dry_run_json_is_offline(capsys, monkeypatch):
    monkeypatch.setattr(schedule, "_project_client", _no_network)
    rc = main(["eval", "schedule", "create", "nightly", "--eval-id", "eval_1",
               "--agent", "support", "--traces", "--daily-at", "21,9", "--dry-run", "--json"])
    env = _envelope(capsys)
    assert rc == 0
    assert env["command"] == "eval schedule create"
    assert env["status"] == "dry_run"
    assert env["effects"] == {"network": False, "billable": False, "mutating": False}
    rule = env["result"]["rule"]
    assert env["result"]["rule_id"] == "nightly"
    assert rule["action"]["type"] == "traceEvaluation"
    assert rule["schedule"]["schedule"] == {"type": "Daily", "hours": [9, 21]}


def test_schedule_create_live_calls_create_or_update(capsys, monkeypatch):
    calls = []

    class Rules:
        def create_or_update(self, rule_id, body):
            calls.append((rule_id, body))
            return body

    monkeypatch.setattr(schedule, "_project_client", lambda args: _FakeClient(Rules()))
    rc = main(["eval", "schedule", "create", "weekly", "--eval-id", "eval_1",
               "--agent", "support", "--dataset-file-id", "file-1", "--cron", "0 9 * * 1",
               "--json"])
    env = _envelope(capsys)
    assert rc == 0
    assert env["status"] == "created"
    assert env["effects"] == {"network": True, "billable": True, "mutating": True}
    assert calls[0][0] == "weekly"
    assert calls[0][1]["action"]["source"]["source"] == {"type": "file_id", "id": "file-1"}


def test_eval_traces_dry_run_uses_window(capsys, monkeypatch):
    monkeypatch.setattr(schedule.time, "time", lambda: 1_700_003_600)
    rc = main(["eval", "traces", "--eval-id", "eval_1", "--agent", "support",
               "--window", "1h", "--max-traces", "10", "--dry-run", "--json"])
    env = _envelope(capsys)
    assert rc == 0
    trace_source = env["result"]["data_source"]["trace_source"]
    assert trace_source["start_time"] == 1_700_000_000
    assert trace_source["end_time"] == 1_700_003_600
    assert trace_source["max_traces"] == 10


def test_eval_traces_bad_window_is_error_envelope(capsys):
    rc = main(["eval", "traces", "--eval-id", "eval_1", "--agent", "support",
               "--window", "soon", "--json"])
    env = _envelope(capsys)
    assert rc == 2
    assert env["ok"] is False
    assert env["diagnostics"][0]["message"] == "window must look like 30m, 24h, or 7d"


class _FakeClient:
    def __init__(self, rules):
        self.evaluation_rules = rules

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _no_network(args):
    raise AssertionError("dry-run must not create a project client")

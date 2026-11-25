from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia.__main__ import main
from castia.optimizing import preflight as pf
from castia.optimizing.jobs import OptimizerClient

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "spec/conformance/optimization/preflight.json")
    .read_text(encoding="utf-8")
)


def test_constants_match_spec():
    c = FIXTURE["constants"]
    assert list(pf.PASSTHROUGH_FIELDS) == c["passthrough_fields"]
    assert {k: list(v) for k, v in pf.PASSTHROUGH_BY_TYPE.items()} == c["passthrough_by_type"]
    assert list(pf.KNOWN_DROPPED_FIELDS) == c["known_dropped_fields"]
    assert list(pf.TURN_LEVEL_EVALUATOR_TYPES) == c["turn_level_evaluator_types"]
    assert pf.PILOT_MAX_ROWS == c["pilot_max_rows"]
    assert pf.PILOT_MAX_CANDIDATES == c["pilot_max_candidates"]
    assert pf.APPROVAL_HOURS == c["approval_hours"]
    assert pf.SERVICE_CAP_HOURS == c["service_cap_hours"]
    assert pf.DEFAULT_ROW_SECONDS == c["default_row_seconds"]
    assert pf.ASSUMED_CANDIDATES == c["assumed_candidates"]


@pytest.mark.parametrize("case", FIXTURE["item_references"])
def test_item_references(case):
    assert pf.item_references(case["value"]) == case["expected"]


def _matches(problems: list[str], expected: list[str]) -> None:
    assert len(problems) == len(expected), problems
    for problem, prefix in zip(problems, expected):
        assert problem.startswith(prefix), (problem, prefix)


@pytest.mark.parametrize("case", FIXTURE["evaluator_level"], ids=lambda c: c["name"])
def test_evaluator_level(case):
    _matches(
        pf.evaluator_level_problems(case["evaluators"], case["options"], case.get("definitions")),
        case["expected"],
    )


@pytest.mark.parametrize("case", FIXTURE["item_fields"], ids=lambda c: c["name"])
def test_item_fields(case):
    _matches(
        pf.item_field_problems(
            case["evaluators"],
            case["rows"],
            passthrough=case.get("passthrough"),
            definitions=case.get("definitions"),
        ),
        case["expected"],
    )


@pytest.mark.parametrize("case", FIXTURE["estimate"], ids=lambda c: c["name"])
def test_estimate(case):
    i = case["input"]
    est = pf.estimate_optimize(
        rows=i["rows"],
        max_candidates=i["max_candidates"],
        row_seconds=i.get("row_seconds", pf.DEFAULT_ROW_SECONDS),
        concurrency=i.get("concurrency"),
        hourly_usd=i.get("hourly_usd"),
    )
    e = case["expected"]
    assert est.hours == e["hours"]
    assert est.needs_approval is e["needs_approval"]
    assert est.candidates_assumed is e["candidates_assumed"]
    if "cost_usd" in e:
        assert est.cost_usd == e["cost_usd"]


def _setup(tmp_path: Path, *, evaluators: str, rows: int = 1, options: str = "") -> Path:
    cand = tmp_path / ".agent_configs" / "baseline"
    cand.mkdir(parents=True)
    (cand / "metadata.yaml").write_text("model: gpt-4o\n", encoding="utf-8")
    (tmp_path / "data.jsonl").write_text(
        "".join(json.dumps({"query": f"q{i}", "ground_truth": "a"}) + "\n" for i in range(rows)),
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    config.write_text(
        "agent:\n  name: opt\n"
        f"evaluators:\n{evaluators}"
        "dataset:\n  local_uri: data.jsonl\n"
        "options:\n  eval_model: gpt-4o\n  optimization_model: gpt-5-mini\n" + options,
        encoding="utf-8",
    )
    return config


def _run(config: Path, tmp_path: Path, *extra: str) -> int:
    return main(
        ["optimize", "run", "--config", str(config),
         "--config-dir", str(tmp_path / ".agent_configs"), *extra]
    )


def test_run_blocks_dropped_item_field(tmp_path, capsys):
    config = _setup(
        tmp_path,
        evaluators="  - name: gold\n    initialization_parameters:\n"
        "      data_mapping:\n        ground_truth: '{{item.ground_truth}}'\n",
    )
    rc = _run(config, tmp_path, "--dry-run")
    err = capsys.readouterr().err
    assert rc == 1
    assert "item.ground_truth scores against nothing" in err
    assert "BLOCKED BY PREFLIGHT" in err


def test_run_passthrough_override_unblocks(tmp_path, capsys):
    config = _setup(
        tmp_path,
        evaluators="  - name: gold\n    initialization_parameters:\n"
        "      m: '{{item.ground_truth}}'\n",
    )
    rc = _run(config, tmp_path, "--dry-run", "--passthrough-field", "query",
              "--passthrough-field", "ground_truth")
    assert rc == 0
    assert "estimate      : 1 rows" in capsys.readouterr().out


def test_run_blocks_turn_level_definition(tmp_path, capsys):
    config = _setup(tmp_path, evaluators="  - name: exact\n")
    definition = tmp_path / "exact.json"
    definition.write_text(json.dumps({"name": "exact", "type": "string_check"}), encoding="utf-8")
    rc = _run(config, tmp_path, "--dry-run", "--evaluator-definition", str(definition))
    assert rc == 1
    assert "type string_check runs per turn" in capsys.readouterr().err


def test_run_large_job_needs_approval(tmp_path, capsys, monkeypatch):
    config = _setup(tmp_path, evaluators="  - builtin.task_adherence\n", rows=30,
                    options="  max_candidates: 50\n")

    def fail_submit(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("submitted without approval")

    monkeypatch.setattr(OptimizerClient, "start", fail_submit)
    rc = _run(config, tmp_path, "--project-endpoint", "https://x/api/projects/p")
    err = capsys.readouterr().err
    assert rc == 1
    assert "NEEDS APPROVAL" in err
    assert "30 rows > pilot 20" in err
    assert "50 candidates > pilot 10" in err


def test_run_approved_large_job_submits(tmp_path, capsys, monkeypatch):
    config = _setup(tmp_path, evaluators="  - builtin.task_adherence\n", rows=30)

    class FakeClient:
        def __init__(self, endpoint):
            pass

        def start(self, request):
            return {"id": "opt_9", "status": "queued"}

    monkeypatch.setattr("castia.optimizing.jobs.OptimizerClient", FakeClient)
    rc = _run(config, tmp_path, "--project-endpoint", "https://x/api/projects/p",
              "--approve-run", "--no-wait")
    assert rc == 0
    assert "job           : opt_9" in capsys.readouterr().out

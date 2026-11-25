from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from castia.__main__ import main
from castia.finetuning import probe
from castia.finetuning.rft import string_check_grader

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "spec" / "conformance" / "finetuning"
     / "probe.json").read_text(encoding="utf-8")
)


def test_constants_match_spec():
    t = FIXTURE["thresholds"]
    assert (probe.PROBE_ROWS, probe.PASS_FLOOR, probe.FAIL_CEILING, probe.DEFAULT_TOLERANCE) == (
        t["probe_rows"], t["pass_floor"], t["fail_ceiling"], t["default_tolerance"],
    )
    assert probe.probe_graders() == FIXTURE["probe_graders"]


@pytest.mark.parametrize("case", FIXTURE["render"], ids=lambda c: c["template"])
def test_render_template(case):
    assert probe.render_template(case["template"], case["sample"], case["item"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["grade"], ids=lambda c: c["name"])
def test_grade_local_cases(case):
    score = probe.grade_local(case["grader"], probe.sample_for(case["output"]), case["item"])
    assert score == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["reward_from_events"])
def test_reward_from_events(case):
    assert probe.reward_from_events(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["verdict"])
def test_probe_verdict(case):
    data = case["input"]
    verdict = probe.probe_verdict(data["rewards"], offline_score=data.get("offline_score"))
    assert verdict["status"] == case["expected"]


def test_probe_graders_score_in_process():
    for name, expected in (("always_true", 1.0), ("always_false", 0.0)):
        assert probe.grade_local(probe.probe_graders()[name], probe.sample_for("x"), {}) == expected


def test_grade_rows_reports_python_errors_and_json_sample():
    grader = {"type": "python", "name": "g", "source": (
        "def grade(sample, item):\n"
        "    return float(sample['output_json']['n'] == item['n'])\n"
    )}
    report = probe.grade_rows(grader, [{"n": 1}, {"n": 2}, {"n": 3}],
                              ['{"n": 1}', '{"n": 0}', "not json"])
    assert report["scores"][:2] == [1.0, 0.0]
    assert report["errors"][0]["row"] == 2 and "TypeError" in report["errors"][0]["error"]
    assert report["mean"] == 0.5


def _rows(tmp_path, n=3):
    row = {"messages": [{"role": "user", "content": "2+2?"}], "answer": "4"}
    train, val = tmp_path / "train.jsonl", tmp_path / "val.jsonl"
    for path in (train, val):
        path.write_text((json.dumps(row) + "\n") * n, encoding="utf-8")
    grader = tmp_path / "grader.json"
    grader.write_text(json.dumps(string_check_grader(
        "exact", input="{{ sample.output_text }}", reference="{{ item.answer }}",
    )), encoding="utf-8")
    return train, val, grader


def test_cli_grade_local_with_output_field(tmp_path, capsys):
    train, _, grader = _rows(tmp_path)
    out = tmp_path / "report.json"
    assert main([
        "finetune", "grade-local", "--grader", str(grader), "--dataset", str(train),
        "--output-field", "answer", "--out", str(out),
    ]) == 0
    assert "mean 1, min 1, max 1" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["scored"] == 3


def test_cli_probe_plans_by_default_then_submits_with_approval(tmp_path, monkeypatch, capsys):
    train, val, grader = _rows(tmp_path)
    record = tmp_path / "probe.json"
    args = [
        "finetune", "probe", "--model", "o4-mini", "--dataset", str(train),
        "--validation", str(val), "--grader", str(grader), "--output-field", "answer",
        "--out", str(record),
    ]
    calls = []
    monkeypatch.setattr("castia.finetuning.rft.upload_file",
                        lambda path, *, endpoint=None: calls.append(path) or f"file-{len(calls)}")
    monkeypatch.setattr(
        "castia.finetuning.rft.submit_rft_job",
        lambda job, *, endpoint=None: SimpleNamespace(id=f"ftjob-{job['suffix']}"),
    )
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "total spend   : <=$" in out and "plan only" in out
    assert calls == []
    planned = json.loads(record.read_text(encoding="utf-8"))
    assert planned["verdict"]["status"] == "planned"
    assert planned["offline_score"] == 1.0
    assert set(planned["jobs"]) == {"always_true", "always_false", "real"}

    assert main([*args, "--approve-spend"]) == 0
    submitted = json.loads(record.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert submitted["jobs"]["always_true"]["job_id"] == "ftjob-probe-always-true"
    assert submitted["verdict"]["status"] == "submitted"


def test_cli_probe_check_and_submit_gate(tmp_path, monkeypatch, capsys):
    train, val, grader = _rows(tmp_path, n=10)
    record = tmp_path / "probe.json"
    rewards = {"ftjob-t": 1.0, "ftjob-f": 0.0, "ftjob-r": 0.0}
    record.write_text(json.dumps({
        "offline_score": 1.0, "tolerance": 0.15,
        "jobs": {"always_true": {"job_id": "ftjob-t"}, "always_false": {"job_id": "ftjob-f"},
                 "real": {"job_id": "ftjob-r"}},
    }), encoding="utf-8")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def events(self, job_id, *, limit):
            return [{"type": "metrics", "data": {"train_reward_mean": rewards[job_id]}}]

    monkeypatch.setattr("castia.finetuning.jobs.FineTuningClient", FakeClient)
    assert main(["finetune", "probe-check", "--record", str(record)]) == 1
    assert "diverges from offline score" in capsys.readouterr().err

    submit = [
        "finetune", "submit", "--type", "rft", "--model", "o4-mini",
        "--dataset", str(train), "--validation", str(val), "--grader", str(grader),
        "--probe-record", str(record), "--approve-train",
    ]
    assert main(submit) == 1
    assert "BLOCKED BY PROBE" in capsys.readouterr().err

    rewards["ftjob-r"] = 0.95
    assert main(["finetune", "probe-check", "--record", str(record)]) == 0
    assert main([*submit[:-1], "--dry-run"]) == 0
    assert "probe         : pass" in capsys.readouterr().out

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from castia.finetuning import FineTuningClient, preflight
from castia.finetuning.rft import string_check_grader

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "spec" / "conformance" / "finetuning"
     / "preflight.json").read_text(encoding="utf-8")
)


def test_cost_table_and_constants_match_spec():
    assert preflight.RFT_COST_TABLE == FIXTURE["cost_table"]
    assert preflight.DEFAULT_SECONDS_PER_STEP == FIXTURE["default_seconds_per_step"]
    assert list(preflight.APPROVAL_STAGES) == FIXTURE["approval_stages"]


@pytest.mark.parametrize("case", FIXTURE["batch_size"], ids=lambda c: repr(c["input"]))
def test_normalize_batch_size(case):
    if "error" in case:
        with pytest.raises(ValueError, match=case["error"].replace("[", r"\[")):
            preflight.normalize_batch_size(case["input"])
    else:
        assert preflight.normalize_batch_size(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["estimate"])
def test_estimate_training(case):
    data = dict(case["input"])
    rows = data.pop("rows")
    if "error" in case:
        with pytest.raises(ValueError, match=case["error"]):
            preflight.estimate_training(rows, **data)
    else:
        assert preflight.estimate_training(rows, **data) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["training_started_at"])
def test_training_started_at(case):
    assert preflight.training_started_at(case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["timing"])
def test_split_job_timing(case):
    assert preflight.split_job_timing(**case["input"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["delete"])
def test_delete_guard(case):
    decision = preflight.delete_guard(**case["input"])
    assert {k: decision[k] for k in case["expected"]} == case["expected"]


def test_approvals_are_independent():
    for case in FIXTURE["approvals"]:
        assert preflight.approvals(**case["input"]) == case["expected"]


def test_spend_line_marks_upper_bound_and_assumptions():
    line = preflight.spend_line(preflight.estimate_training(12))
    assert line.startswith("<=12 steps")
    assert "$8.00" in line and "assumed" in line


def _rft_files(tmp_path, rows=10):
    grader = string_check_grader(
        "exact", input="{{ sample.output_text }}", reference="{{ item.answer }}",
    )
    row = {"messages": [{"role": "user", "content": "2+2?"}], "answer": "4"}
    paths = []
    for name in ("train", "val"):
        path = tmp_path / f"{name}.jsonl"
        path.write_text((json.dumps(row) + "\n") * rows, encoding="utf-8")
        paths.append(path)
    grader_path = tmp_path / "grader.json"
    grader_path.write_text(json.dumps(grader), encoding="utf-8")
    return [
        "finetune", "submit", "--type", "rft", "--model", "o4-mini",
        "--dataset", str(paths[0]), "--validation", str(paths[1]),
        "--grader", str(grader_path),
    ]


def test_cli_submit_drops_auto_batch_and_prints_estimate(tmp_path, capsys):
    from castia.__main__ import main

    args = _rft_files(tmp_path)
    assert main([*args, "--batch-size", "-1", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"batch_size"' not in out
    assert "estimate      : <=10 steps" in out
    assert main([*args, "--batch-size", "0", "--dry-run"]) == 2
    assert "batch_size must be an integer" in capsys.readouterr().err


def test_cli_live_submit_requires_train_approval(tmp_path, monkeypatch, capsys):
    from castia.__main__ import main

    def fail_upload(*_a, **_k):
        raise AssertionError("must not upload without --approve-train")

    monkeypatch.setattr("castia.finetuning.rft.upload_file", fail_upload)
    args = _rft_files(tmp_path)
    assert main([*args, "--batch-size", "2", "--seconds-per-step", "30"]) == 2
    captured = capsys.readouterr()
    assert "estimate      : ~5 steps" in captured.out
    assert "--approve-train" in captured.err

    monkeypatch.setattr("castia.finetuning.rft.upload_file", lambda *_a, **_k: "file-1")
    monkeypatch.setattr(
        "castia.finetuning.rft.submit_rft_job", lambda job, *, endpoint=None: "ftjob-1",
    )
    assert main([*args, "--approve-train"]) == 0
    assert "approve promotion separately" in capsys.readouterr().out


def _client(job):
    transport = MagicMock()
    transport.fine_tuning.jobs.retrieve.return_value = job
    return FineTuningClient(client=transport), transport


def test_delete_refuses_model_job_without_confirmation():
    job = SimpleNamespace(id="job-1", fine_tuned_model="ft-model", status="succeeded")
    client, transport = _client(job)
    client.deployments = lambda: [{"name": "prod", "model": "ft-model"}]
    with pytest.raises(PermissionError, match="prod"):
        client.delete("job-1", yes=True)
    transport.delete.assert_not_called()
    decision = client.delete("job-1", confirm_model="ft-model")
    assert decision["deleted"] and decision["deployments"] == ["prod"]
    transport.delete.assert_called_once()


def test_status_timing_splits_queue_from_training():
    job = SimpleNamespace(id="job-1", created_at=1000, finished_at=None)
    client, transport = _client(job)
    transport.fine_tuning.jobs.list_events.return_value = SimpleNamespace(data=[
        SimpleNamespace(created_at=8200, type="message", message="Training started"),
    ])
    assert client.timing(job, now=9000) == {
        "phase": "training", "queue_seconds": 7200, "training_seconds": 800,
    }


def test_handoff_records_separate_approvals():
    job = SimpleNamespace(
        id="job-1", status="succeeded", fine_tuned_model="ft", result_files=[],
    )
    client, _ = _client(job)
    assert client.deployment_handoff("job-1")["approvals"] == {
        "train": "approved", "evaluate": "required", "promote": "required",
    }


GRADER_RULES = json.loads(
    (Path(__file__).resolve().parents[3] / "spec" / "conformance" / "finetuning"
     / "python_grader_rules.json").read_text(encoding="utf-8")
)


def test_denied_grader_modules_match_spec():
    spec = GRADER_RULES["denied_modules"]
    assert set(preflight.DENIED_GRADER_MODULES) == set(spec)
    for name, entry in spec.items():
        assert preflight.DENIED_GRADER_MODULES[name] == entry
    assert preflight.MIN_RFT_TRAINING_ROWS == GRADER_RULES["min_training_rows"]["rft"]


@pytest.mark.parametrize("case", GRADER_RULES["cases"], ids=lambda c: c["name"])
def test_python_grader_denied_imports(case):
    assert preflight.python_grader_denied_imports(case["source"]) == case["denied"]


def test_validate_grader_rejects_urllib_parse_with_fix(tmp_path):
    from castia.finetuning.rft import validate_grader

    grader = {
        "type": "python", "name": "g", "image_tag": "2025-05-08",
        "source": "from urllib.parse import urlsplit\n\ndef grade(sample, item):\n    return 1.0\n",
    }
    problems = validate_grader(grader)
    assert any("urllib.parse" in p and "str methods or re" in p for p in problems)
    nested = {"type": "multi", "name": "m", "graders": {"a": grader}, "calculate_output": "a"}
    assert any("urllib.parse" in p for p in validate_grader(nested))


def test_min_rows_problem():
    assert preflight.min_rows_problem("rft", 9).startswith("training: 9 example(s)")
    assert preflight.min_rows_problem("rft", 10) is None
    assert preflight.min_rows_problem("sft", 1) is None


def test_cli_check_blocks_denied_grader_import(tmp_path, capsys):
    from castia.__main__ import main

    row = {"messages": [{"role": "user", "content": "2+2?"}], "answer": "4"}
    train, val = tmp_path / "train.jsonl", tmp_path / "val.jsonl"
    for path in (train, val):
        path.write_text((json.dumps(row) + "\n") * 10, encoding="utf-8")
    grader = tmp_path / "grader.json"
    grader.write_text(json.dumps({
        "type": "python", "name": "g", "image_tag": "2025-05-08",
        "source": "import urllib.parse\n\ndef grade(sample, item):\n    return 1.0\n",
    }), encoding="utf-8")
    assert main([
        "finetune", "check", "--dataset", str(train), "--validation", str(val),
        "--grader", str(grader),
    ]) == 1
    captured = capsys.readouterr()
    assert "urllib.parse" in captured.out + captured.err

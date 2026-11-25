from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia.__main__ import main
from castia.optimizing import verify as vf
from castia.optimizing import verify_cli
from castia.optimizing.cli import _print_optimizer_status

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "spec/conformance/optimization/verify.json")
    .read_text(encoding="utf-8")
)


def test_constants_match_spec():
    c = FIXTURE["constants"]
    assert vf.LEAK_SHINGLE_WORDS == c["leak_shingle_words"]
    assert vf.LEAK_MIN_WORDS == c["leak_min_words"]
    assert list(vf.STALL_MARKERS) == c["stall_markers"]
    assert vf.STOPPED_EARLY_LABEL == c["stopped_early_label"]


@pytest.mark.parametrize("case", FIXTURE["training_leaks"], ids=lambda c: c["name"])
def test_training_leaks(case):
    got = vf.training_leaks(case["candidate"], case["rows"], baseline_text=case["baseline"])
    assert got == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["heldout_overlap"], ids=lambda c: c["name"])
def test_heldout_overlap(case):
    assert vf.heldout_overlap(case["training"], case["heldout"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["metric_values"], ids=lambda c: c["name"])
def test_metric_values(case):
    assert vf.metric_values(case["rows"], case["metric"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["verdict"], ids=lambda c: c["name"])
def test_verdict(case):
    got = vf.verify_verdict(
        case["baseline"], case["candidate"],
        min_delta=case["min_delta"], leaks=case["leaks"], overlap=case["overlap"],
    )
    expected = dict(case["expected"])
    assert len(got["reasons"]) == expected.pop("reasons"), got["reasons"]
    for key, value in expected.items():
        assert got[key] == value, key


@pytest.mark.parametrize(
    ("baseline", "candidate", "min_delta", "leaks", "status", "reason"),
    [
        ([1.0], [float("nan")], 0.0, 0, "incomplete", "nonfinite"),
        ([float("-inf")], [1.0], 0.0, 0, "incomplete", "nonfinite"),
        ([0.5], [0.5], float("-inf"), 0, "incomplete", "must be finite"),
        ([0.5], [0.5], float("nan"), 0, "incomplete", "must be finite"),
        ([1.0], [float("inf")], 0.0, 1, "fail", "nonfinite"),
    ],
)
def test_verdict_rejects_nonfinite(baseline, candidate, min_delta, leaks, status, reason):
    got = vf.verify_verdict(baseline, candidate, min_delta=min_delta, leaks=leaks)
    assert got["status"] == status
    assert got["delta"] is None
    assert any(reason in text for text in got["reasons"]), got["reasons"]
    json.dumps(got, allow_nan=False)


@pytest.mark.parametrize("case", FIXTURE["termination_label"], ids=lambda c: c["name"])
def test_termination_label(case):
    assert vf.termination_label(case["status"]) == case["expected"]


@pytest.mark.parametrize("case", FIXTURE["seed_from_version"], ids=lambda c: c["name"])
def test_seed_from_version(case):
    if "error" in case:
        with pytest.raises(ValueError, match=case["error"]):
            vf.seed_from_version(case["request"], case["version"],
                                 agent_version=case["agent_version"])
        return
    request, seeded = vf.seed_from_version(
        case["request"], case["version"], agent_version=case["agent_version"]
    )
    assert request == case["expected_request"]
    assert seeded == case["expected_seeded"]
    baseline = case["request"]["optimization_configuration"]["baseline_agent_configuration"]
    assert baseline["system_prompt"] == "local"


def test_status_labels_early_stop(capsys):
    _print_optimizer_status({
        "id": "job1",
        "status": "succeeded",
        "message": "Maximum consecutive stall limit reached",
        "result": {"candidate_summary": {"baseline_id": "c0", "baseline_score": 0.5,
                                         "best_id": "c0", "best_score": 0.5}},
    })
    out = capsys.readouterr().out
    assert "succeeded (stopped early (no improvement))" in out
    assert "best          : c0 score=0.5" in out


def _setup(tmp_path: Path, candidate_instructions: str) -> Path:
    configs = tmp_path / ".agent_configs"
    for name, text in (("baseline", "You are a contracts assistant.\n"),
                       ("cand1", candidate_instructions)):
        (configs / name).mkdir(parents=True)
        (configs / name / "instructions.md").write_text(text, encoding="utf-8")
    (tmp_path / "train.jsonl").write_text(
        json.dumps({"query": "Which clause covers early termination for convenience by the buyer?",
                    "ground_truth": "Clause fourteen point two"}) + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "eval.yaml"
    config.write_text(
        "agent:\n  name: opt\nevaluators:\n  - name: gold\n"
        "dataset:\n  local_uri: train.jsonl\n"
        "options:\n  eval_model: gpt-4o\n  optimization_model: gpt-5-mini\n",
        encoding="utf-8",
    )
    return config


def _args(config: Path, tmp_path: Path) -> list[str]:
    return ["--candidate", "cand1", "--config", str(config),
            "--config-dir", str(tmp_path / ".agent_configs")]


def test_diff_flags_copied_training_text(tmp_path, capsys):
    config = _setup(tmp_path, "You are a contracts assistant.\n"
                    "Clause fourteen point two covers termination.\n")
    rc = main(["optimize", "diff", *_args(config, tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "+Clause fourteen point two covers termination." in captured.out
    assert 'row 0 ground_truth: "clause fourteen point two"' in captured.out


def test_diff_clean_candidate(tmp_path, capsys):
    config = _setup(tmp_path, "You are a careful contracts assistant.\n")
    assert main(["optimize", "diff", *_args(config, tmp_path)]) == 0
    assert "no verbatim training text found" in capsys.readouterr().out


def _results(path: Path, values: list[float]) -> str:
    path.write_text("".join(json.dumps({"score": v}) + "\n" for v in values), encoding="utf-8")
    return str(path)


def test_verify_pass_and_report(tmp_path, capsys):
    config = _setup(tmp_path, "You are a careful contracts assistant.\n")
    held = tmp_path / "held.jsonl"
    held.write_text(json.dumps({"query": "Who may assign the contract?"}) + "\n", encoding="utf-8")
    out = tmp_path / "report.json"
    rc = main(["optimize", "verify", *_args(config, tmp_path),
               "--held-out", str(held),
               "--baseline-results", _results(tmp_path / "b.jsonl", [0.0, 1.0]),
               "--candidate-results", _results(tmp_path / "c.jsonl", [1.0, 1.0]),
               "--out", str(out)])
    assert rc == 0
    assert "result        : pass" in capsys.readouterr().out
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"]["delta"] == 0.5
    assert report["overlap_rows"] == []


def test_verify_fails_on_overlap(tmp_path, capsys):
    config = _setup(tmp_path, "You are a careful contracts assistant.\n")
    held = tmp_path / "held.json"
    held.write_text(json.dumps(
        [{"query": "Which clause covers early termination for convenience by the buyer"}]
    ), encoding="utf-8")
    rc = main(["optimize", "verify", *_args(config, tmp_path), "--held-out", str(held),
               "--baseline-results", _results(tmp_path / "b.jsonl", [1.0]),
               "--candidate-results", _results(tmp_path / "c.jsonl", [1.0])])
    assert rc == 1
    assert "also appear in training" in capsys.readouterr().err


def test_verify_incomplete_without_results(tmp_path, capsys):
    config = _setup(tmp_path, "You are a careful contracts assistant.\n")
    assert main(["optimize", "verify", *_args(config, tmp_path)]) == 3
    assert "result        : incomplete" in capsys.readouterr().out


def test_run_from_version_seeds_request(tmp_path, capsys, monkeypatch):
    config = _setup(tmp_path, "unused\n")
    calls = []

    def fake_fetch(endpoint, agent, version):
        calls.append((endpoint, agent, version))
        return {"definition": {"kind": "prompt", "model": "gpt-4.1",
                               "instructions": "Deployed v4 prompt."}}

    monkeypatch.setattr(verify_cli, "fetch_agent_version", fake_fetch)
    rc = main(["optimize", "run", "--config", str(config),
               "--config-dir", str(tmp_path / ".agent_configs"),
               "--from-version", "4", "--project-endpoint", "https://example/api",
               "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert calls == [("https://example/api", "opt", "4")]
    assert "seeded from   : version 4 (instructions, model, 0 function tool(s))" in out
    assert "Deployed v4 prompt." in out


def test_run_from_version_requires_endpoint(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    config = _setup(tmp_path, "unused\n")
    rc = main(["optimize", "run", "--config", str(config),
               "--config-dir", str(tmp_path / ".agent_configs"),
               "--from-version", "4", "--dry-run"])
    assert rc == 2
    assert "--from-version reads the project" in capsys.readouterr().err

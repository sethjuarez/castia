"""Offline tests for the repeated, resumable eval-run harness."""

from __future__ import annotations

import json

import pytest

from castia.evaluation import foundry
from castia.evaluation.harness import (
    FoundryRestBackend,
    HarnessConfig,
    RunSnapshot,
    format_report,
    judge,
    run_harness,
    snapshot_from_run,
    summarize,
)


def done(passed, failed, *, errored=0, total=None, crit=None, status="completed"):
    total = passed + failed + errored if total is None else total
    return RunSnapshot(status=status, total=total, errored=errored, passed=passed,
                       failed=failed, criteria=crit or {"gold": [passed, failed]})


class FakeBackend:
    """Each run reports ``pending`` for ``lag`` polls, then its scripted result."""

    def __init__(self, results, lag=1):
        self.results = list(results)
        self.lag = lag
        self.submitted: list[tuple[str, dict]] = []
        self.polls: dict[str, int] = {}
        self.max_seen_in_flight = 0
        self.live: set[str] = set()

    def submit(self, name, metadata):
        run_id = f"run_{len(self.submitted) + 1}"
        self.submitted.append((name, metadata))
        self.live.add(run_id)
        self.max_seen_in_flight = max(self.max_seen_in_flight, len(self.live))
        return run_id

    def poll(self, run_id):
        self.polls[run_id] = self.polls.get(run_id, 0) + 1
        if self.polls[run_id] <= self.lag:
            return RunSnapshot(status="in_progress")
        self.live.discard(run_id)
        return self.results[int(run_id.split("_")[1]) - 1]


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, s):
        self.now += s

    def __call__(self):
        return self.now


def cfg(**kw):
    base = {"rung": "base", "rows": 60, "runs": 4, "max_in_flight": 2,
            "poll_interval": 1, "timeout": 100}
    return HarnessConfig(**{**base, **kw})


def test_harness_caps_in_flight_names_runs_and_accepts(tmp_path):
    backend = FakeBackend([done(50, 10)] * 4)
    clock = Clock()
    state = run_harness(backend, cfg(), tmp_path / "s.json", sleep=clock.sleep, clock=clock)
    assert backend.max_seen_in_flight == 2
    assert [n for n, _ in backend.submitted] == [f"base · n60 · r{i}" for i in range(1, 5)]
    assert backend.submitted[0][1] == {"castia.rung": "base", "castia.repeat": "1"}
    assert [e["decision"] for e in state["runs"]] == ["accepted"] * 4
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8")) == state


@pytest.mark.parametrize(
    ("snap", "reason"),
    [
        (done(40, 10, errored=10), "incomplete: scored 50/60"),
        (done(57, 0, errored=3, total=60), None),
        (done(0, 0, status="failed", total=0), "status failed"),
        (done(0, 0, total=0), "no rows scored"),
    ],
)
def test_judge(snap, reason):
    got = judge(snap, cfg(max_errors=None))
    assert got == reason or (reason and got.startswith(reason))


def test_judge_max_errors():
    assert judge(done(990, 0, errored=11), cfg(rows=1001)) == "too many errors: 11 > 10"


def test_harness_resumes_without_resubmitting(tmp_path):
    path = tmp_path / "s.json"
    clock = Clock()
    first = FakeBackend([done(50, 10)] * 4, lag=100)
    with pytest.raises(TimeoutError, match="rerun to resume"):
        run_harness(first, cfg(timeout=3), path, sleep=clock.sleep, clock=clock)
    assert len(first.submitted) == 2

    second = FakeBackend([done(50, 10)] * 4, lag=0)
    second.submitted = list(first.submitted)  # run ids keep counting from run_3
    state = run_harness(second, cfg(), path, sleep=clock.sleep, clock=clock)
    assert len(second.submitted) == 4
    assert set(second.polls) == {"run_1", "run_2", "run_3", "run_4"}
    assert [e["decision"] for e in state["runs"]] == ["accepted"] * 4


def test_state_for_another_rung_is_refused(tmp_path):
    path = tmp_path / "s.json"
    run_harness(FakeBackend([done(1, 0)], lag=0), cfg(runs=1), path)
    with pytest.raises(ValueError, match="pass a different --state"):
        run_harness(FakeBackend([]), cfg(rung="cand", runs=1), path)


def test_report_has_n_beside_every_score_and_excludes_rejected(tmp_path):
    results = [
        done(40, 10, crit={"gold": [40, 10], "style": [45, 5]}),
        done(30, 20, crit={"gold": [30, 20], "style": [50, 0]}),
        done(10, 10, errored=30, total=50),
    ]
    state = run_harness(FakeBackend(results, lag=0), cfg(runs=3, rows=50, max_in_flight=3),
                        tmp_path / "s.json")
    summary = summarize(state)
    assert (summary["accepted"], summary["rejected"]) == (2, 1)
    gold = summary["criteria"]["gold"]
    assert gold == {"mean": 0.7, "min": 0.6, "max": 0.8, "runs": 2, "n": 100, "min_n": 50}
    text = "\n".join(format_report(state, requested=3))
    assert "gold                 0.800 (n=50)" in text
    assert "score         : gold                 0.700 [0.600, 0.800] (runs=2, n=100)" in text
    assert "accepted      : 2/3 runs" in text
    assert "incomplete: scored 20/50" in text
    assert "warning" not in text


def test_report_warns_on_small_n(tmp_path):
    state = run_harness(FakeBackend([done(20, 5)], lag=0), cfg(runs=1, rows=25),
                        tmp_path / "s.json")
    assert any("n=25 in some run (< 50)" in line for line in format_report(state, requested=1))


def test_snapshot_from_openai_run_object():
    snap = snapshot_from_run({
        "status": "completed",
        "result_counts": {"total": 10, "errored": 1, "failed": 3, "passed": 6},
        "per_testing_criteria_results": [{"testing_criteria": "gold-1", "passed": 6, "failed": 3}],
        "report_url": "https://ai.azure.com/r",
    })
    assert (snap.total, snap.errored, snap.scored, snap.criteria) == (10, 1, 9, {"gold-1": [6, 3]})


def test_rest_backend_clones_template_data_source():
    calls = []

    class Client:
        def get_eval_run(self, eval_id, run_id):
            calls.append(("get", eval_id, run_id))
            return {"id": run_id, "data_source": {"type": "azure_ai_target_completions"},
                    "result_counts": {"total": 42}, "status": "completed"}

        def create_eval_run(self, eval_id, body):
            calls.append(("post", eval_id, body))
            return {"id": "run_new"}

    backend = FoundryRestBackend(Client(), "ev", "run_t")
    assert backend.default_rows() == 42
    assert backend.submit("base · n42 · r1", {"castia.rung": "base"}) == "run_new"
    assert calls[1] == ("post", "ev", {"name": "base · n42 · r1",
                                       "data_source": {"type": "azure_ai_target_completions"},
                                       "metadata": {"castia.rung": "base"}})
    assert backend.poll("run_t").status == "completed"


def test_cli_dry_run_and_validation(tmp_path, capsys):
    from castia.__main__ import main

    base = ["eval", "run", "--config", str(tmp_path / "missing.yaml"), "--runs", "3"]
    assert main([*base, "--rung", "base"]) == 2
    assert "--eval-id, --template-run" in capsys.readouterr().err
    full = [*base, "--rung", "base", "--eval-id", "e", "--template-run", "r", "--rows", "60"]
    assert main([*full, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 x 'base · n60', max 3 in flight" in out
    assert "eval_runs" in out and "base-n60.json" in out


def test_cli_exits_nonzero_when_runs_are_rejected(tmp_path, monkeypatch, capsys):
    from castia.__main__ import main

    backend = FakeBackend([done(50, 10), done(5, 5, errored=50, total=60)], lag=0)
    monkeypatch.setattr(foundry, "FoundryEvalClient", lambda endpoint: object())
    monkeypatch.setattr("castia.evaluation.harness.FoundryRestBackend",
                        lambda client, eval_id, run: backend)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://x")
    rc = main(["eval", "run", "--config", str(tmp_path / "missing.yaml"), "--runs", "2",
               "--rung", "base", "--rows", "60", "--eval-id", "e", "--template-run", "r",
               "--poll-interval", "0", "--state", str(tmp_path / "s.json")])
    assert rc == 1
    assert "accepted      : 1/2 runs" in capsys.readouterr().out

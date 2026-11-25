"""Offline tests for the Foundry eval REST client, delete --wait and run names."""

from __future__ import annotations

import json

import pytest

from castia.evaluation import foundry
from castia.evaluation.foundry import (
    FoundryEvalClient,
    is_templated_run_name,
    run_name,
)

ENDPOINT = "https://example.ai.azure.com/api/projects/p"


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body

    def text(self):
        return json.dumps(self._body or {})


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def make_client(monkeypatch, replies):
    calls: list[tuple[str, str]] = []
    queue = list(replies)

    class FakePipelineClient:
        def __init__(self, *, base_url, policies):
            assert base_url == ENDPOINT

        def send_request(self, request):
            calls.append((request.method, request.url))
            return queue.pop(0)

    monkeypatch.setattr("azure.core.PipelineClient", FakePipelineClient)
    clock = Clock()
    client = FoundryEvalClient(ENDPOINT, credential=object(), sleep=clock.sleep, clock=clock)
    return client, calls, clock


def test_delete_without_wait_returns_service_answer(monkeypatch):
    client, calls, clock = make_client(
        monkeypatch, [FakeResponse(200, {"deleted": False, "location": "/x"})]
    )
    assert client.delete_eval_run("ev 1", "run_1") == {"deleted": False, "location": "/x"}
    assert calls == [("DELETE", f"{ENDPOINT}/openai/v1/evals/ev%201/runs/run_1")]
    assert clock.sleeps == []


def test_delete_wait_polls_location_until_404(monkeypatch):
    location = "https://example.ai.azure.com/ops/del_1"
    client, calls, clock = make_client(
        monkeypatch,
        [
            FakeResponse(200, {"deleted": False, "location": location}),
            FakeResponse(200, {"status": "running"}),
            FakeResponse(404),
        ],
    )
    result = client.delete_eval_run("ev_1", "run_1", wait=True, interval=5, timeout=60)
    assert result["deleted"] is True
    assert calls[1:] == [("GET", location), ("GET", location)]
    assert clock.sleeps == [5, 5]


def test_delete_wait_polls_resource_when_no_location(monkeypatch):
    client, calls, _ = make_client(
        monkeypatch,
        [
            FakeResponse(200, {"deleted": False}),
            # A still-present run reports its own status (e.g. "completed");
            # that must not be mistaken for the delete finishing.
            FakeResponse(200, {"id": "run_1", "status": "completed"}),
            FakeResponse(404),
        ],
    )
    assert client.delete_eval("ev_1", wait=True, interval=1)["deleted"] is True
    assert [c[1] for c in calls] == [f"{ENDPOINT}/openai/v1/evals/ev_1"] * 3


def test_delete_wait_raises_on_timeout(monkeypatch):
    client, _, clock = make_client(
        monkeypatch,
        [FakeResponse(200, {"deleted": False, "location": "/ops/1"})]
        + [FakeResponse(200, {"status": "running"})] * 10,
    )
    with pytest.raises(TimeoutError, match="not confirmed within 30s"):
        client.delete_eval_run("ev_1", "run_1", wait=True, interval=10, timeout=30)
    assert clock.now == 30


def test_delete_wait_raises_on_failed_operation(monkeypatch):
    client, _, _ = make_client(
        monkeypatch,
        [
            FakeResponse(200, {"deleted": False, "location": "/ops/1"}),
            FakeResponse(200, {"status": "failed"}),
        ],
    )
    with pytest.raises(RuntimeError, match="failed"):
        client.delete_eval_run("ev_1", "run_1", wait=True, interval=1)


def test_delete_already_gone_is_success(monkeypatch):
    client, calls, _ = make_client(monkeypatch, [FakeResponse(404)])
    assert client.delete_eval_run("ev_1", "run_1", wait=True) == {}
    assert len(calls) == 1


def test_delete_http_error_raises(monkeypatch):
    client, _, _ = make_client(monkeypatch, [FakeResponse(403, {"error": "nope"})])
    with pytest.raises(RuntimeError, match="403"):
        client.delete_eval("ev_1")


@pytest.mark.parametrize(
    ("rung", "rows", "repeat", "expected"),
    [
        ("baseline", 50, None, "baseline · n50"),
        ("cand-2", 120, 3, "cand-2 · n120 · r3"),
    ],
)
def test_run_name_template(rung, rows, repeat, expected):
    name = run_name(rung, rows, repeat=repeat)
    assert name == expected
    assert is_templated_run_name(name)


@pytest.mark.parametrize(("rung", "rows"), [("", 5), ("a · b", 5), ("base", 0)])
def test_run_name_rejects_bad_input(rung, rows):
    with pytest.raises(ValueError):
        run_name(rung, rows)


def _suite(tmp_path, rows: int):
    (tmp_path / "data.jsonl").write_text(
        "".join(json.dumps({"query": f"q{i}"}) + "\n" for i in range(rows)) + "\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "eval.yaml"
    cfg.write_text("name: s\ndataset:\n  local_uri: data.jsonl\n", encoding="utf-8")
    return cfg


def test_eval_run_names_from_rung_and_dataset_rows(tmp_path, capsys):
    from castia.__main__ import main

    cfg = _suite(tmp_path, 7)
    assert main(["eval", "run", "--config", str(cfg), "--rung", "baseline", "--dry-run"]) == 0
    assert "--name baseline · n7" in capsys.readouterr().out


def test_eval_run_requires_a_name_for_live_runs(tmp_path, capsys, monkeypatch):
    from castia.__main__ import main

    monkeypatch.setattr("castia.evaluation.suite.run_azd", lambda argv: pytest.fail("ran azd"))
    cfg = _suite(tmp_path, 3)
    assert main(["eval", "run", "--config", str(cfg), "--no-prompt"]) == 2
    assert "pass --rung" in capsys.readouterr().err


def test_eval_run_warns_on_free_form_name(tmp_path, capsys):
    from castia.__main__ import main

    cfg = _suite(tmp_path, 3)
    assert main(["eval", "run", "--config", str(cfg), "--name", "try 4", "--dry-run"]) == 0
    assert "not '<rung> · n<rows>'" in capsys.readouterr().err


def test_eval_delete_cli_reports_timeout(monkeypatch, capsys):
    from castia.__main__ import main

    class FakeClient:
        def __init__(self, endpoint):
            assert endpoint == ENDPOINT

        def delete_eval_run(self, eval_id, run_id, **kwargs):
            assert kwargs["wait"] is True and kwargs["timeout"] == 5
            raise TimeoutError("delete not confirmed within 5s")

    monkeypatch.setattr(foundry, "FoundryEvalClient", FakeClient)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    rc = main(["eval", "delete", "--eval-id", "e", "--run-id", "r", "--wait", "--timeout", "5"])
    assert rc == 1
    assert "not confirmed" in capsys.readouterr().err

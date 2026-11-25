"""``castia.cli.v1`` envelope: shared Typra vectors plus CLI ``--json`` wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia.lifecycle.output import (
    CLI_SCHEMA,
    command_envelope,
    error_envelope,
    redact_secrets,
)

VECTORS = (
    Path(__file__).resolve().parents[3]
    / "spec" / "generated" / ".typra-generated" / "vectors.json"
)


def _vectors(operation: str) -> list[dict]:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    return [
        v["vector"] for v in data["vectors"]
        if v["contract"] == "LifecycleOutputRuntime" and v["operation"] == operation
    ]


@pytest.mark.parametrize("vector", _vectors("commandEnvelope"), ids=lambda v: v["name"])
def test_command_envelope_matches_spec_vectors(vector):
    args = vector["input"]

    def call():
        return command_envelope(
            args["command"], status=args["status"], result=args["result"],
            diagnostics=args["diagnostics"], next_actions=args["nextActions"],
            effects=args["effects"],
        )

    if "expectedError" in vector:
        with pytest.raises(ValueError, match=vector["expectedError"]["message"]):
            call()
    else:
        assert call() == vector["expected"]


@pytest.mark.parametrize("vector", _vectors("redactSecrets"), ids=lambda v: v["name"])
def test_redact_secrets_matches_spec_vectors(vector):
    assert redact_secrets(vector["input"]["value"]) == vector["expected"]


def test_vectors_are_present():
    assert len(_vectors("commandEnvelope")) >= 3
    assert len(_vectors("redactSecrets")) >= 2


def test_error_envelope_is_structural_and_redacted():
    env = error_envelope("optimize status", RuntimeError("401 Bearer abc.def"))
    assert env["schema"] == CLI_SCHEMA
    assert env["ok"] is False and env["status"] == "error"
    assert env["diagnostics"] == [
        {"severity": "error", "code": "RuntimeError", "message": "401 Bearer [REDACTED]"}
    ]


# --- CLI --json wiring ----------------------------------------------------


def _json_out(capsys) -> dict:
    out = capsys.readouterr().out
    env = json.loads(out)
    assert env["schema"] == CLI_SCHEMA
    return env


def test_eval_run_dry_run_json_is_offline(capsys):
    from castia.__main__ import main

    assert main(["eval", "run", "--config", "eval.yaml", "--backend", "agent",
                 "--dry-run", "--json"]) == 0
    env = _json_out(capsys)
    assert env["command"] == "eval run" and env["status"] == "dry_run" and env["ok"]
    assert env["effects"] == {"network": False, "billable": False, "mutating": False}
    assert env["result"]["argv"][:3] == ["azd", "ai", "agent"]


def test_eval_check_missing_config_is_error_envelope(tmp_path, capsys):
    from castia.__main__ import main

    rc = main(["eval", "check", "--config", str(tmp_path / "nope.yaml"), "--json"])
    env = _json_out(capsys)
    assert rc == 2 and env["ok"] is False and env["status"] == "error"


def test_finetune_check_json_reports_problems(tmp_path, capsys):
    from castia.__main__ import main

    dataset = tmp_path / "train.jsonl"
    dataset.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n",
        encoding="utf-8",
    )
    rc = main(["finetune", "check", "--type", "sft", "--dataset", str(dataset), "--json"])
    env = _json_out(capsys)
    assert rc == 1 and env["status"] == "problems" and env["ok"] is False
    assert env["result"]["training"] == {"path": str(dataset), "examples": 1}
    assert all(d["severity"] == "error" for d in env["diagnostics"])


def test_finetune_management_json_wraps_status(monkeypatch, capsys):
    from castia.__main__ import main
    from castia.finetuning import jobs

    class _Job:
        def model_dump(self, mode):
            return {"id": "ftjob-1", "status": "running", "api_key": "sekret"}

    class _Client:
        endpoint = "https://example.services.ai.azure.com/api/projects/p"

        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def status(self, job_id):
            return _Job()

        def timing(self, job):
            return None

    monkeypatch.setattr(jobs, "FineTuningClient", _Client)
    assert main(["finetune", "status", "ftjob-1", "--json"]) == 0
    env = _json_out(capsys)
    assert env["command"] == "finetune status" and env["status"] == "running"
    assert env["result"]["data"]["api_key"] == "[REDACTED]"
    assert env["effects"]["network"] is True and env["effects"]["mutating"] is False
    assert env["next"] == ["python -m castia finetune status ftjob-1 --watch"]


def test_optimize_status_json_failed_job_exits_one(monkeypatch, capsys):
    from castia.__main__ import main
    from castia.optimizing.jobs import OptimizerClient

    monkeypatch.setattr(OptimizerClient, "status", lambda self, job_id: {
        "id": job_id, "status": "Failed", "error": {"message": "boom"},
    })
    rc = main([
        "optimize", "status", "opt_1", "--project-endpoint",
        "https://example.services.ai.azure.com/api/projects/p", "--json",
    ])
    env = _json_out(capsys)
    assert rc == 1 and env["status"] == "failed" and env["ok"] is False
    assert env["result"]["job_id"] == "opt_1" and env["result"]["terminal"] is True
    assert env["diagnostics"][0]["code"] == "job_failed"


def test_optimize_json_flag_is_accepted_on_parent_and_subcommand(monkeypatch, capsys):
    from castia.__main__ import main
    from castia.optimizing.jobs import OptimizerClient

    monkeypatch.setattr(OptimizerClient, "cancel", lambda self, job_id: {"status": "cancelling"})
    rc = main([
        "optimize", "--json", "cancel", "opt_2", "--project-endpoint",
        "https://example.services.ai.azure.com/api/projects/p",
    ])
    env = _json_out(capsys)
    assert rc == 0 and env["status"] == "cancel_requested"
    assert env["effects"] == {"network": True, "billable": False, "mutating": True}

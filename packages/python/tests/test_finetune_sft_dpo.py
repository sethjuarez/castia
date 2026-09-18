"""Offline SFT and DPO fine-tuning helpers."""

from __future__ import annotations

import json

import pytest

from castia.__main__ import main
from castia.finetuning.dpo import (
    build_dpo_job,
    build_dpo_method,
    validate_dpo_example,
    validate_dpo_splits,
)
from castia.finetuning.sft import (
    build_sft_job,
    build_sft_method,
    validate_sft_example,
    validate_sft_splits,
)


def _sft_row(final_role: str = "assistant") -> dict:
    return {
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": final_role, "content": "4"},
        ]
    }


def _dpo_row() -> dict:
    return {
        "input": {
            "messages": [
                {"role": "system", "content": "Be precise."},
                {"role": "user", "content": "Explain gravity."},
            ]
        },
        "preferred_output": [
            {"role": "assistant", "content": "Gravity attracts objects with mass."}
        ],
        "non_preferred_output": [
            {"role": "assistant", "content": "Stuff falls."}
        ],
    }


def test_sft_requires_assistant_completion():
    assert validate_sft_example(_sft_row()) == []
    problems = validate_sft_example(_sft_row(final_role="user"))
    assert any("final message role must be 'assistant'" in p for p in problems)
    assert validate_sft_splits([_sft_row()], [_sft_row()]) == []


def test_sft_payload_builder_and_hyperparameters():
    job = build_sft_job(
        model="gpt-4.1-mini",
        training_file="file-train",
        validation_file="file-val",
        hyperparameters={"n_epochs": 2, "learning_rate_multiplier": 0.5},
        suffix="sft1",
    )
    assert job["method"] == {
        "type": "supervised",
        "supervised": {
            "hyperparameters": {"n_epochs": 2, "learning_rate_multiplier": 0.5}
        },
    }
    assert job["validation_file"] == "file-val"
    assert job["suffix"] == "sft1"

    with pytest.raises(ValueError):
        build_sft_method(hyperparameters={"beta": 0.1})


def test_dpo_validates_preference_pairs():
    assert validate_dpo_example(_dpo_row()) == []
    bad = _dpo_row()
    bad["preferred_output"] = [{"role": "user", "content": "Nope"}]
    problems = validate_dpo_example(bad)
    assert any("preferred_output" in p and "role must be" in p for p in problems)
    assert validate_dpo_splits([_dpo_row()]) == []


def test_dpo_payload_builder_and_hyperparameters():
    job = build_dpo_job(
        model="gpt-4.1",
        training_file="file-train",
        hyperparameters={"beta": 0.1, "l2_multiplier": 0.2},
    )
    assert job["method"] == {
        "type": "dpo",
        "dpo": {"hyperparameters": {"beta": 0.1, "l2_multiplier": 0.2}},
    }
    assert "validation_file" not in job

    with pytest.raises(ValueError):
        build_dpo_method(hyperparameters={"reasoning_effort": "high"})


def test_cli_sft_and_dpo_dry_run_payloads(tmp_path, capsys):
    sft = tmp_path / "sft.jsonl"
    sft.write_text(json.dumps(_sft_row()) + "\n", encoding="utf-8")
    assert main([
        "finetune", "submit", "--type", "sft", "--model", "gpt-4.1-mini",
        "--dataset", str(sft), "--n-epochs", "2", "--dry-run",
    ]) == 0
    out = capsys.readouterr().out
    assert "type          : sft" in out
    assert '"type": "supervised"' in out

    dpo = tmp_path / "dpo.jsonl"
    dpo.write_text(json.dumps(_dpo_row()) + "\n", encoding="utf-8")
    assert main([
        "finetune", "submit", "--type", "dpo", "--model", "gpt-4.1",
        "--dataset", str(dpo), "--beta", "0.1", "--dry-run",
    ]) == 0
    out = capsys.readouterr().out
    assert "type          : dpo" in out
    assert '"type": "dpo"' in out


def test_cli_keeps_rft_grader_boundary(tmp_path, capsys):
    sft = tmp_path / "sft.jsonl"
    grader = tmp_path / "grader.json"
    sft.write_text(json.dumps(_sft_row()) + "\n", encoding="utf-8")
    grader.write_text(json.dumps({"type": "string_check", "name": "x"}), encoding="utf-8")
    assert main([
        "finetune", "check", "--type", "sft", "--dataset", str(sft),
        "--grader", str(grader),
    ]) == 1
    assert "--grader is only valid with --type rft" in capsys.readouterr().out


def test_cli_rft_without_validation_still_checks_training(tmp_path, capsys):
    dataset = tmp_path / "bad-rft.jsonl"
    grader = tmp_path / "grader.json"
    dataset.write_text(json.dumps(_sft_row(final_role="assistant")) + "\n", encoding="utf-8")
    grader.write_text(
        json.dumps({
            "type": "string_check",
            "name": "acc",
            "input": "{{ sample.output_text }}",
            "reference": "{{ item.answer }}",
            "operation": "eq",
        }),
        encoding="utf-8",
    )
    assert main([
        "finetune", "check", "--dataset", str(dataset), "--grader", str(grader),
    ]) == 1
    out = capsys.readouterr().out
    assert "final message role must be 'user'" in out
    assert "answer" in out


def test_cli_rft_check_grader_stays_optional(tmp_path, capsys):
    dataset = tmp_path / "train.jsonl"
    validation = tmp_path / "val.jsonl"
    row = {"messages": [{"role": "user", "content": "Solve it."}]}
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    validation.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert main([
        "finetune", "check", "--dataset", str(dataset), "--validation", str(validation),
    ]) == 0
    assert "result        : ok" in capsys.readouterr().out


def test_sft_and_dpo_accept_tool_call_messages():
    sft = _sft_row()
    sft["messages"][-1] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function"}],
    }
    assert validate_sft_example(sft) == []

    dpo = _dpo_row()
    dpo["preferred_output"] = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function"}],
    }]
    assert validate_dpo_example(dpo) == []


def test_cli_live_submit_validates_payload_before_upload(tmp_path, monkeypatch, capsys):
    dataset = tmp_path / "sft.jsonl"
    dataset.write_text(json.dumps(_sft_row()) + "\n", encoding="utf-8")

    def fail_upload(*_args, **_kwargs):
        raise AssertionError("upload should not run before payload validation")

    monkeypatch.setattr("castia.finetuning.rft.upload_file", fail_upload)
    assert main([
        "finetune", "submit", "--type", "sft", "--model", "gpt-4.1-mini",
        "--dataset", str(dataset), "--beta", "0.1",
    ]) == 2
    assert "unknown SFT hyperparameter" in capsys.readouterr().err

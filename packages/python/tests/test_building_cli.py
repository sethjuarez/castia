"""The build registrar plugs into argparse without importing the root CLI."""

import argparse
import json

import pytest

from castia.building.cli import register_commands


def parser():
    result = argparse.ArgumentParser()
    register_commands(result.add_subparsers(dest="command", required=True))
    return result


def test_init_and_check_cli_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    args = parser().parse_args(["build", "init", str(tmp_path), "--name", "cli-agent"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["app_target"] == "main:app"
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    args = parser().parse_args(["build", "check", str(tmp_path)])
    assert args.func(args) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "offline"


def test_cli_conflict_is_error_without_overwrite(tmp_path, capsys):
    marker = tmp_path / "main.py"
    marker.write_text("unchanged")
    args = parser().parse_args(["build", "scaffold", str(tmp_path)])
    assert args.func(args) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "FileExistsError"
    assert marker.read_text() == "unchanged"


def test_preflight_alias_and_required_subcommand():
    args = parser().parse_args(["build", "preflight", "--app", "module:agent"])
    assert args.project == "."
    assert args.app == "module:agent"
    assert args.build_action == "check"
    with pytest.raises(SystemExit):
        parser().parse_args(["build"])


def test_actual_project_test_subcommand(tmp_path, capsys):
    args = parser().parse_args(["build", "scaffold", str(tmp_path)])
    assert args.func(args) == 0
    capsys.readouterr()
    args = parser().parse_args(["build", "test", str(tmp_path)])
    assert args.func(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert "2 passed" in report["output"]


def test_deployment_flag_is_registered():
    args = parser().parse_args(["build", "check", "--deployment"])
    assert args.deployment is True

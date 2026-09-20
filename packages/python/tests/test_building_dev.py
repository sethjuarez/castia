import json
import subprocess
import sys
from pathlib import Path

from castia.building.dev import dev_preflight, run_dev_server
from castia.building.scaffold import scaffold_project


def write_local_env(root):
    (root / ".env").write_text(
        "FOUNDRY_PROJECT_ENDPOINT=https://example.services.ai.azure.com/api/projects/demo\n"
        "AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4o\n"
        "PORT=8099\n",
        encoding="utf-8",
    )


def statuses(report):
    return {item.check: item.status for item in report.diagnostics}


def test_dev_preflight_loads_env_and_prints_local_endpoints_without_live_work(tmp_path):
    scaffold_project(tmp_path)
    write_local_env(tmp_path)

    report = dev_preflight(tmp_path)

    assert report.ok, report.to_dict()
    assert report.local_url == "http://127.0.0.1:8099"
    assert report.readiness_url == "http://127.0.0.1:8099/readiness"
    assert report.responses_url == "http://127.0.0.1:8099/responses"
    assert statuses(report)["env_file"] == "pass"
    assert statuses(report)["toolbox.config"] == "skipped"
    assert statuses(report)["optimizer.drift"] == "pass"
    assert "https://example" not in json.dumps(report.to_dict())


def test_dev_preflight_fails_partial_toolbox_configuration(tmp_path):
    scaffold_project(tmp_path)
    env = {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
        "TOOLBOX_NAME": "contracts",
    }

    report = dev_preflight(
        tmp_path,
        env_file=".missing",
        environment=env,
        optimize_check=False,
    )

    assert not report.ok
    assert statuses(report)["toolbox.config"] == "fail"


def test_run_dev_server_uses_relative_dotted_entrypoint(tmp_path, monkeypatch):
    package = tmp_path / "agent"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text("from castia import Agent\napp = Agent(name='demo')\n")
    calls = []

    def fake_run(argv, *, cwd, env, check):
        calls.append((argv, cwd, env, check))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_dev_server(tmp_path, app_target="agent.main:app") == 0
    assert calls[0][0][-1] == str(Path("agent") / "main.py")
    assert calls[0][1] == tmp_path


def test_root_cli_registers_dev_command():
    result = subprocess.run(
        [sys.executable, "-m", "castia", "dev", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--check-only" in result.stdout
    assert "--require-prompty" in result.stdout

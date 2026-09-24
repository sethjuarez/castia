import asyncio
import json
import subprocess
import sys

import pytest

from castia import Agent
from castia.building import AgentTestHarness


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("build", ("scaffold", "check", "test")),
        ("lifecycle", ("snapshot", "dataset", "evaluate", "promote", "rollback")),
        ("observe", ("features", "traces", "local-traces", "verify", "suite")),
        ("finetune", ("submit", "status", "results", "handoff")),
        ("optimize", ("run", "status", "cancel", "apply")),
    ],
)
def test_root_cli_registers_workflow_commands(command, expected):
    result = subprocess.run(
        [sys.executable, "-m", "castia", command, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in expected:
        assert name in result.stdout


def test_documented_protocol_harness_example():
    app = Agent(name="echo")

    @app.responses()
    async def echo(text: str) -> str:
        return text

    async def check_echo():
        async with AgentTestHarness(app) as harness:
            response = await harness.client.post("/responses", json={"input": "hello"})
            assert response.json()["output_text"] == "hello"

    asyncio.run(check_echo())


def test_root_drift_cli_fails_uncovered_feature_without_network(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {"schema_version": 1, "name": "uncovered", "features": ["runtime.activity"]}
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "castia", "observe", "suite", "run",
            "--suite", str(suite), "--live",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is False

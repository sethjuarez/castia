"""Unit tests for Castia-native Agent Optimizer ownership."""

from __future__ import annotations

import json
from pathlib import Path

from castia.__main__ import main
from castia.optimizer import (
    OptimizerClient,
    apply_candidate_config,
    build_optimizer_request,
    load_optimizer_state,
)


def _write_agent_config(root: Path, *, tool_key: str = "tool_file") -> None:
    baseline = root / ".agent_configs" / "baseline"
    baseline.mkdir(parents=True)
    (baseline / "instructions.md").write_text("Use the web tool carefully.\n", encoding="utf-8")
    (baseline / "tools.json").write_text(
        json.dumps(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web",
                        "description": "Search the web.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (baseline / "metadata.yaml").write_text(
        "model: gpt-4o\n"
        "instruction_file: instructions.md\n"
        f"{tool_key}: tools.json\n",
        encoding="utf-8",
    )


def _eval_config() -> dict:
    return {
        "agent": {"name": "optimizer-smoke"},
        "evaluators": [{"name": "smoke-core", "version": "1"}],
        "dataset": {"local_uri": "data.jsonl"},
        "options": {
            "eval_model": "gpt-4o",
            "optimization_model": "gpt-5-mini",
            "max_candidates": 2,
        },
    }


def test_build_optimizer_request_inlines_tools_and_dataset(tmp_path: Path):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text(
        '{"query":"hello","answer":"world"}\n',
        encoding="utf-8",
    )

    request = build_optimizer_request(eval_config=_eval_config(), root=tmp_path)

    assert request["agent"]["agent_name"] == "optimizer-smoke"
    assert request["options"]["eval_model"] == "gpt-4o"
    assert request["options"]["optimization_model"] == "gpt-5-mini"
    opt = request["options"]["optimization_config"]
    assert opt["model"] == "gpt-4o"
    assert opt["system_prompt"] == "Use the web tool carefully.\n"
    assert opt["tools"][0]["function"]["name"] == "web"
    assert request["train_dataset"] == {
        "type": "inline",
        "items": [{"query": "hello", "answer": "world"}],
    }


def test_build_optimizer_request_accepts_azd_tools_file_alias(tmp_path: Path):
    _write_agent_config(tmp_path, tool_key="tools_file")
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")

    request = build_optimizer_request(eval_config=_eval_config(), root=tmp_path)

    assert request["options"]["optimization_config"]["tools"][0]["function"]["name"] == "web"


def test_build_optimizer_request_falls_back_when_agent_config_is_app_relative(
    tmp_path: Path,
):
    _write_agent_config(tmp_path / "src" / "agent")
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")
    config = _eval_config()
    config["agent"]["config"] = ".agent_configs/baseline/metadata.yaml"

    request = build_optimizer_request(
        eval_config=config,
        root=tmp_path,
        config_dir="src/agent/.agent_configs",
    )

    assert request["options"]["optimization_config"]["tools"][0]["function"]["name"] == "web"


def test_build_optimizer_request_uses_registered_dataset_reference(tmp_path: Path):
    _write_agent_config(tmp_path)
    config = _eval_config()
    config.pop("dataset")
    config["dataset"] = {"name": "smoke-data", "version": "3"}

    request = build_optimizer_request(eval_config=config, root=tmp_path)

    assert request["train_dataset"] == {
        "type": "reference",
        "name": "smoke-data",
        "version": "3",
    }


def test_apply_candidate_config_writes_runtime_candidate_layout(tmp_path: Path):
    config = {
        "model": "gpt-5-mini",
        "system_prompt": "Prefer short answers.",
        "tools": [{"type": "function", "function": {"name": "web"}}],
        "skills": [{"name": "tone", "description": "Voice", "body": "Be crisp."}],
    }

    plan = apply_candidate_config("cand_1", config, config_dir=tmp_path / ".agent_configs")

    assert plan.metadata_path.read_text(encoding="utf-8") == (
        "model: gpt-5-mini\n"
        "instruction_file: instructions.md\n"
        "tool_file: tools.json\n"
        "tools_file: tools.json\n"
        "skill_dir: skills\n"
    )
    assert plan.instructions_path.read_text(encoding="utf-8") == "Prefer short answers."
    assert json.loads(plan.tools_path.read_text(encoding="utf-8"))[0]["function"]["name"] == "web"
    assert (plan.skills_dir / "tone" / "SKILL.md").read_text(encoding="utf-8") == (
        "---\nname: tone\ndescription: Voice\n---\nBe crisp.\n"
    )


def test_optimizer_client_uses_expected_rest_paths(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"id": "opt_1"}

    class FakePipelineClient:
        def __init__(self, *, base_url, policies):
            assert base_url == "https://example.ai.azure.com/api/projects/p"
            assert policies

        def send_request(self, request):
            assert request.headers["Foundry-Features"] == "AgentsOptimization=V2Preview"
            calls.append((request.method, request.url, getattr(request, "content", None)))
            return Response()

    class Credential:
        pass

    monkeypatch.setattr("castia.optimizer.PipelineClient", FakePipelineClient)
    client = OptimizerClient(
        "https://example.ai.azure.com/api/projects/p",
        credential=Credential(),
    )

    client.start({"x": 1})
    client.status("opt_1")
    client.cancel("opt_1")
    client.candidate_config("opt_1", "cand_1")

    assert calls == [
        (
            "POST",
            "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs?api-version=v1",
            '{"inputs": {"x": 1}}',
        ),
        (
            "GET",
            "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1?api-version=v1",
            None,
        ),
        (
            "POST",
            "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1:cancel?api-version=v1",
            None,
        ),
        (
            "GET",
            (
                "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/"
                "opt_1/candidates/cand_1/config?api-version=v1"
            ),
            None,
        ),
    ]


def test_optimize_run_dry_run_prints_payload_without_submitting(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")
    config = tmp_path / "eval.yaml"
    config.write_text(
        "agent:\n"
        "  name: optimizer-smoke\n"
        "evaluators:\n"
        "  - name: smoke-core\n"
        "dataset:\n"
        "  local_uri: data.jsonl\n"
        "options:\n"
        "  eval_model: gpt-4o\n"
        "  optimization_model: gpt-5-mini\n",
        encoding="utf-8",
    )

    def fail_submit(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("dry-run submitted")

    monkeypatch.setattr(OptimizerClient, "start", fail_submit)
    rc = main(
        [
            "optimize",
            "run",
            "--config",
            str(config),
            "--config-dir",
            str(tmp_path / ".agent_configs"),
            "--dry-run",
        ]
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "result        : dry-run (nothing submitted)" in out
    assert '"optimization_model": "gpt-5-mini"' in out
    assert '"name": "web"' in out


def test_optimize_run_waits_and_saves_state_in_config_dir(
    tmp_path: Path,
    capsys,
    monkeypatch,
):
    _write_agent_config(tmp_path / "src" / "agent")
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")
    config = tmp_path / "eval.yaml"
    config.write_text(
        "agent:\n"
        "  name: optimizer-smoke\n"
        "  config: .agent_configs/baseline/metadata.yaml\n"
        "evaluators:\n"
        "  - name: smoke-core\n"
        "dataset:\n"
        "  local_uri: data.jsonl\n"
        "options:\n"
        "  eval_model: gpt-4o\n"
        "  optimization_model: gpt-5-mini\n",
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def start(self, request):
            assert request["options"]["optimization_config"]["tools"][0]["function"]["name"] == "web"
            return {"id": "opt_1", "status": "queued"}

        def status(self, job_id):
            assert job_id == "opt_1"
            return {"id": "opt_1", "status": "succeeded", "result": {"best": "cand_1"}}

    monkeypatch.setattr("castia.optimizer.OptimizerClient", FakeClient)
    rc = main(
        [
            "optimize",
            "run",
            "--config",
            str(config),
            "--config-dir",
            "src/agent/.agent_configs",
            "--project-endpoint",
            "https://example.ai.azure.com/api/projects/p",
        ]
    )

    out = capsys.readouterr().out
    state = load_optimizer_state(config_dir=tmp_path / "src" / "agent" / ".agent_configs")
    assert rc == 0
    assert state.job_id == "opt_1"
    assert "status        : succeeded" in out

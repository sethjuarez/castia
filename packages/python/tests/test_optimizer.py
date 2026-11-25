"""Unit tests for Castia-native Agent Optimizer ownership."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia.__main__ import main
from castia.optimizing.jobs import (
    CandidateResolutionError,
    OptimizerClient,
    apply_candidate_config,
    build_optimizer_request,
    load_optimizer_state,
    resolve_candidate_config,
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


def _opt(request: dict) -> dict:
    return request["optimization_configuration"]


def test_build_optimizer_request_inlines_tools_and_dataset(tmp_path: Path):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text(
        '{"query":"hello","answer":"world"}\n',
        encoding="utf-8",
    )

    request = build_optimizer_request(eval_config=_eval_config(), root=tmp_path)

    assert request["target_configuration"] == {"type": "foundry_agent", "name": "optimizer-smoke"}
    assert request["optimization_model_configuration"] == {"model": "gpt-5-mini"}
    opt = _opt(request)
    assert opt["type"] == "agent_optimization"
    assert opt["candidate_search_configuration"] == {"max_candidates": 2}
    baseline = opt["baseline_agent_configuration"]
    assert baseline["current_model"] == "gpt-4o"
    assert baseline["system_prompt"] == "Use the web tool carefully.\n"
    assert baseline["tools"][0]["function"]["name"] == "web"
    evaluation = opt["evaluation_configuration"]
    assert evaluation["evaluation_model"] == {"model": "gpt-4o"}
    assert evaluation["evaluators"] == [{"name": "smoke-core", "version": "1"}]
    assert evaluation["training_set"] == {
        "type": "target_completion",
        "source": {"type": "inline", "test_cases": [{"query": "hello", "ground_truth": "world"}]},
    }


def test_build_optimizer_request_accepts_azd_tools_file_alias(tmp_path: Path):
    _write_agent_config(tmp_path, tool_key="tools_file")
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")

    request = build_optimizer_request(eval_config=_eval_config(), root=tmp_path)

    assert _opt(request)["baseline_agent_configuration"]["tools"][0]["function"]["name"] == "web"


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

    assert _opt(request)["baseline_agent_configuration"]["tools"][0]["function"]["name"] == "web"


def test_build_optimizer_request_uses_registered_dataset_reference(tmp_path: Path):
    _write_agent_config(tmp_path)
    config = _eval_config()
    config["dataset"] = {"name": "smoke-data", "version": 3}

    request = build_optimizer_request(eval_config=config, root=tmp_path)

    assert _opt(request)["evaluation_configuration"]["training_set"]["source"] == {
        "type": "dataset_reference",
        "name": "smoke-data",
        "version": "3",
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c["options"].update(max_stalls=3), "options.max_stalls is not supported"),
        (lambda c: c["options"].update(evaluation_level="turn"), "evaluation_level is not supported"),
        (lambda c: c.update(dataset={"name": "smoke-data"}), "dataset.version is required"),
        (
            lambda c: c["options"].update(optimization_config={"temperature": 0.2}),
            "unsupported keys: temperature",
        ),
    ],
)
def test_build_optimizer_request_rejects_unsupported_inputs(tmp_path: Path, mutate, message):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")
    config = _eval_config()
    mutate(config)

    with pytest.raises(ValueError, match=message):
        build_optimizer_request(eval_config=config, root=tmp_path)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ('{"query":"q","answer":"a","expected":"b"}', "conflicting ground truth"),
        ('{"query":"q","context":"c"}', "unsupported fields context"),
        ('{"answer":"a"}', "'query' must be non-empty text"),
    ],
)
def test_build_optimizer_request_validates_dataset_rows(tmp_path: Path, row, message):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text(row + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        build_optimizer_request(eval_config=_eval_config(), root=tmp_path)


def test_build_optimizer_request_traces_selects_skill_optimization(tmp_path: Path):
    config = _eval_config()
    config.pop("dataset")
    config["options"].pop("max_candidates")
    config["traces"] = {
        "resource_id": "/subscriptions/s/resourceGroups/r/providers/microsoft.insights/components/a",
        "window": {"start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-02T00:00:00Z"},
        "training_split_ratio": 0.8,
    }

    opt = _opt(build_optimizer_request(eval_config=config, root=tmp_path))

    assert opt["type"] == "skill_optimization"
    assert opt["training_split_ratio"] == 0.8
    context = opt["context"][0]
    assert context["trace_connection_configuration"]["agent_name"] == "optimizer-smoke"
    assert context["trace_window"]["type"] == "fixed"
    assert "training_set" not in opt["evaluation_configuration"]

    config["dataset"] = {"local_uri": "data.jsonl"}
    with pytest.raises(ValueError, match="cannot be combined with dataset"):
        build_optimizer_request(eval_config=config, root=tmp_path)
    config.pop("dataset")
    config["traces"]["window"]["end_time"] = "2026-01-02T00:00:00"
    with pytest.raises(ValueError, match="must include a timezone"):
        build_optimizer_request(eval_config=config, root=tmp_path)


def _job() -> dict:
    return {
        "id": "opt_1",
        "status": "succeeded",
        "optimization_configuration": {
            "type": "agent_optimization",
            "baseline_agent_configuration": {
                "system_prompt": "base prompt",
                "current_model": "gpt-4o",
                "tools": [{"type": "function", "function": {"name": "web", "description": "old"}}],
            },
        },
    }


def test_resolve_candidate_config_prefers_config_json_and_merges_tool_mutations():
    candidate = {
        "candidate_id": "cand_1",
        "status": "completed",
        "output": {
            "mutations": [
                {"type": "instructions", "value": "mutated prompt"},
                {
                    "type": "tools",
                    "value": [{"type": "function", "function": {"name": "web", "description": "new"}}],
                },
            ]
        },
    }

    config = resolve_candidate_config(
        _job(), candidate, {"instructions": "file prompt", "model": "gpt-5-mini"}
    )

    assert config["instructions"] == "file prompt"
    assert config["model"] == "gpt-5-mini"
    assert config["tools"] == [{"type": "function", "function": {"name": "web", "description": "new"}}]


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ({"candidate_id": "cand_0", "status": "completed", "output": {}}, "likely the baseline"),
        ({"candidate_id": "cand_1", "status": "failed"}, "only completed candidates"),
        (
            {"candidate_id": "cand_1", "output": {"mutations": [{"type": "temperature", "value": 1}]}},
            "unsupported or malformed mutation",
        ),
    ],
)
def test_resolve_candidate_config_refuses_unresolvable_candidates(candidate, message):
    with pytest.raises(CandidateResolutionError, match=message):
        resolve_candidate_config(_job(), candidate, None)


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


def test_optimizer_client_uses_ga_rest_paths(monkeypatch):
    calls: list[tuple[str, str, object]] = []
    base = "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs"
    candidate = {
        "candidate_id": "cand 1",
        "status": "completed",
        "files": [{"path": "config.json", "type": "config"}],
        "output": {"mutations": [{"type": "instructions", "value": "x"}]},
    }
    pages = {
        f"{base}/opt_1/candidates?api-version=v1": {
            "data": [{"candidate_id": "a"}], "has_more": True, "last_id": "a",
        },
        f"{base}/opt_1/candidates?after=a&api-version=v1": {"data": [{"candidate_id": "b"}]},
        f"{base}/opt_1/candidates/cand%201?expand=mutations&api-version=v1": candidate,
        f"{base}/opt_1?api-version=v1": _job(),
    }

    class Response:
        def __init__(self, url):
            self.url = url

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return pages.get(self.url, {"id": "opt_1"})

        def read(self) -> bytes:
            return b'{"instructions": "from file", "model": "gpt-5-mini"}'

    class FakePipelineClient:
        def __init__(self, *, base_url, policies):
            assert base_url == "https://example.ai.azure.com/api/projects/p"
            assert policies

        def send_request(self, request):
            assert "Foundry-Features" not in request.headers
            calls.append((request.method, request.url, getattr(request, "content", None)))
            return Response(request.url)

    class Credential:
        pass

    monkeypatch.setattr("castia.optimizing.jobs.PipelineClient", FakePipelineClient)
    client = OptimizerClient(
        "https://example.ai.azure.com/api/projects/p",
        credential=Credential(),
    )

    client.start({"x": 1})
    client.estimate({"x": 1})
    client.cancel("opt_1")
    assert [c["candidate_id"] for c in client.list_candidates("opt_1")] == ["a", "b"]
    config = client.candidate_config("opt_1", "cand 1")

    assert calls[0] == ("POST", f"{base}?api-version=v1", '{"x": 1}')
    assert calls[1] == ("POST", f"{base}:estimate?api-version=v1", '{"x": 1}')
    assert calls[2] == ("POST", f"{base}/opt_1:cancel?api-version=v1", None)
    assert calls[-1][1] == (
        f"{base}/opt_1/files?path=config.json&candidate_id=cand+1&api-version=v1"
    )
    assert config["instructions"] == "from file"
    assert config["model"] == "gpt-5-mini"


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
    assert '"optimization_model_configuration": {' in out
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
            tools = request["optimization_configuration"]["baseline_agent_configuration"]["tools"]
            assert tools[0]["function"]["name"] == "web"
            return {"id": "opt_1", "status": "queued"}

        def status(self, job_id):
            assert job_id == "opt_1"
            return {"id": "opt_1", "status": "succeeded", "result": {"best": "cand_1"}}

    monkeypatch.setattr("castia.optimizing.jobs.OptimizerClient", FakeClient)
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


ENDPOINT = "https://example.ai.azure.com/api/projects/p"


def _fake_client(monkeypatch, job: dict, calls: list) -> None:
    class FakeClient:
        def __init__(self, endpoint):
            assert endpoint == ENDPOINT

        def status(self, job_id):
            return job

        def estimate(self, request):
            calls.append(("estimate", request["target_configuration"]["name"]))
            return {"estimated_cost": {"total": 1.5}}

        def start(self, request):  # pragma: no cover - must not run
            raise AssertionError("submitted")

        def candidate_config(self, job_id, candidate_id):
            calls.append(("candidate_config", candidate_id))
            return {"instructions": "x"}

    monkeypatch.setattr("castia.optimizing.jobs.OptimizerClient", FakeClient)


def test_optimize_apply_best_refuses_when_baseline_wins(tmp_path: Path, capsys, monkeypatch):
    calls: list = []
    job = {
        "id": "opt_1",
        "status": "succeeded",
        "result": {"candidate_summary": {"baseline_id": "cand_0", "best_id": "cand_0"}},
    }
    _fake_client(monkeypatch, job, calls)

    rc = main(["optimize", "apply", "--job-id", "opt_1", "--project-endpoint", ENDPOINT,
               "--config-dir", str(tmp_path)])

    assert rc == 1
    assert "baseline cand_0 is best; nothing to apply" in capsys.readouterr().out
    assert calls == []


def test_optimize_status_exits_nonzero_for_skipped_job(capsys, monkeypatch):
    _fake_client(monkeypatch, {"id": "opt_1", "status": "skipped"}, [])

    rc = main(["optimize", "status", "opt_1", "--project-endpoint", ENDPOINT])

    assert rc == 1
    assert "status        : skipped" in capsys.readouterr().out


def test_optimize_run_estimate_starts_no_job(tmp_path: Path, capsys, monkeypatch):
    _write_agent_config(tmp_path)
    (tmp_path / "data.jsonl").write_text('{"query":"hello"}\n', encoding="utf-8")
    config = tmp_path / "eval.yaml"
    config.write_text(
        "agent:\n  name: optimizer-smoke\nevaluators:\n  - name: smoke-core\n"
        "dataset:\n  local_uri: data.jsonl\n"
        "options:\n  eval_model: gpt-4o\n  optimization_model: gpt-5-mini\n",
        encoding="utf-8",
    )
    calls: list = []
    _fake_client(monkeypatch, {}, calls)

    rc = main(["optimize", "run", "--config", str(config), "--config-dir",
               str(tmp_path / ".agent_configs"), "--project-endpoint", ENDPOINT, "--estimate"])

    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [("estimate", "optimizer-smoke")]
    assert "estimate only (no job started)" in out
    assert not (tmp_path / ".agent_configs" / ".castia").exists()

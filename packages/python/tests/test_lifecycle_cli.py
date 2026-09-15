"""Exercise the registrar directly; the parent CLI owns its root integration."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

from castia.lifecycle import ArtifactStore, Decision, Run, canonical_json
from castia.lifecycle.cli import _adapter, register_commands


@pytest.fixture
def workspace():
    root = Path.cwd() / f".lifecycle-cli-test-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        for name, module in tuple(sys.modules.items()):
            file = getattr(module, "__file__", None)
            if file and Path(file).resolve().is_relative_to(root):
                sys.modules.pop(name, None)
        shutil.rmtree(root)


@pytest.fixture
def inputs(workspace):
    module = f"lifecycle_adapter_{uuid.uuid4().hex}"
    (workspace / f"{module}.py").write_text(
        "import asyncio\n"
        "from pathlib import Path\n"
        "Path(__file__).with_suffix('.imported').write_text('imported')\n"
        "def redact(trace):\n"
        "    return trace\n"
        "async def evaluate_one(agent, example, repetition):\n"
        "    return {'quality': 0.9 if agent.instructions == 'better' else 0.8,\n"
        "            'cost': 0.001, 'latency_seconds': 0.01}\n"
        "async def fail_evaluation(*args):\n"
        "    raise RuntimeError('Bearer private-exception')\n"
        "async def missing_quality(*args):\n"
        "    return {'latency_seconds': 0.01}\n"
        "async def timeout_evaluation(*args):\n"
        "    await asyncio.sleep(5)\n"
        "async def deploy(candidate):\n"
        "    path = Path(__file__).with_suffix('.deployments')\n"
        "    previous = path.read_text() if path.exists() else ''\n"
        "    path.write_text(previous + candidate.id + '\\n')\n"
        "    return candidate.id\n"
        "async def verify(candidate, receipt):\n"
        "    return candidate.id == receipt\n"
        "async def reject(*args):\n"
        "    return False\n"
        "async def reconcile(state):\n"
        "    return {'no_outstanding_writes': True,\n"
        "            'evidence': 'offline fake operation completed; no background writes'}\n",
        encoding="utf-8",
    )
    (workspace / "agent.py").write_text("def agent(): return 'hello'\n", encoding="utf-8")
    (workspace / "instructions.md").write_bytes(b"baseline")
    (workspace / "snapshot.json").write_text(canonical_json({
        "source_files": ["agent.py", "instructions.md"], "dependencies": {"castia": "0.5.0"},
        "model": {"deployment": "baseline"}, "instructions": "baseline",
    }), encoding="utf-8")
    (workspace / "evaluator.json").write_text(canonical_json({
        "name": "quality", "version": "v1", "configuration": {"judge": "offline"},
    }), encoding="utf-8")
    (workspace / "traces.jsonl").write_text("".join(
        canonical_json({
            "trace_id": f"trace-{i}", "input": f"question-{i}", "reference": f"answer-{i}",
            "reviewer": "human", "approved": True, "group": f"group-{i}",
            "reference_origin": "human", "model_output": f"generated-{i}",
        }) + "\n"
        for i in range(6)
    ), encoding="utf-8")
    return module


def invoke(workspace, capsys, *arguments):
    parser = argparse.ArgumentParser()
    register_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args([
        "lifecycle", "--store", str(workspace / "evidence"), *map(str, arguments),
    ])
    code = args.func(args)
    output = capsys.readouterr()
    return code, json.loads(output.out) if output.out.strip() else None, output.err


def snapshot(workspace, capsys):
    code, result, error = invoke(
        workspace, capsys, "snapshot", "--root", workspace,
        "--config", workspace / "snapshot.json",
    )
    assert code == 0 and not error
    return result["id"]


def dataset(workspace, capsys, module):
    code, result, error = invoke(
        workspace, capsys, "dataset", "--traces", workspace / "traces.jsonl",
        "--redactor", f"{module}:redact", "--redaction-version", "policy-v1",
        "--adapter-root", workspace, "--allow-code",
    )
    assert code == 0 and not error
    return result["id"]


def evaluation(workspace, capsys, module, agent_id, dataset_id):
    code, result, error = invoke(
        workspace, capsys, "evaluate", "--agent", agent_id, "--dataset", dataset_id,
        "--evaluator", workspace / "evaluator.json", "--callback", f"{module}:evaluate_one",
        "--adapter-root", workspace, "--allow-code", "--repeats", "2", "--concurrency", "2",
    )
    assert code == 0 and not error
    return result["id"]


def test_all_lifecycle_commands_end_to_end(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    baseline_run = evaluation(workspace, capsys, inputs, agent_id, dataset_id)
    config_path = workspace / "snapshot.json"
    config = json.loads(config_path.read_text())
    config["instructions"] = "better"
    config_path.write_text(json.dumps(config))
    (workspace / "instructions.md").write_bytes(b"better")
    better_id = snapshot(workspace, capsys)
    better_run = evaluation(workspace, capsys, inputs, better_id, dataset_id)
    code, decision, error = invoke(workspace, capsys, "compare", baseline_run, better_run)
    assert code == 0 and not error and decision["kind"] == "decision"
    code, initial_decision, _ = invoke(workspace, capsys, "compare", baseline_run, baseline_run)
    assert code == 0

    config_file = workspace / "instructions.md"
    config_file.write_text("baseline")
    code, initial, _ = invoke(
        workspace, capsys, "stage", "--root", workspace, "--file", "instructions.md",
        "--baseline", agent_id, "--agent", agent_id,
    )
    assert code == 0
    config_file.write_text("better")
    code, candidate, _ = invoke(
        workspace, capsys, "stage", "--root", workspace, "--file", "instructions.md",
        "--baseline", agent_id, "--agent", better_id,
    )
    assert code == 0
    code, diff, _ = invoke(workspace, capsys, "diff", initial["id"], candidate["id"])
    assert code == 0 and set(diff) == {"instructions.md"}
    assert diff["instructions.md"]["before"] != diff["instructions.md"]["after"]
    code, shown, _ = invoke(workspace, capsys, "show", candidate["id"])
    assert code == 0 and shown["record"]["agent_id"] == better_id

    handoff = [
        "--journal", workspace / "journal", "--adapter-root", workspace,
        "--deploy", f"{inputs}:deploy", "--verify", f"{inputs}:verify",
        "--allow-code", "--execute",
    ]
    code, state, error = invoke(
        workspace, capsys, "promote", *handoff, "--expected-revision", "0",
        "--candidate", initial["id"], "--decision", initial_decision["id"],
    )
    assert code == 0 and not error and state["known_good"] == initial["id"]
    code, state, error = invoke(
        workspace, capsys, "promote", *handoff, "--expected-revision", "2",
        "--candidate", candidate["id"], "--decision", decision["id"],
    )
    assert code == 0 and not error and state["known_good"] == candidate["id"]
    code, state, error = invoke(
        workspace, capsys, "rollback", *handoff, "--expected-revision", "4",
    )
    assert code == 0 and not error and state["known_good"] == initial["id"]
    code, shown, _ = invoke(workspace, capsys, "show", "--journal", workspace / "journal")
    assert code == 0 and shown == state
    deployments = (workspace / f"{inputs}.deployments").read_text().splitlines()
    assert deployments == [initial["id"], candidate["id"], initial["id"]]


def test_allow_code_is_required_before_adapter_import(workspace, inputs, capsys):
    code, _, error = invoke(
        workspace, capsys, "dataset", "--traces", workspace / "traces.jsonl",
        "--redactor", f"{inputs}:redact", "--redaction-version", "policy-v1",
        "--adapter-root", workspace,
    )
    assert code == 2 and error
    assert not (workspace / f"{inputs}.imported").exists()
    assert not (workspace / "evidence").exists()


def test_reconcile_cli_records_explicit_quiescence_before_any_recovery(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    run_id = evaluation(workspace, capsys, inputs, agent_id, dataset_id)
    _, decision, _ = invoke(workspace, capsys, "compare", run_id, run_id)
    _, candidate, _ = invoke(
        workspace, capsys, "stage", "--root", workspace, "--file", "instructions.md",
        "--baseline", agent_id, "--agent", agent_id,
    )
    code, result, _ = invoke(
        workspace, capsys, "promote", "--journal", workspace / "journal",
        "--candidate", candidate["id"], "--decision", decision["id"], "--expected-revision", 0,
        "--deploy", f"{inputs}:deploy", "--verify", f"{inputs}:reject",
        "--adapter-root", workspace, "--allow-code", "--execute",
    )
    assert code == 2 and result is None
    code, state, _ = invoke(workspace, capsys, "show", "--journal", workspace / "journal")
    assert code == 0 and state["pending_cleanup"] and state["remote_state"] == "unknown"
    code, reconciled, error = invoke(
        workspace, capsys, "reconcile", "--journal", workspace / "journal",
        "--expected-revision", 2, "--verify", f"{inputs}:reconcile",
        "--adapter-root", workspace, "--allow-code", "--execute",
    )
    assert code == 0 and not error and not reconciled["pending_cleanup"]
    assert reconciled["remote_state"] == "unknown" and reconciled["last_status"] == "reconciled"
    assert reconciled["revision"] == 3 and reconciled["known_good"] is None


def test_execute_is_required_before_deployment_adapter_import(workspace, inputs, capsys):
    code, _, error = invoke(
        workspace, capsys, "promote", "--journal", workspace / "journal",
        "--expected-revision", 0, "--candidate", "0" * 64, "--decision", "1" * 64,
        "--deploy", f"{inputs}:deploy", "--verify", f"{inputs}:verify",
        "--adapter-root", workspace, "--allow-code",
    )
    assert code == 2 and error
    assert not (workspace / f"{inputs}.imported").exists()
    assert not (workspace / "journal").exists()


def test_rejected_comparison_is_persisted_with_status_one(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    run_id = evaluation(workspace, capsys, inputs, agent_id, dataset_id)
    (workspace / "gate.json").write_text('{"minimum_quality": 0.99}')
    code, result, error = invoke(
        workspace, capsys, "compare", run_id, run_id, "--gate", workspace / "gate.json",
    )
    assert code == 1 and not error
    decision = ArtifactStore(workspace / "evidence").get(result["id"])
    assert isinstance(decision, Decision) and not decision.accepted


def test_cli_accepts_deterministic_fixture_origins_and_reports_safe_schema_errors(
    workspace, inputs, capsys,
):
    path = workspace / "traces.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for i, row in enumerate(rows):
        row["reference_origin"] = "deterministic"
        row["reference"] = str(i + i)
        row["model_output"] = None
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))
    identity = dataset(workspace, capsys, inputs)
    record = ArtifactStore(workspace / "evidence").get(identity)
    assert all(e.reference_origins == ("deterministic",) for e in record.examples)
    rows[0]["reference_origin"] = "fixture"
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))
    code, result, error = invoke(
        workspace, capsys, "dataset", "--traces", path, "--redactor", f"{inputs}:redact",
        "--redaction-version", "fixture-v1", "--adapter-root", workspace, "--allow-code",
    )
    assert code == 2 and result is None
    assert "reference_origin:" in error
    assert "human, authoritative, deterministic" in error
    assert "model-generated gold is forbidden" not in error


def test_callback_errors_persist_evidence_without_private_error_text(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    code, result, error = invoke(
        workspace, capsys, "evaluate", "--agent", agent_id, "--dataset", dataset_id,
        "--evaluator", workspace / "evaluator.json", "--callback", f"{inputs}:fail_evaluation",
        "--adapter-root", workspace, "--allow-code",
    )
    assert code == 1 and not error and result["status"] == "inconclusive"
    run = ArtifactStore(workspace / "evidence").get(result["id"])
    assert isinstance(run, Run) and all(r.error == "callback_error" for r in run.results)
    assert "private-exception" not in canonical_json(run)


def test_unknown_quality_is_inconclusive_not_a_zero_score(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    code, result, error = invoke(
        workspace, capsys, "evaluate", "--agent", agent_id, "--dataset", dataset_id,
        "--evaluator", workspace / "evaluator.json", "--callback", f"{inputs}:missing_quality",
        "--adapter-root", workspace, "--allow-code",
    )
    assert code == 1 and not error and result["status"] == "inconclusive"
    run = ArtifactStore(workspace / "evidence").get(result["id"])
    assert isinstance(run, Run)
    assert all("quality" not in r.metrics for r in run.results)


def test_timeout_reports_inconclusive_and_unproven_remote_cleanup(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    code, result, error = invoke(
        workspace, capsys, "evaluate", "--agent", agent_id, "--dataset", dataset_id,
        "--evaluator", workspace / "evaluator.json", "--callback", f"{inputs}:timeout_evaluation",
        "--adapter-root", workspace, "--allow-code", "--timeout", "0.01",
    )
    assert code == 1 and result["status"] == "inconclusive"
    assert "not remote-job cleanup evidence" in error
    run = ArtifactStore(workspace / "evidence").get(result["id"])
    assert isinstance(run, Run) and all(r.error == "timeout" for r in run.results)


def test_adapter_import_errors_are_sanitized(workspace, capsys):
    name = f"broken_adapter_{uuid.uuid4().hex}"
    (workspace / f"{name}.py").write_text("raise RuntimeError('Bearer private-import')")
    (workspace / "traces.jsonl").write_text("")
    code, result, error = invoke(
        workspace, capsys, "dataset", "--traces", workspace / "traces.jsonl",
        "--redactor", f"{name}:redact", "--redaction-version", "policy-v1",
        "--adapter-root", workspace, "--allow-code",
    )
    assert code == 2 and result is None
    assert "RuntimeError" in error and "private-import" not in error


@pytest.mark.parametrize("reference", [
    "os:system", "sys:exit", "../escape:call", "adapter:call()", "adapter",
    "adapter:attribute.nested", "adapter.py:call", "adapter:call;execute",
])
def test_adapter_discovery_rejects_nonlocal_or_invalid_code(workspace, reference):
    with pytest.raises(ValueError):
        _adapter(reference, root=str(workspace), allow_code=True)


def test_adapter_accepts_reviewed_local_packages(workspace):
    name = f"adapter_package_{uuid.uuid4().hex}"
    package = workspace / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "callbacks.py").write_text("def identity(value): return value\n")
    callback = _adapter(f"{name}.callbacks:identity", root=str(workspace), allow_code=True)
    assert callback("safe") == "safe"


def test_cached_external_module_collision_is_rejected(workspace):
    (workspace / "json.py").write_text("def loads(value): return 'wrong'\n")
    with pytest.raises(ValueError, match="conflicts"):
        _adapter("json:loads", root=str(workspace), allow_code=True)


def test_adapter_must_name_a_callable(workspace):
    name = f"constant_adapter_{uuid.uuid4().hex}"
    (workspace / f"{name}.py").write_text("value = 42\n")
    with pytest.raises(TypeError):
        _adapter(f"{name}:value", root=str(workspace), allow_code=True)


def test_no_callback_import_when_handoff_revision_is_stale(workspace, inputs, capsys):
    code, result, error = invoke(
        workspace, capsys, "promote", "--journal", workspace / "journal",
        "--expected-revision", 99, "--candidate", "0" * 64, "--decision", "1" * 64,
        "--deploy", f"{inputs}:deploy", "--verify", f"{inputs}:verify",
        "--adapter-root", workspace, "--allow-code", "--execute",
    )
    assert code == 2 and result is None and error
    assert not (workspace / f"{inputs}.imported").exists()


def test_no_callback_import_when_rollback_has_no_verified_target(workspace, inputs, capsys):
    code, result, error = invoke(
        workspace, capsys, "rollback", "--journal", workspace / "journal",
        "--expected-revision", 0, "--deploy", f"{inputs}:deploy", "--verify", f"{inputs}:verify",
        "--adapter-root", workspace, "--allow-code", "--execute",
    )
    assert code == 2 and result is None and error
    assert not (workspace / f"{inputs}.imported").exists()


def test_manifest_wrong_type_duplicate_keys_and_unknown_fields_fail(workspace, inputs, capsys):
    for content in ('[]', '{"model":{},"model":{}}', '{"unknown":true}'):
        (workspace / "snapshot.json").write_text(content)
        code, result, error = invoke(
            workspace, capsys, "snapshot", "--root", workspace,
            "--config", workspace / "snapshot.json",
        )
        assert code == 2 and result is None and error


def test_wrong_artifact_types_and_show_traversal_fail(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    code, _, error = invoke(workspace, capsys, "compare", agent_id, agent_id)
    assert code == 2 and error
    code, _, error = invoke(workspace, capsys, "show", "..\\other")
    assert code == 2 and error
    code, _, error = invoke(workspace, capsys, "show", agent_id, "--journal", workspace / "journal")
    assert code == 2 and error


def test_deleted_baseline_artifact_blocks_comparison(workspace, inputs, capsys):
    agent_id = snapshot(workspace, capsys)
    dataset_id = dataset(workspace, capsys, inputs)
    run_id = evaluation(workspace, capsys, inputs, agent_id, dataset_id)
    (workspace / "evidence" / f"{run_id}.json").unlink()
    code, result, error = invoke(workspace, capsys, "compare", run_id, run_id)
    assert code == 2 and result is None and error


def test_store_flag_works_after_subcommand(workspace, inputs, capsys):
    other = workspace / "other-store"
    code, result, error = invoke(
        workspace, capsys, "snapshot", "--config", workspace / "snapshot.json",
        "--root", workspace, "--store", other,
    )
    assert code == 0 and not error
    assert ArtifactStore(other).get(result["id"])
    assert not (workspace / "evidence").exists()

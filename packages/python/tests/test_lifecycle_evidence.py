"""Offline lifecycle contracts: immutable evidence, no implicit cloud operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from castia.finetuning.rft import validate_rft_splits
from castia.lifecycle import (
    REFERENCE_ORIGINS,
    SCHEMA_VERSION,
    AcceptanceGate,
    ArtifactStore,
    Candidate,
    ConfigFile,
    ConflictError,
    DeploymentError,
    DeploymentJournal,
    EvaluationResult,
    Evaluator,
    FileDigest,
    IntegrityError,
    LifecycleValidationError,
    ReviewedTrace,
    Run,
    canonical_json,
    compare_runs,
    content_hash,
    create_agent_snapshot,
    curate_dataset,
    dataset_jsonl,
    diff_candidates,
    evaluate,
    record_from_dict,
    stage_candidate,
)


@pytest.fixture
def workspace():
    # Keep all test filesystem writes inside the checkout, including on Windows.
    root = Path.cwd() / f".lifecycle-test-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


@pytest.fixture
def agent(workspace):
    (workspace / "agent.py").write_text("print('hello')\n", encoding="utf-8")
    (workspace / "instructions.md").write_bytes(b"Be helpful.")
    return create_agent_snapshot(
        workspace, source_files=["agent.py", "instructions.md"], dependencies={"castia": "0.5.0"},
        model={"deployment": "baseline"}, instructions="Be helpful.",
    )


def traces(count=6):
    return [
        ReviewedTrace(
            trace_id=f"trace-{i}", input=f"question-{i}", reference=f"reference-{i}",
            reviewer="human-reviewer", group=f"conversation-{i}", approved=True,
            model_output=f"generated-{i}",
        )
        for i in range(count)
    ]


@pytest.fixture
def dataset():
    return curate_dataset(traces(), redact=lambda t: t, redaction_version="v1")


def run_for(agent, dataset, quality=0.8, *, metrics=None, evaluator=None):
    return Run(
        agent_id=agent.id, dataset_id=dataset.id,
        evaluator=evaluator or Evaluator("rubric", "v1", {"judge": "judge-v1"}),
        split="heldout", expected_ids=dataset.heldout_ids, repeats=1,
        results=tuple(
            EvaluationResult(e, 0, metrics if metrics is not None else {
                "quality": quality, "latency_seconds": 0.1, "cost": 0.001,
            })
            for e in dataset.heldout_ids
        ),
    )


def candidate_for(agent, baseline=None):
    return Candidate(
        baseline_id=(baseline or agent).id, agent_id=agent.id,
        files=(ConfigFile("instructions.md", agent.instructions),),
        agent_snapshot=agent,
    )


def with_instructions(agent, instructions):
    raw = instructions.encode("utf-8")
    files = tuple(
        FileDigest(f.path, hashlib.sha256(raw).hexdigest(), len(raw))
        if f.path == "instructions.md" else f for f in agent.source_files
    )
    return replace(agent, instructions=instructions, source_files=files)


async def quiescent_fake_operations(state):
    assert state.pending_cleanup
    return {
        "no_outstanding_writes": True,
        "evidence": "offline fake operations are terminal; no background writes exist",
    }


def test_canonical_identity_and_deep_immutability(workspace, agent, monkeypatch):
    assert content_hash({"b": [2, 1], "a": "é"}) == content_hash({"a": "é", "b": (2, 1)})
    assert agent.id == record_from_dict(agent.to_dict()).id
    config = {"deployment": "baseline", "nested": {"values": [1, 2]}}
    snapshot = replace(agent, model=config)
    before = snapshot.id
    config["nested"]["values"].append(3)
    assert snapshot.id == before
    with pytest.raises(TypeError):
        snapshot.model["deployment"] = "mutated"
    with pytest.raises(TypeError):
        snapshot.model["nested"]["values"][0] = 9
    with pytest.raises(FrozenInstanceError):
        snapshot.instructions = "changed"
    monkeypatch.setenv("LIFECYCLE_TEST_SECRET", "not-read-or-persisted")
    recreated = create_agent_snapshot(
        workspace, source_files=["agent.py", "instructions.md"], dependencies={"castia": "0.5.0"},
        model={"deployment": "baseline"}, instructions="Be helpful.",
    )
    assert recreated.id == agent.id
    assert "not-read-or-persisted" not in canonical_json(recreated)
    (workspace / "agent.py").write_text("changed", encoding="utf-8")
    changed = create_agent_snapshot(
        workspace, source_files=["agent.py", "instructions.md"], dependencies={"castia": "0.5.0"},
        model={"deployment": "baseline"}, instructions="Be helpful.",
    )
    assert changed.id != agent.id


@pytest.mark.parametrize("path", [
    "../escape", "..\\escape", "/absolute", "C:\\absolute", "C:relative",
    "\\\\server\\share", "dir/../escape", "dir//file", "./file",
    "file:stream", "CON.txt", "trailing.", "space ", "",
])
def test_paths_reject_traversal_and_windows_aliases(workspace, agent, path):
    with pytest.raises(ValueError):
        create_agent_snapshot(
            workspace, source_files=[path], dependencies={}, model={"deployment": "x"},
            instructions="safe",
        )
    with pytest.raises(ValueError):
        ConfigFile(path, "safe")


@pytest.mark.parametrize("value", [
    {"api_key": "private"}, {"apiKey": "private"}, {"clientSecret": "private"},
    {"nested": {"password": "private"}},
    {"endpoint": "https://name:password@example.test"},
    {"value": "Bearer private"}, {"value": float("nan")},
])
def test_config_rejects_credentials_and_nonfinite(agent, value):
    with pytest.raises(ValueError):
        replace(agent, model=value)


def test_explicit_source_only_and_credential_files(workspace, agent):
    (workspace / ".env").write_text("PASSWORD=private", encoding="utf-8")
    assert len(agent.source_files) == 2
    with pytest.raises(ValueError):
        create_agent_snapshot(
            workspace, source_files=[".env"], dependencies={}, model={"deployment": "x"},
            instructions="safe",
        )
    with pytest.raises(ValueError):
        replace(agent, source_files=agent.source_files * 2)
    with pytest.raises(ValueError):
        replace(agent, schema_version=True)
    with pytest.raises(ValueError):
        replace(agent, schema_version=2)


def test_artifact_roundtrips_all_records_and_is_idempotent(workspace, agent, dataset):
    store = ArtifactStore(workspace / "evidence")
    run = run_for(agent, dataset)
    decision = compare_runs(run, run)
    for record in (agent, dataset, run, candidate_for(agent), decision):
        identity = store.put(record)
        assert store.get(identity).to_dict() == record.to_dict()
        assert store.put(record) == identity
    assert len(list(store.root.glob("*.json"))) == 5
    assert not list(store.root.glob(".write-*"))


def test_atomic_concurrent_store_put(workspace, agent):
    store = ArtifactStore(workspace / "evidence")
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(lambda _: store.put(agent), range(32)))
    assert set(identities) == {agent.id}
    assert store.get(agent.id) == agent
    assert len(list(store.root.iterdir())) == 1


@pytest.mark.parametrize("mutation", [
    lambda p: p["record"].update(instructions="tampered"),
    lambda p: p["record"].update(schema_version=99),
    lambda p: p["record"].update(unknown="extra"),
    lambda p: p.update(id="0" * 64),
    lambda p: p.update(extra=True),
    lambda p: p.update(record=[]),
])
def test_tampered_manifest_fails_and_is_never_overwritten(workspace, agent, mutation):
    store = ArtifactStore(workspace)
    identity = store.put(agent)
    path = workspace / f"{identity}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    raw = json.dumps(payload)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(IntegrityError):
        store.get(identity)
    with pytest.raises(IntegrityError):
        store.put(agent)
    assert path.read_text(encoding="utf-8") == raw


def test_manifest_duplicate_keys_and_identity_traversal(workspace, agent):
    store = ArtifactStore(workspace)
    store.put(agent)
    path = workspace / f"{agent.id}.json"
    path.write_text('{"id":"a","id":"b","record":{}}', encoding="utf-8")
    with pytest.raises(IntegrityError):
        store.get(agent.id)
    for identity in ("../other", "..\\other", "A" * 64):
        with pytest.raises(ValueError):
            store.get(identity)


def test_curation_deterministic_deduplicated_and_independent(dataset):
    duplicate = replace(traces()[0], trace_id="trace-duplicate", reviewer="second-reviewer")
    rows = [*traces(), duplicate]
    first = curate_dataset(rows, redact=lambda t: t, redaction_version="v1")
    second = curate_dataset(reversed(rows), redact=lambda t: t, redaction_version="v1")
    assert first.id == second.id
    assert len(first.examples) == len(dataset.examples)
    example = next(e for e in first.examples if e.input == duplicate.input)
    assert example.provenance == ("trace-0", "trace-duplicate")
    assert example.reviewers == ("human-reviewer", "second-reviewer")
    assert not set(first.train_ids) & set(first.heldout_ids)
    assert "model_output" not in canonical_json(first)
    assert "generated-0" not in canonical_json(first)
    assert first.id != replace(first, redaction_version="v2").id


@pytest.mark.parametrize("mutation", [
    {"approved": False}, {"approved": 1}, {"reviewer": ""},
    {"reference_origin": "model"}, {"reference": "generated-0"},
])
def test_curation_refuses_unreviewed_or_model_gold(mutation):
    rows = traces()
    rows[0] = replace(rows[0], **mutation)
    with pytest.raises(ValueError):
        curate_dataset(rows, redact=lambda t: t, redaction_version="v1")


def test_deterministic_computed_references_preserve_their_actual_origin():
    rows = [
        replace(
            row, reference=str(i + i), reference_origin="deterministic",
            reviewer="fixture-rule-review", model_output=None,
        )
        for i, row in enumerate(traces())
    ]
    dataset = curate_dataset(rows, redact=lambda t: t, redaction_version="fixture-v1")
    assert all(e.reference_origins == ("deterministic",) for e in dataset.examples)
    assert "human" not in canonical_json(dataset)
    exported = [
        json.loads(line) for line in dataset_jsonl(dataset, "heldout").splitlines()
    ]
    assert all(row["reference_origins"] == ["deterministic"] for row in exported)
    assert record_from_dict(dataset.to_dict()) == dataset


@pytest.mark.parametrize("origin", ["fixture", "model", "llm", "synthetic", "", "Bearer private"])
def test_unknown_reference_origins_have_accurate_safe_diagnostics(origin):
    rows = [replace(row, reference_origin=origin) for row in traces()]
    with pytest.raises(LifecycleValidationError) as caught:
        curate_dataset(rows, redact=lambda t: t, redaction_version="v1")
    assert caught.value.code == "reference_origin"
    assert "human, authoritative, deterministic" in str(caught.value)
    assert "Bearer private" not in str(caught.value)


def test_deduplicated_examples_retain_each_independent_reference_origin():
    rows = traces()
    duplicate = replace(
        rows[0], trace_id="deterministic-copy", reference_origin="deterministic",
    )
    dataset = curate_dataset([*rows, duplicate], redact=lambda t: t, redaction_version="v1")
    example = next(e for e in dataset.examples if e.input == duplicate.input)
    assert example.reference_origins == ("deterministic", "human")


def test_curation_redacts_provenance_before_persistence():
    rows = [replace(t, trace_id=f"Bearer private-{i}", reviewer="Bearer private") for i, t in enumerate(traces())]
    calls = []

    def redact(row):
        calls.append(row)
        return replace(row, trace_id=content_hash(row.trace_id), reviewer="reviewed")

    dataset = curate_dataset(rows, redact=redact, redaction_version="strip-v1")
    assert len(calls) == len(rows)
    assert "private" not in canonical_json(dataset)
    with pytest.raises(ValueError):
        curate_dataset(rows, redact=lambda t: t, redaction_version="bad-v1")


def test_curation_rejects_conflicting_gold_and_provenance():
    rows = traces()
    conflict = replace(rows[0], trace_id="another", reference="conflicting")
    with pytest.raises(ValueError, match="conflicting references"):
        curate_dataset([*rows, conflict], redact=lambda t: t, redaction_version="v1")
    conflict = replace(rows[1], trace_id=rows[0].trace_id)
    with pytest.raises(ValueError, match="provenance"):
        curate_dataset([*rows, conflict], redact=lambda t: t, redaction_version="v1")


def test_curation_preserves_original_groups_even_when_redaction_renames_them():
    rows = [replace(t, group="same-conversation") for t in traces()]
    with pytest.raises(ValueError, match="two independent groups"):
        curate_dataset(
            rows, redact=lambda t: replace(t, group=t.trace_id), redaction_version="v1",
        )


def test_redaction_cannot_launder_model_output_into_reference():
    with pytest.raises(ValueError, match="model output"):
        curate_dataset(
            traces(),
            redact=lambda t: replace(t, reference=t.model_output, model_output=None),
            redaction_version="bad-policy",
        )


def test_curation_unions_duplicate_input_groups():
    rows = traces()
    rows.append(replace(rows[0], trace_id="cross-group", group=rows[1].group))
    dataset = curate_dataset(rows, redact=lambda t: t, redaction_version="v1")
    matching = [e for e in dataset.examples if e.input in (rows[0].input, rows[1].input)]
    assert matching[0].group == matching[1].group
    assert all(e.id in dataset.train_ids for e in matching) or all(
        e.id in dataset.heldout_ids for e in matching
    )


def test_dataset_rejects_split_leakage_and_exports_independent_gold(dataset):
    with pytest.raises(ValueError):
        replace(dataset, train_ids=(*dataset.train_ids, dataset.heldout_ids[0]))
    with pytest.raises(ValueError):
        replace(dataset, heldout_ids=())
    examples = tuple(replace(e, group="same") for e in dataset.examples)
    with pytest.raises(ValueError, match="group"):
        replace(dataset, examples=examples)
    for split, ids in (("train", dataset.train_ids), ("heldout", dataset.heldout_ids)):
        rows = [json.loads(line) for line in dataset_jsonl(dataset, split).splitlines()]
        assert {r["example_id"] for r in rows} == set(ids)
        assert all(r["messages"][-1]["role"] == "user" for r in rows)
        assert all("reference" in r and "provenance" in r for r in rows)


def test_exports_pass_existing_offline_finetune_validation(dataset):
    train = [json.loads(line) for line in dataset_jsonl(dataset, "train").splitlines()]
    heldout = [json.loads(line) for line in dataset_jsonl(dataset, "heldout").splitlines()]
    assert validate_rft_splits(train, heldout) == []


def test_record_collections_reject_scalar_shortcuts(agent, dataset):
    with pytest.raises(TypeError):
        replace(agent, tools={})
    with pytest.raises(TypeError):
        replace(dataset.examples[0], provenance="trace")
    with pytest.raises(TypeError):
        AcceptanceGate(required_metrics="quality")
    with pytest.raises(ValueError):
        replace(dataset, seed="Bearer private")


def test_async_repeated_evaluation_is_bounded_and_versioned(agent, dataset):
    active = peak = 0

    async def callback(snapshot, example, repetition):
        nonlocal active, peak
        assert snapshot.id == agent.id and example in dataset.examples
        active += 1
        peak = max(active, peak)
        try:
            await asyncio.sleep(0.001)
            return {"quality": 1.0, "cost": repetition * 0.001}
        finally:
            active -= 1

    evaluator = Evaluator("rubric", "v2", {"judge": "model", "temperature": 0})
    run = asyncio.run(evaluate(agent, dataset, evaluator, callback, repeats=4, concurrency=2))
    assert active == 0 and peak == 2
    assert len(run.results) == len(dataset.heldout_ids) * 4
    assert all(r.error is None and r.metrics["latency_seconds"] >= 0 for r in run.results)
    assert run.evaluator.id == evaluator.id
    assert evaluator.id != replace(evaluator, version="v3").id


def test_async_errors_timeouts_and_invalid_metrics_are_safe(agent, dataset):
    async def callback(snapshot, example, repetition):
        if repetition == 0:
            raise RuntimeError("Bearer never persist exception content")
        if repetition == 1:
            await asyncio.sleep(1)
        return {"quality": float("nan")}

    run = asyncio.run(evaluate(
        agent, dataset, Evaluator("eval", "v1", {}), callback,
        repeats=3, concurrency=2, timeout=0.01,
    ))
    assert {r.error for r in run.results} == {"callback_error", "timeout", "invalid_metrics"}
    assert "never persist" not in canonical_json(run)
    assert all(not r.metrics for r in run.results)
    assert not compare_runs(run, run).accepted


def test_async_cancellation_joins_all_workers(agent, dataset):
    async def scenario():
        active = 0
        started = asyncio.Event()

        async def callback(*args):
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.sleep(100)
            finally:
                active -= 1

        task = asyncio.create_task(evaluate(
            agent, dataset, Evaluator("e", "1", {}), callback, concurrency=2,
        ))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("metric", [float("nan"), float("inf"), -float("inf"), True, "0.8"])
def test_nonfinite_and_wrong_type_metrics_are_rejected(dataset, metric):
    with pytest.raises(ValueError):
        EvaluationResult(dataset.heldout_ids[0], 0, {"quality": metric})
    with pytest.raises(ValueError):
        AcceptanceGate(minimum_quality=metric)


def test_comparison_happy_path_and_required_metric_gates(agent, dataset):
    baseline = run_for(agent, dataset)
    candidate = run_for(with_instructions(agent, "Better"), dataset, quality=0.9)
    decision = compare_runs(baseline, candidate)
    assert decision.accepted and not decision.reasons
    assert decision.aggregates["candidate"]["quality"] == 0.9
    assert set(decision.aggregates["candidate"]) == {"quality", "latency_seconds", "cost"}
    assert not compare_runs(
        baseline, candidate, gate=AcceptanceGate(maximum_cost=0.0001)
    ).accepted
    assert not compare_runs(
        baseline, candidate, gate=AcceptanceGate(maximum_latency_seconds=0.01)
    ).accepted
    missing = replace(candidate, results=tuple(
        replace(r, metrics={"quality": 0.9}) for r in candidate.results
    ))
    assert compare_runs(baseline, missing).accepted
    assert not compare_runs(
        baseline, missing, gate=AcceptanceGate(maximum_cost=1)
    ).accepted
    assert not compare_runs(
        baseline, missing, gate=AcceptanceGate(required_metrics=("safety",))
    ).accepted


@pytest.mark.parametrize("change", [
    "dataset", "evaluator", "split", "expected", "repeats", "missing", "failed", "quality",
])
def test_comparison_fails_closed_on_incomparable_or_incomplete_evidence(agent, dataset, change):
    baseline = run_for(agent, dataset)
    candidate = baseline
    if change == "dataset":
        candidate = replace(candidate, dataset_id="0" * 64)
    elif change == "evaluator":
        candidate = replace(candidate, evaluator=Evaluator("rubric", "v2", {}))
    elif change == "split":
        candidate = replace(candidate, split="train")
    elif change == "expected":
        candidate = replace(candidate, expected_ids=dataset.train_ids, results=())
    elif change == "repeats":
        candidate = replace(candidate, repeats=2)
    elif change == "missing":
        candidate = replace(candidate, results=candidate.results[:-1])
    elif change == "failed":
        candidate = replace(candidate, results=tuple(
            replace(r, metrics={}, error="callback_error") for r in candidate.results
        ))
    elif change == "quality":
        candidate = replace(candidate, results=tuple(
            replace(r, metrics={"latency_seconds": 0.1}) for r in candidate.results
        ))
    assert not compare_runs(baseline, candidate).accepted


def test_per_example_regression_cannot_hide_behind_aggregate(agent, dataset):
    baseline = run_for(agent, dataset, quality=0.5)
    assert len(baseline.results) == 2
    candidate = replace(baseline, results=tuple(
        replace(r, metrics={"quality": 0.4 if i == 0 else 0.9})
        for i, r in enumerate(baseline.results)
    ))
    decision = compare_runs(baseline, candidate)
    assert not decision.accepted and decision.regressions == (baseline.results[0].example_id,)
    assert decision.aggregates["candidate"]["quality"] > 0.5
    assert compare_runs(
        baseline, candidate, gate=AcceptanceGate(maximum_example_regressions=1)
    ).accepted


def test_train_comparisons_are_diagnostic_unless_explicitly_requested(agent, dataset):
    run = replace(run_for(agent, dataset), split="train")
    assert not compare_runs(run, run).accepted
    assert compare_runs(run, run, gate=AcceptanceGate(require_heldout=False)).accepted


def test_finite_extremes_do_not_leak_nonfinite_comparison_values(agent, dataset):
    baseline = run_for(agent, dataset, quality=-1.7e308)
    candidate = run_for(agent, dataset, quality=1.7e308)
    decision = compare_runs(baseline, candidate)
    assert not decision.accepted
    assert "nonfinite comparison delta" in decision.reasons
    assert "Infinity" not in canonical_json(decision)


def test_candidate_stage_hash_diff_and_no_baseline_mutation(workspace, agent):
    path = workspace / "instructions.md"
    path.write_bytes(b"baseline")
    baseline_agent = with_instructions(agent, "baseline")
    baseline = stage_candidate(
        workspace, ["instructions.md"], baseline=baseline_agent, agent=baseline_agent,
    )
    path.write_bytes(b"candidate\r\n")
    changed = with_instructions(agent, "candidate\r\n")
    candidate = stage_candidate(workspace, ["instructions.md"], baseline=baseline_agent, agent=changed)
    assert baseline.files[0].content == "baseline"
    assert candidate.files[0].sha256 == hashlib.sha256(b"candidate\r\n").hexdigest()
    assert diff_candidates(baseline, candidate) == {
        "instructions.md": {"before": baseline.files[0].sha256, "after": candidate.files[0].sha256}
    }
    assert diff_candidates(candidate, candidate) == {}
    assert path.read_bytes() == b"candidate\r\n"
    with pytest.raises(ValueError):
        ConfigFile("tools.json", '{"api_key": "private"}')
    with pytest.raises(ValueError):
        replace(candidate, files=(ConfigFile("A.md", "a"), ConfigFile("a.md", "b")))
    with pytest.raises(ValueError, match="digest mismatch"):
        ConfigFile("instructions.md", "content", sha256="0" * 64)
    with pytest.raises(ValueError):
        ConfigFile("tools.json", '{"value":NaN}')
    assert candidate.to_dict()["files"][0]["sha256"] == candidate.files[0].sha256


def test_staged_bytes_must_match_the_previously_evaluated_snapshot(workspace, agent):
    stage_candidate(workspace, ["instructions.md"], baseline=agent, agent=agent)
    (workspace / "instructions.md").write_bytes(b"changed after evaluation")
    with pytest.raises(ValueError, match="not bound"):
        stage_candidate(workspace, ["instructions.md"], baseline=agent, agent=agent)
    with pytest.raises(ValueError, match="not bound"):
        Candidate(
            baseline_id=agent.id, agent_id=agent.id, agent_snapshot=agent,
            files=(ConfigFile("instructions.md", "changed after evaluation"),),
        )
    (workspace / "extra.md").write_bytes(b"uncaptured")
    with pytest.raises(ValueError, match="not bound"):
        stage_candidate(workspace, ["extra.md"], baseline=agent, agent=agent)
    contradictory = replace(agent, source_files=tuple(
        replace(f, sha256="0" * 64) if f.path == "instructions.md" else f
        for f in agent.source_files
    ))
    with pytest.raises(ValueError, match="not bound"):
        stage_candidate(workspace, ["instructions.md"], baseline=agent, agent=contradictory)


def test_rehashed_candidate_manifest_still_requires_evaluated_file_binding(workspace, agent):
    store = ArtifactStore(workspace / "evidence")
    payload = candidate_for(agent).to_dict()
    payload["files"][0]["content"] = "attacker-controlled instructions"
    payload["files"][0]["sha256"] = hashlib.sha256(
        payload["files"][0]["content"].encode("utf-8")
    ).hexdigest()
    identity = content_hash(payload)
    (store.root / f"{identity}.json").write_text(
        canonical_json({"id": identity, "record": payload}), encoding="utf-8",
    )
    with pytest.raises(IntegrityError):
        store.get(identity)
    with pytest.raises(ValueError, match="identity"):
        replace(candidate_for(agent), agent_snapshot=with_instructions(agent, "different"))


@pytest.mark.parametrize("content", [
    "client_secret: synthetic-secret\n",
    "clientSecret: synthetic-secret\n",
    '"client_secret": synthetic-secret\n',
    "'client secret': synthetic-secret\n",
    'outer: {"client_secret": "synthetic-secret"}\n',
    "outer:\n  client_secret: synthetic-secret\n",
    "value: &credential synthetic-secret\nclient_secret: *credential\n",
    '"client\\u005fsecret": synthetic-secret\n',
    'api_key: "${API_KEY}"\napi_key: synthetic-secret\n',
    "# client_secret: synthetic-secret\npublic: value\n",
])
def test_yaml_secret_keys_cannot_escape_json_equivalent_policy(content):
    with pytest.raises(ValueError):
        ConfigFile("settings.yaml", content)


@pytest.mark.parametrize("filename,content", [
    ("settings.json", '{"client_secret": "${CLIENT_SECRET}"}'),
    ("settings.yaml", "client_secret: ${CLIENT_SECRET}\n"),
    ("settings.yml", "clientSecret: '<redacted>'\n"),
    ("settings.toml", 'client_secret = "${CLIENT_SECRET}"\n'),
    ("settings.json", '{"client_secret": null}'),
    ("settings.yaml", "client_secret: null\n"),
])
def test_structured_config_allows_only_explicit_safe_secret_placeholders(filename, content):
    captured = ConfigFile(filename, content)
    assert captured.content == content
    assert captured.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("filename,content", [
    ("settings.json", '{"client_secret":"synthetic-secret","client_secret":"${CLIENT_SECRET}"}'),
    ("settings.toml", 'client_secret = "synthetic-secret"\n'),
    ("settings.yaml", 'client_secret: "${CLIENT_SECRET}-extra-secret"\n'),
    ("settings.txt", 'outer: {client_secret: synthetic-secret}\n'),
    ("settings.xml", "<client_secret>synthetic-secret</client_secret>"),
    ("settings.yaml", 'client_secret: "${CLIENT_SECRET}"\nclient_secret: "${OTHER_SECRET}"\n'),
    ("settings.yaml", "value: !!python/object:builtins.object {}\n"),
])
def test_secret_capture_cannot_hide_in_other_formats_or_partial_placeholders(filename, content):
    with pytest.raises(ValueError):
        ConfigFile(filename, content)


def test_journal_promote_then_verified_rollback(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace / "journal")
        calls = []

        async def deploy(candidate):
            calls.append(candidate.id)
            return {"remote": "receipt-never-persisted"}

        async def verify(candidate, receipt):
            assert receipt["remote"] == "receipt-never-persisted"
            return True

        baseline = candidate_for(agent)
        run = run_for(agent, dataset)
        first = await journal.promote(
            baseline, compare_runs(run, run), deploy=deploy, verify=verify, expected_revision=0,
        )
        assert first.known_good == baseline.id and first.revision == 2
        assert first.remote_state == "verified"
        changed = with_instructions(agent, "Better")
        candidate = candidate_for(changed, agent)
        second = await journal.promote(
            candidate, compare_runs(run, run_for(changed, dataset, 0.9)),
            deploy=deploy, verify=verify, expected_revision=2,
        )
        assert second.known_good == candidate.id and second.previous_good == (baseline.id,)
        third = await journal.rollback(deploy=deploy, verify=verify, expected_revision=4)
        assert third.known_good == baseline.id and third.previous_good == () and third.revision == 6
        assert calls == [baseline.id, candidate.id, baseline.id]
        assert journal.read() == third
        assert all("receipt-never-persisted" not in p.read_text() for p in journal.events.glob("*.json"))

    asyncio.run(scenario())


def test_failed_verification_preserves_good_and_can_restore_it(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace / "journal")
        deployed = []

        async def deploy(candidate):
            deployed.append(candidate.id)

        async def yes(*args):
            return True

        async def no(*args):
            return False

        run = run_for(agent, dataset)
        baseline = candidate_for(agent)
        await journal.promote(
            baseline, compare_runs(run, run), deploy=deploy, verify=yes, expected_revision=0,
        )
        changed = with_instructions(agent, "Better")
        candidate = candidate_for(changed, agent)
        with pytest.raises(DeploymentError):
            await journal.promote(
                candidate, compare_runs(run, run_for(changed, dataset, 0.9)),
                deploy=deploy, verify=no, expected_revision=2,
            )
        assert journal.read().known_good == baseline.id
        assert journal.read().last_status == "failed"
        assert journal.read().remote_state == "unknown"
        assert journal.read().revision == 4
        with pytest.raises(ConflictError, match="reconcile"):
            await journal.promote(
                candidate, compare_runs(run, run_for(changed, dataset, 0.9)),
                deploy=deploy, verify=yes, expected_revision=4,
            )
        await journal.reconcile(verify=quiescent_fake_operations, expected_revision=4)
        restored = await journal.rollback(deploy=deploy, verify=yes, expected_revision=5)
        assert restored.known_good == baseline.id and restored.previous_good == ()
        assert restored.remote_state == "verified"
        assert deployed == [baseline.id, candidate.id, baseline.id]

    asyncio.run(scenario())


def test_remote_activation_followed_by_timeout_requires_explicit_rollback(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)
        remote_active = None
        baseline = candidate_for(agent)
        changed = with_instructions(agent, "activated-before-timeout")
        candidate = candidate_for(changed, agent)

        async def deploy(target):
            nonlocal remote_active
            remote_active = target.id
            if target.id == candidate.id:
                raise TimeoutError("azd stopped waiting after remote version activation")
            return target.id

        async def verify(target, receipt):
            return remote_active == target.id == receipt

        run = run_for(agent, dataset)
        await journal.promote(
            baseline, compare_runs(run, run), deploy=deploy, verify=verify, expected_revision=0,
        )
        with pytest.raises(DeploymentError):
            await journal.promote(
                candidate, compare_runs(run, run_for(changed, dataset, 0.9)),
                deploy=deploy, verify=verify, expected_revision=2,
            )
        assert remote_active == candidate.id
        assert journal.read().known_good == baseline.id
        assert journal.read().remote_state == "unknown"
        with pytest.raises(ConflictError, match="reconcile"):
            await journal.rollback(deploy=deploy, verify=verify, expected_revision=4)
        await journal.reconcile(verify=quiescent_fake_operations, expected_revision=4)
        recovered = await journal.rollback(deploy=deploy, verify=verify, expected_revision=5)
        assert remote_active == baseline.id
        assert recovered.known_good == baseline.id and recovered.remote_state == "verified"

    asyncio.run(scenario())


def test_late_original_write_blocks_rollback_until_terminal_reconciliation(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)
        baseline = candidate_for(agent)
        changed = with_instructions(agent, "late candidate")
        candidate = candidate_for(changed, agent)
        remote = None
        release_old_write = asyncio.Event()
        pending = None

        async def late_write():
            nonlocal remote
            await release_old_write.wait()
            remote = candidate.id

        async def deploy(target):
            nonlocal pending, remote
            if target.id == candidate.id:
                pending = asyncio.create_task(late_write())
                raise TimeoutError("stopped waiting; service-side write remains pending")
            remote = target.id
            return remote

        async def verify(target, receipt):
            return remote == target.id == receipt

        async def verify_terminal_operations(state):
            return {
                "no_outstanding_writes": pending.done(),
                "evidence": "owned-operation-1 terminal status checked",
            }

        run = run_for(agent, dataset)
        await journal.promote(
            baseline, compare_runs(run, run), deploy=deploy, verify=verify, expected_revision=0,
        )
        try:
            with pytest.raises(DeploymentError):
                await journal.promote(
                    candidate, compare_runs(run, run_for(changed, dataset, 0.9)),
                    deploy=deploy, verify=verify, expected_revision=2,
                )
            assert journal.read().pending_cleanup
            with pytest.raises(ConflictError, match="reconcile"):
                await journal.rollback(deploy=deploy, verify=verify, expected_revision=4)
            with pytest.raises(ValueError, match="outstanding"):
                await journal.reconcile(verify=verify_terminal_operations, expected_revision=4)
            assert journal.read().revision == 4 and remote == baseline.id
            release_old_write.set()
            await pending
            assert remote == candidate.id
            reconciled = await journal.reconcile(
                verify=verify_terminal_operations, expected_revision=4,
            )
            assert not reconciled.pending_cleanup and reconciled.remote_state == "unknown"
            restored = await journal.rollback(deploy=deploy, verify=verify, expected_revision=5)
            assert restored.remote_state == "verified" and remote == baseline.id
            assert pending.done()
        finally:
            release_old_write.set()
            if pending is not None:
                await pending

    asyncio.run(scenario())


@pytest.mark.parametrize("evidence", [
    True,
    {"no_outstanding_writes": 1, "evidence": "not literal boolean"},
    {"no_outstanding_writes": False, "evidence": "operation still running"},
    {"no_outstanding_writes": True, "evidence": ""},
    {"no_outstanding_writes": True, "evidence": "Bearer private"},
    {"no_outstanding_writes": True, "evidence": "claimed", "status": "failed"},
])
def test_reconciliation_requires_explicit_nonsecret_terminal_evidence(workspace, agent, dataset, evidence):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            raise TimeoutError("remote write status unknown")

        async def verify(*args):
            return True

        async def reconcile(*args):
            return evidence

        run = run_for(agent, dataset)
        with pytest.raises(DeploymentError):
            await journal.promote(
                candidate_for(agent), compare_runs(run, run), deploy=deploy, verify=verify,
                expected_revision=0,
            )
        with pytest.raises((ValueError, TypeError)):
            await journal.reconcile(verify=reconcile, expected_revision=2)
        state = journal.read()
        assert state.pending_cleanup and state.remote_state == "unknown" and state.revision == 2

    asyncio.run(scenario())


def test_handoff_concurrent_updates_and_stale_revisions_fail_before_deploy(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace / "journal")
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def deploy(candidate):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()

        async def verify(*args):
            return True

        candidate = candidate_for(agent)
        run = run_for(agent, dataset)
        decision = compare_runs(run, run)
        task = asyncio.create_task(journal.promote(
            candidate, decision, deploy=deploy, verify=verify, expected_revision=0,
        ))
        await started.wait()
        assert journal.read().known_good is None and journal.read().last_status == "started"
        assert journal.read().remote_state == "unknown"
        with pytest.raises(ConflictError):
            await DeploymentJournal(journal.root).promote(
                candidate, decision, deploy=deploy, verify=verify, expected_revision=0,
            )
        release.set()
        await task
        with pytest.raises(ConflictError):
            await journal.promote(
                candidate, decision, deploy=deploy, verify=verify, expected_revision=0,
            )
        assert calls == 1

    asyncio.run(scenario())


def test_rejected_or_mismatched_decision_never_deploys(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def forbidden(*args):
            pytest.fail("callback must not run")

        run = run_for(agent, dataset)
        decision = compare_runs(run, run_for(agent, dataset, quality=0.1))
        with pytest.raises(ValueError):
            await journal.promote(
                candidate_for(agent), decision, deploy=forbidden, verify=forbidden,
                expected_revision=0,
            )
        candidate = candidate_for(with_instructions(agent, "unevaluated"), agent)
        with pytest.raises(ValueError):
            await journal.promote(
                candidate, compare_runs(run, run), deploy=forbidden, verify=forbidden,
                expected_revision=0,
            )
        assert journal.read().revision == 0

    asyncio.run(scenario())


def test_handoff_cancellation_is_journaled_and_releases_lock(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)
        started = asyncio.Event()

        async def deploy(*args):
            started.set()
            await asyncio.sleep(100)

        async def verify(*args):
            return True

        run = run_for(agent, dataset)
        task = asyncio.create_task(journal.promote(
            candidate_for(agent), compare_runs(run, run),
            deploy=deploy, verify=verify, expected_revision=0,
        ))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal.read().known_good is None
        assert journal.read().last_status == "cancelled"
        assert journal.read().remote_state == "unknown"
        assert not (workspace / ".handoff.lock").exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("verification", [False, 1, "yes", None, {"status": "failed"}, []])
def test_verification_requires_literal_true(workspace, agent, dataset, verification):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            return "receipt"

        async def verify(*args):
            return verification

        run = run_for(agent, dataset)
        with pytest.raises(DeploymentError):
            await journal.promote(
                candidate_for(agent), compare_runs(run, run),
                deploy=deploy, verify=verify, expected_revision=0,
            )
        assert journal.read().known_good is None
        assert journal.read().last_status == "failed"

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["failed", "failure", "error", "cancelled", "canceled", " FAILED "])
def test_explicit_failed_deployment_receipt_never_reaches_verification(
    workspace, agent, dataset, status,
):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            return {"status": status}

        async def verify(*args):
            pytest.fail("explicit deployment failure must not reach verification")

        run = run_for(agent, dataset)
        with pytest.raises(DeploymentError):
            await journal.promote(
                candidate_for(agent), compare_runs(run, run), deploy=deploy, verify=verify,
                expected_revision=0,
            )
        assert journal.read().known_good is None
        assert journal.read().last_status == "failed"

    asyncio.run(scenario())


def test_duplicate_sample_repeat_pairs_are_rejected(agent, dataset):
    run = run_for(agent, dataset)
    with pytest.raises(ValueError, match="duplicate"):
        replace(run, results=(*run.results, run.results[0]))
    with pytest.raises(ValueError, match="unique"):
        replace(run, expected_ids=(*run.expected_ids, run.expected_ids[0]))


def test_deployment_exception_is_sanitized_and_verifier_is_not_called(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            raise RuntimeError("Bearer private")

        async def verify(*args):
            pytest.fail("verify must not run after failed deployment")

        run = run_for(agent, dataset)
        with pytest.raises(DeploymentError, match="known-good is unchanged"):
            await journal.promote(
                candidate_for(agent), compare_runs(run, run),
                deploy=deploy, verify=verify, expected_revision=0,
            )
        assert journal.read().known_good is None
        assert all("private" not in p.read_text() for p in journal.events.glob("*.json"))

    asyncio.run(scenario())


def test_failed_rollback_preserves_known_good_history(workspace, agent, dataset):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            return "receipt"

        async def yes(*args):
            return True

        async def no(*args):
            return False

        run = run_for(agent, dataset)
        first = candidate_for(agent)
        await journal.promote(
            first, compare_runs(run, run), deploy=deploy, verify=yes, expected_revision=0,
        )
        changed = with_instructions(agent, "Better")
        second = candidate_for(changed, agent)
        await journal.promote(
            second, compare_runs(run, run_for(changed, dataset, 0.9)),
            deploy=deploy, verify=yes, expected_revision=2,
        )
        with pytest.raises(DeploymentError):
            await journal.rollback(deploy=deploy, verify=no, expected_revision=4)
        state = journal.read()
        assert state.known_good == second.id and state.previous_good == (first.id,)
        await journal.reconcile(verify=quiescent_fake_operations, expected_revision=6)
        recovered = await journal.rollback(deploy=deploy, verify=yes, expected_revision=7)
        assert recovered.known_good == second.id and recovered.previous_good == (first.id,)

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ["hash", "gap", "candidate"])
def test_journal_tampering_fails_closed(workspace, agent, dataset, tamper):
    async def scenario():
        journal = DeploymentJournal(workspace)

        async def deploy(*args):
            return None

        async def verify(*args):
            return True

        run = run_for(agent, dataset)
        candidate = candidate_for(agent)
        await journal.promote(
            candidate, compare_runs(run, run), deploy=deploy, verify=verify, expected_revision=0,
        )
        path = journal.events / "000000000001.json"
        if tamper == "hash":
            payload = json.loads(path.read_text())
            payload["event"]["status"] = "verified"
            path.write_text(json.dumps(payload))
        elif tamper == "gap":
            path.unlink()
        else:
            artifact = journal.artifacts.root / f"{candidate.id}.json"
            payload = json.loads(artifact.read_text())
            payload["record"]["files"][0]["content"] = "tampered"
            artifact.write_text(json.dumps(payload))
        with pytest.raises(IntegrityError):
            journal.read()

    asyncio.run(scenario())


def test_symlinked_artifacts_and_sources_are_rejected(workspace, agent):
    target = workspace / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = workspace / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("OS does not permit unprivileged symlinks")
    with pytest.raises(ValueError, match="symlink"):
        create_agent_snapshot(
            workspace, source_files=["link.py"], dependencies={},
            model={"deployment": "x"}, instructions="safe",
        )
    store = ArtifactStore(workspace / "store")
    (store.root / f"{agent.id}.json").symlink_to(target)
    with pytest.raises(IntegrityError):
        store.get(agent.id)


@pytest.fixture(scope="module")
def lifecycle_contract():
    path = (
        Path(__file__).resolve().parents[3]
        / "spec" / "conformance" / "lifecycle" / "records.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_lifecycle_conformance_hash_vectors_and_record_shapes(
    workspace, agent, dataset, lifecycle_contract,
):
    contract = lifecycle_contract
    assert contract["schema_version"] == SCHEMA_VERSION
    assert tuple(contract["dataset"]["reference_origins"]) == REFERENCE_ORIGINS
    for vector in contract["hash_vectors"]:
        assert canonical_json(vector["value"]) == vector["canonical"]
        assert content_hash(vector["value"]) == vector["sha256"]
        assert hashlib.sha256(vector["canonical"].encode("utf-8")).hexdigest() == vector["sha256"]
        if "kind" in vector["value"]:
            assert record_from_dict(vector["value"]).id == vector["sha256"]
    run = run_for(agent, dataset)
    records = (agent, dataset, run, candidate_for(agent), compare_runs(run, run))
    assert {r.kind for r in records} == set(contract["record_fields"])
    store = ArtifactStore(workspace)
    for record in records:
        assert set(record.to_dict()) == set(contract["record_fields"][record.kind])
        identity = store.put(record)
        envelope = json.loads((workspace / f"{identity}.json").read_text())
        assert set(envelope) == set(contract["artifact_envelope_fields"])
        assert content_hash(envelope["record"]) == identity
        assert store.get(identity).schema_version == contract["schema_version"]
    nested = {
        "source_file": agent.to_dict()["source_files"][0],
        "config_file": candidate_for(agent).to_dict()["files"][0],
        "example": dataset.to_dict()["examples"][0],
        "evaluator": run.to_dict()["evaluator"],
        "evaluation_result": run.to_dict()["results"][0],
    }
    assert {key: set(value) for key, value in nested.items()} == {
        key: set(value) for key, value in contract["nested_fields"].items()
    }


def test_lifecycle_conformance_errors_and_fail_closed_gate(agent, dataset, lifecycle_contract):
    contract = lifecycle_contract
    assert json.loads(canonical_json(AcceptanceGate())) == contract["acceptance"]["default_gate"]
    for error in contract["evaluation"]["error_codes"]:
        result = EvaluationResult(dataset.heldout_ids[0], 0, {}, error)
        assert result.error == error
        if error is not None:
            assert dict(result.metrics) == contract["evaluation"]["failure_metrics"]
    with pytest.raises(ValueError):
        EvaluationResult(dataset.heldout_ids[0], 0, {}, "unrecognized")
    run = run_for(agent, dataset)
    accepted = compare_runs(run, run)
    assert set(accepted.aggregates) == set(contract["acceptance"]["aggregates"])
    assert accepted.accepted == (not accepted.reasons)
    incomplete = replace(run, results=())
    rejected = compare_runs(run, incomplete)
    assert not rejected.accepted and rejected.reasons


def test_lifecycle_conformance_journal_statuses(workspace, agent, dataset, lifecycle_contract):
    contract = lifecycle_contract["deployment"]
    assert contract["journal_schema_version"] == SCHEMA_VERSION
    transitions = {item["status"]: item["advances_known_good"] for item in contract["transitions"]}
    observed = set()

    async def scenario(status):
        journal = DeploymentJournal(workspace / status)
        candidate = candidate_for(agent)
        run = run_for(agent, dataset)

        async def deploy(value):
            started = journal.read()
            assert started.last_status == "started"
            assert not transitions["started"] and started.known_good is None
            if status == "cancelled":
                raise asyncio.CancelledError()
            return value.id

        async def verify(*args):
            return status == "verified"

        try:
            await journal.promote(
                candidate, compare_runs(run, run), deploy=deploy, verify=verify,
                expected_revision=0,
            )
        except (DeploymentError, asyncio.CancelledError):
            assert status != "verified"
        state = journal.read()
        assert state.last_status == status
        assert (state.known_good == candidate.id) == transitions[status]
        assert state.remote_state == ("verified" if status == "verified" else "unknown")
        assert state.remote_state in contract["remote_states"]
        assert set(json.loads(canonical_json(state))) == set(contract["state_fields"])
        if status != "verified":
            reconciled = await journal.reconcile(
                verify=quiescent_fake_operations, expected_revision=state.revision,
            )
            assert not reconciled.pending_cleanup and reconciled.remote_state == "unknown"
        for path in journal.events.glob("*.json"):
            envelope = json.loads(path.read_text())
            assert set(envelope) == set(contract["event_envelope_fields"])
            event = envelope["event"]
            assert set(event) == set(contract["event_fields"])
            assert event["action"] in contract["actions"]
            assert event["schema_version"] == contract["journal_schema_version"]
            observed.add(event["status"])

    for status in ("verified", "failed", "cancelled"):
        asyncio.run(scenario(status))
    assert observed == set(contract["statuses"])

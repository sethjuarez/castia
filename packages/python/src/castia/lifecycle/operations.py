"""Offline snapshot, reviewed curation, evaluation and acceptance operations.

Evaluation callbacks own model/judge invocation. This module never discovers a
service, reads credentials, or submits fine-tuning jobs. Callback exceptions are
recorded as safe error codes rather than potentially secret-bearing messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from castia.lifecycle.records import (
    REFERENCE_ORIGINS,
    AgentSnapshot,
    Candidate,
    ConfigFile,
    DatasetSnapshot,
    Decision,
    EvaluationResult,
    Evaluator,
    Example,
    FileDigest,
    LifecycleValidationError,
    Run,
    canonical_json,
    content_hash,
    integer,
    number,
    plain,
    safe_path,
    text,
)


def _file(root: Path, relative: str) -> Path:
    normalized = safe_path(relative)
    path = root.joinpath(*normalized.split("/"))
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("file escapes the explicit root")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("symlinked evidence paths are not allowed")
        current = current.parent
    if path.name.lower().startswith(".env") or path.suffix.lower() in (".pem", ".key", ".pfx"):
        raise ValueError("credential files are not evidence")
    if not path.is_file():
        raise ValueError("explicit evidence file does not exist")
    return path


def create_agent_snapshot(
    root: str | Path,
    *,
    source_files: Sequence[str],
    dependencies: Mapping[str, str],
    model: Mapping[str, Any],
    instructions: str,
    tools: Sequence[Mapping[str, Any]] = (),
) -> AgentSnapshot:
    """Hash only named source files; versions/configuration are caller-supplied.

    Source bytes are not persisted. Dependency values should be resolved versions
    or lockfile digests, never package-index URLs containing credentials.
    """
    base = Path(root).resolve()
    sources = []
    for relative in source_files:
        raw = _file(base, relative).read_bytes()
        sources.append(FileDigest(safe_path(relative), hashlib.sha256(raw).hexdigest(), len(raw)))
    if not sources:
        raise ValueError("at least one source file is required")
    return AgentSnapshot(
        source_files=tuple(sources), dependencies=dependencies, model=model,
        instructions=instructions, tools=tuple(tools),
    )


@dataclass(frozen=True)
class ReviewedTrace:
    """An explicitly reviewed trace, before caller-owned redaction.

    ``reference`` must come from an independent human/authoritative source or a
    deterministic rule/test computation, never from ``model_output``. Synthetic
    deterministic fixtures establish control-flow behavior, not live quality.
    ``group`` identifies a conversation/user/document unit
    that must not appear in both splits. Review/redaction cannot be inferred.
    """

    trace_id: str
    input: str
    reference: str
    reviewer: str
    group: str
    approved: bool
    reference_origin: str = "human"
    model_output: str | None = None


def _reviewed(trace: ReviewedTrace) -> None:
    if not isinstance(trace, ReviewedTrace) or trace.approved is not True:
        raise LifecycleValidationError("review_approval")
    if trace.reference_origin not in REFERENCE_ORIGINS:
        raise LifecycleValidationError("reference_origin")
    for name in ("trace_id", "input", "reference", "reviewer", "group"):
        text(getattr(trace, name), name)
    if trace.model_output is not None and trace.reference.strip() == trace.model_output.strip():
        raise ValueError("model output cannot be reused as gold")


def curate_dataset(
    traces: Iterable[ReviewedTrace],
    *,
    redact: Callable[[ReviewedTrace], ReviewedTrace],
    redaction_version: str,
    heldout_fraction: float = 0.2,
    seed: str = "castia",
) -> DatasetSnapshot:
    """Review → redact → deduplicate → group split, deterministically.

    Raw traces and model outputs are never retained. Duplicate inputs merge
    provenance, and their groups are unioned before splitting to prevent leakage.
    Conflicting references fail rather than silently selecting a label.
    """
    number(heldout_fraction, "heldout_fraction")
    if not 0 < heldout_fraction < 1:
        raise ValueError("heldout_fraction must be between zero and one")
    text(seed, "seed")
    text(redaction_version, "redaction_version")
    rows: dict[str, list[ReviewedTrace]] = defaultdict(list)
    parents: dict[str, str] = {}
    seen_traces: dict[str, str] = {}
    original_groups: dict[str, str] = {}

    def find(group: str) -> str:
        parents.setdefault(group, group)
        while parents[group] != group:
            group = parents[group]
        return group

    def union(a: str, b: str) -> None:
        left, right = sorted((find(a), find(b)))
        parents[right] = left

    for raw in traces:
        _reviewed(raw)
        cleaned = redact(raw)
        _reviewed(cleaned)
        if raw.model_output is not None and cleaned.reference.strip() == raw.model_output.strip():
            raise ValueError("redaction cannot turn model output into gold")
        # Redaction may remove sensitive text, but cannot manufacture approval.
        if cleaned.reference_origin != raw.reference_origin:
            raise ValueError("redaction cannot change the reference's authority")
        fingerprint = content_hash({"input": cleaned.input})
        if raw.group in original_groups:
            union(original_groups[raw.group], cleaned.group)
        else:
            original_groups[raw.group] = cleaned.group
        if cleaned.trace_id in seen_traces and seen_traces[cleaned.trace_id] != fingerprint:
            raise ValueError("trace provenance reused for conflicting inputs")
        seen_traces[cleaned.trace_id] = fingerprint
        previous = rows[fingerprint]
        if previous:
            if previous[0].reference != cleaned.reference:
                raise ValueError("duplicate input has conflicting references")
            union(previous[0].group, cleaned.group)
        find(cleaned.group)
        rows[fingerprint].append(cleaned)

    examples = tuple(
        Example(
            input=group[0].input, reference=group[0].reference,
            group=content_hash({"group": find(group[0].group)}),
            provenance=tuple(t.trace_id for t in group),
            reviewers=tuple(t.reviewer for t in group),
            reference_origins=tuple(t.reference_origin for t in group),
        )
        for group in rows.values()
    )
    groups = sorted(
        {e.group for e in examples}, key=lambda g: content_hash({"seed": seed, "group": g})
    )
    if len(groups) < 2:
        raise ValueError("curation requires at least two independent groups")
    heldout_count = max(1, min(len(groups) - 1, math.ceil(len(groups) * heldout_fraction)))
    heldout_groups = set(groups[:heldout_count])
    return DatasetSnapshot(
        examples=examples,
        train_ids=tuple(e.id for e in examples if e.group not in heldout_groups),
        heldout_ids=tuple(e.id for e in examples if e.group in heldout_groups),
        seed=seed, redaction_version=redaction_version,
    )


EvaluationCallback = Callable[
    [AgentSnapshot, Example, int], Awaitable[Mapping[str, float]]
]


async def evaluate(
    agent: AgentSnapshot,
    dataset: DatasetSnapshot,
    evaluator: Evaluator,
    callback: EvaluationCallback,
    *,
    split: str = "heldout",
    repeats: int = 1,
    concurrency: int = 4,
    timeout: float = 30,
) -> Run:
    """Run bounded asynchronous workers; cancellation propagates and joins workers.

    Callbacks must cooperate with asyncio cancellation. Metrics use higher-is-
    better ``quality``, nonnegative ``cost``, and ``latency_seconds``; measured
    callback wall time supplies latency when the callback omits it.

    A timeout is failed evidence, not proof that a remote job was cancelled.
    Callbacks that launch background service jobs must handle cancellation,
    cancel/poll only their owned jobs, and persist terminal status or cancellation
    evidence before completing cleanup. This runner has no remote job ownership
    and never assumes that cancelling an asyncio task stops a service-side job.
    """
    integer(repeats, "repeats", minimum=1)
    integer(concurrency, "concurrency", minimum=1)
    number(timeout, "timeout", minimum=0)
    if timeout == 0:
        raise ValueError("timeout must be positive")
    if split not in ("train", "heldout"):
        raise ValueError("split must be train or heldout")
    if not isinstance(agent, AgentSnapshot) or not isinstance(dataset, DatasetSnapshot):
        raise TypeError("evaluation needs agent and dataset snapshots")
    if not isinstance(evaluator, Evaluator):
        raise TypeError("evaluation needs a versioned evaluator")
    ids = dataset.heldout_ids if split == "heldout" else dataset.train_ids
    examples = {e.id: e for e in dataset.examples}
    jobs = iter((example_id, repetition) for example_id in ids for repetition in range(repeats))
    results: list[EvaluationResult] = []

    async def worker() -> None:
        for example_id, repetition in jobs:
            start = time.perf_counter()
            try:
                metrics = await asyncio.wait_for(
                    callback(agent, examples[example_id], repetition), timeout=timeout
                )
            except TimeoutError:
                result = EvaluationResult(example_id, repetition, {}, "timeout")
            except Exception:  # noqa: BLE001 -- callback failures are isolated and sanitized
                result = EvaluationResult(example_id, repetition, {}, "callback_error")
            else:
                try:
                    if not isinstance(metrics, Mapping):
                        raise TypeError("callback must return metric mapping")
                    values = dict(metrics)
                    values.setdefault("latency_seconds", time.perf_counter() - start)
                    result = EvaluationResult(example_id, repetition, values)
                except (ValueError, TypeError):
                    result = EvaluationResult(example_id, repetition, {}, "invalid_metrics")
            results.append(result)

    tasks = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(ids) * repeats))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return Run(
        agent_id=agent.id, dataset_id=dataset.id, evaluator=evaluator, split=split,
        expected_ids=ids, repeats=repeats, results=tuple(results),
    )


@dataclass(frozen=True)
class AcceptanceGate:
    minimum_quality: float = 0
    maximum_quality_drop: float = 0
    maximum_example_regressions: int = 0
    maximum_latency_seconds: float | None = None
    maximum_cost: float | None = None
    required_metrics: tuple[str, ...] = ("quality",)
    require_heldout: bool = True

    def __post_init__(self) -> None:
        number(self.minimum_quality, "minimum_quality")
        number(self.maximum_quality_drop, "maximum_quality_drop", minimum=0)
        integer(self.maximum_example_regressions, "maximum_example_regressions")
        if type(self.require_heldout) is not bool:
            raise ValueError("require_heldout must be a boolean")
        for name in ("maximum_latency_seconds", "maximum_cost"):
            value = getattr(self, name)
            if value is not None:
                number(value, name, minimum=0)
        if not isinstance(self.required_metrics, (tuple, list)):
            raise TypeError("required_metrics must be an array")
        metrics = tuple(sorted(set(self.required_metrics) | {"quality"}))
        for metric in metrics:
            text(metric, "required metric")
        object.__setattr__(self, "required_metrics", metrics)


_DEFAULT_GATE = AcceptanceGate()


def compare_runs(
    baseline: Run, candidate: Run, *, gate: AcceptanceGate = _DEFAULT_GATE
) -> Decision:
    """Compare identical dataset/evaluator/coverage; reject incomplete evidence.

    Aggregates exist only when *all* expected repetitions supply that metric.
    Quality regression is measured per example after averaging repetitions.
    """
    reasons: list[str] = []
    if gate.require_heldout and (baseline.split != "heldout" or candidate.split != "heldout"):
        reasons.append("acceptance requires heldout evidence")
    if (baseline.dataset_id, baseline.evaluator.id, baseline.split) != (
        candidate.dataset_id, candidate.evaluator.id, candidate.split
    ):
        reasons.append("dataset, evaluator, and split must match")
    if (baseline.expected_ids, baseline.repeats) != (candidate.expected_ids, candidate.repeats):
        reasons.append("expected coverage and repeats must match")
    required = set(gate.required_metrics)
    if gate.maximum_cost is not None:
        required.add("cost")
    if gate.maximum_latency_seconds is not None:
        required.add("latency_seconds")
    aggregates: dict[str, Any] = {}
    per_example: list[dict[str, float]] = []
    for label, run in (("baseline", baseline), ("candidate", candidate)):
        expected = {(e, i) for e in run.expected_ids for i in range(run.repeats)}
        actual = {(r.example_id, r.repetition) for r in run.results}
        complete = actual == expected and all(r.error is None for r in run.results)
        if not complete:
            reasons.append(f"{label}: incomplete or failed coverage")
        available = set.intersection(*(set(r.metrics) for r in run.results)) if run.results else set()
        if not required <= available:
            reasons.append(f"{label}: missing required metrics")
        means: dict[str, float] = {}
        if complete:
            for metric in sorted(available):
                # Dividing before summation avoids overflow for large finite inputs.
                try:
                    mean = math.fsum(r.metrics[metric] / len(run.results) for r in run.results)
                except OverflowError:
                    reasons.append(f"{label}: nonfinite aggregate")
                    continue
                if not math.isfinite(mean):
                    reasons.append(f"{label}: nonfinite aggregate")
                else:
                    means[metric] = mean
        aggregates[label] = means
        quality: dict[str, float] = {}
        if complete and "quality" in available:
            for example_id in run.expected_ids:
                try:
                    quality[example_id] = math.fsum(
                        r.metrics["quality"] / run.repeats
                        for r in run.results if r.example_id == example_id
                    )
                except OverflowError:
                    reasons.append(f"{label}: nonfinite example aggregate")
        per_example.append(quality)
    regressions = tuple(
        example_id for example_id in sorted(per_example[0].keys() & per_example[1].keys())
        if per_example[1][example_id] < per_example[0][example_id]
    )
    left, right = aggregates["baseline"], aggregates["candidate"]
    deltas = {}
    for metric in sorted(left.keys() & right.keys()):
        delta = right[metric] - left[metric]
        if not math.isfinite(delta):
            reasons.append("nonfinite comparison delta")
        else:
            deltas[metric] = delta
    aggregates["delta"] = deltas
    if "quality" in right:
        if right["quality"] < gate.minimum_quality:
            reasons.append("candidate quality below minimum")
        if "quality" in left and right["quality"] < left["quality"] - gate.maximum_quality_drop:
            reasons.append("aggregate quality regressed beyond tolerance")
    if len(regressions) > gate.maximum_example_regressions:
        reasons.append("per-example regression limit exceeded")
    for metric, limit in (
        ("latency_seconds", gate.maximum_latency_seconds), ("cost", gate.maximum_cost)
    ):
        if limit is not None and metric in right and right[metric] > limit:
            reasons.append(f"candidate {metric} exceeds limit")
    return Decision(
        baseline_run_id=baseline.id, candidate_run_id=candidate.id,
        baseline_agent_id=baseline.agent_id, candidate_agent_id=candidate.agent_id,
        accepted=not reasons, reasons=tuple(reasons), aggregates=aggregates,
        regressions=regressions, gate=plain(gate),
    )


def stage_candidate(
    root: str | Path,
    files: Sequence[str],
    *,
    baseline: AgentSnapshot,
    agent: AgentSnapshot,
) -> Candidate:
    """Capture explicit config bytes, never modify the working/deployed baseline.

    Persist with ``ArtifactStore.put``; inspect ``diff_candidates`` before handing
    the candidate to an explicit deployment callback. Every staged file must have
    been included in ``agent.source_files`` with these exact bytes before that
    agent snapshot was evaluated. Capturing changed bytes after evaluation fails.
    """
    base = Path(root).resolve()
    configs = tuple(ConfigFile(safe_path(f), _file(base, f).read_bytes().decode("utf-8")) for f in files)
    return Candidate(
        baseline_id=baseline.id, agent_id=agent.id, files=configs, agent_snapshot=agent,
    )


def diff_candidates(baseline: Candidate, candidate: Candidate) -> dict[str, dict[str, str | None]]:
    """Return sorted changed paths and their old/new SHA-256 digests."""
    left = {f.path: f.sha256 for f in baseline.files}
    right = {f.path: f.sha256 for f in candidate.files}
    return {
        path: {"before": left.get(path), "after": right.get(path)}
        for path in sorted(left.keys() | right.keys()) if left.get(path) != right.get(path)
    }


def dataset_jsonl(dataset: DatasetSnapshot, split: str) -> str:
    """Export reviewed gold with a final user turn, suitable for offline RFT checks.

    This is a local format bridge only; it never prepares or submits a job.
    """
    if split not in ("train", "heldout"):
        raise ValueError("split must be train or heldout")
    ids = dataset.train_ids if split == "train" else dataset.heldout_ids
    return "".join(
        canonical_json({
            "messages": [{"role": "user", "content": e.input}],
            "reference": e.reference, "example_id": e.id,
            "provenance": e.provenance, "reference_origins": e.reference_origins,
        }) + "\n"
        for e in dataset.examples if e.id in ids
    )

"""Repeated, resumable eval runs with acceptance gates and spread reporting.

One eval run is a sample, not a measurement: grader noise between identical
runs can exceed the gap you're trying to see. :func:`run_harness` submits
``runs`` repeats of the same eval (at most ``max_in_flight`` at once), keeps
every decision in a JSON state file so an interrupted harness resumes instead
of resubmitting, and **rejects** runs that didn't finish or scored too few
rows. :func:`format_report` reports each criterion as ``mean [min, max]`` with
the run count and row count beside it.

The service is reached through :class:`EvalBackend`, so the harness doesn't
care whether runs come from the Foundry REST API (:class:`FoundryRestBackend`,
the default) or a future azd-based backend.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from castia.evaluation.foundry import run_name

STATE_VERSION = 1
TERMINAL_OK = "completed"
TERMINAL_BAD = frozenset({"failed", "canceled", "cancelled", "expired"})
SMALL_N = 50


@dataclass
class RunSnapshot:
    """What the harness needs from one polled run."""

    status: str
    total: int = 0
    errored: int = 0
    passed: int = 0
    failed: int = 0
    #: testing-criterion name -> [passed, failed]
    criteria: dict[str, list[int]] = field(default_factory=dict)
    report_url: str | None = None

    @property
    def scored(self) -> int:
        return max(self.total - self.errored, 0)


class EvalBackend(Protocol):
    """Submits and polls eval runs. Implement this for a new backend."""

    def submit(self, name: str, metadata: dict[str, str]) -> str:
        """Start one run named ``name`` and return its id."""

    def poll(self, run_id: str) -> RunSnapshot:
        """Return the current state of run ``run_id``."""


def snapshot_from_run(data: dict[str, Any]) -> RunSnapshot:
    """Parse an OpenAI-compatible eval run object."""
    counts = data.get("result_counts") or {}
    criteria: dict[str, list[int]] = {}
    for entry in data.get("per_testing_criteria_results") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("testing_criteria") or entry.get("name") or "")
        criteria[name] = [int(entry.get("passed") or 0), int(entry.get("failed") or 0)]
    return RunSnapshot(
        status=str(data.get("status") or "unknown").lower(),
        total=int(counts.get("total") or 0),
        errored=int(counts.get("errored") or 0),
        passed=int(counts.get("passed") or 0),
        failed=int(counts.get("failed") or 0),
        criteria=criteria,
        report_url=data.get("report_url"),
    )


class FoundryRestBackend:
    """Re-submit a template run's data source as new runs of the same eval.

    The template is any earlier run of ``eval_id`` (for example the one
    ``eval run`` created). Reusing its ``data_source`` keeps the target agent,
    dataset and testing criteria identical across repeats.
    """

    def __init__(self, client, eval_id: str, template_run_id: str) -> None:
        self._client = client
        self._eval_id = eval_id
        self._template_id = template_run_id
        self._template: dict[str, Any] | None = None

    def template(self) -> dict[str, Any]:
        if self._template is None:
            self._template = self._client.get_eval_run(self._eval_id, self._template_id)
            if not self._template.get("data_source"):
                raise RuntimeError(f"template run {self._template_id} has no data_source")
        return self._template

    def default_rows(self) -> int | None:
        total = (self.template().get("result_counts") or {}).get("total")
        return int(total) if total else None

    def submit(self, name: str, metadata: dict[str, str]) -> str:
        body = {"name": name, "data_source": self.template()["data_source"], "metadata": metadata}
        data = self._client.create_eval_run(self._eval_id, body)
        run_id = data.get("id")
        if not run_id:
            raise RuntimeError(f"run submit returned no id: {data}")
        return str(run_id)

    def poll(self, run_id: str) -> RunSnapshot:
        return snapshot_from_run(self._client.get_eval_run(self._eval_id, run_id))

    def tool_records(self, run_id: str) -> list[dict[str, Any]]:
        """Tool-call records from the run's per-row samples."""
        items = self._client.list_eval_run_output_items(self._eval_id, run_id)
        return tool_records_from_output_items(items)


def tool_records_from_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tool calls found in eval output items' ``sample.output``.

    Responses-style tool items (``mcp_call``, ``file_search_call``, ...) go
    through ``castia.observe.genai.server_tool_calls``; ``castia.tool_capture``
    documents (dicts with a ``tool_calls`` list) pass through unchanged.
    """
    from castia.observe.genai import server_tool_calls

    records: list[dict[str, Any]] = []
    for item in items:
        sample = item.get("sample") if isinstance(item, dict) else None
        output = sample.get("output") if isinstance(sample, dict) else None
        if not isinstance(output, list):
            continue
        records.extend(server_tool_calls(output))
        records.extend(o for o in output if isinstance(o, dict) and isinstance(o.get("tool_calls"), list))
    return records


@dataclass(frozen=True)
class HarnessConfig:
    rung: str
    rows: int
    runs: int
    max_in_flight: int = 3
    min_scored: float = 0.95
    max_errors: int | None = 10
    poll_interval: float = 30.0
    timeout: float = 4 * 3600.0

    def __post_init__(self) -> None:
        if self.runs < 1:
            raise ValueError("runs must be >= 1")
        if self.max_in_flight < 1:
            raise ValueError("max_in_flight must be >= 1")
        if not 0.0 < self.min_scored <= 1.0:
            raise ValueError("min_scored must be in (0, 1]")


def judge(snapshot: RunSnapshot, config: HarnessConfig) -> str | None:
    """Return a rejection reason for a terminal run, or None to accept it."""
    if snapshot.status != TERMINAL_OK:
        return f"status {snapshot.status}"
    if snapshot.total <= 0:
        return "no rows scored"
    fraction = snapshot.scored / snapshot.total
    if fraction < config.min_scored:
        return (f"incomplete: scored {snapshot.scored}/{snapshot.total} "
                f"({fraction:.1%} < {config.min_scored:.0%})")
    if config.max_errors is not None and snapshot.errored > config.max_errors:
        return f"too many errors: {snapshot.errored} > {config.max_errors}"
    return None


# -- state ----------------------------------------------------------------


def load_state(path: Path, config: HarnessConfig) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "rung": config.rung, "rows": config.rows, "runs": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STATE_VERSION:
        raise ValueError(f"{path}: unsupported state version {state.get('version')!r}")
    if state.get("rung") != config.rung or state.get("rows") != config.rows:
        raise ValueError(
            f"{path} belongs to '{state.get('rung')} · n{state.get('rows')}', not "
            f"'{config.rung} · n{config.rows}'; pass a different --state"
        )
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# -- harness --------------------------------------------------------------


def _apply_tool_gate(backend, entry: dict[str, Any], tool_gate) -> str | None:
    tool_records = getattr(backend, "tool_records", None)
    if tool_records is None:
        raise TypeError(f"{type(backend).__name__} has no tool_records(); cannot apply a tool gate")
    result = tool_gate(list(tool_records(entry["run_id"])))
    entry["tool_gate"] = result.to_dict()
    if result.valid:
        return None
    return (f"tool gate: {result.reason} ({result.errors}/{result.total} tool calls failed, "
            f"rate {result.error_rate:.1%} > {result.max_error_rate:.0%})"
            if result.reason == "tool_error_rate_exceeded"
            else f"tool gate: {result.reason} ({result.total} tool calls < {result.min_calls})")


def run_harness(
    backend: EvalBackend,
    config: HarnessConfig,
    state_path: str | os.PathLike,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda _msg: None,
    tool_gate: Callable[[list[dict[str, Any]]], Any] | None = None,
) -> dict[str, Any]:
    """Drive ``config.runs`` repeats to a decision and return the state.

    Safe to call again after an interruption: runs already in the state file
    are polled (not resubmitted). Raises :class:`TimeoutError` with the state
    saved when ``config.timeout`` elapses.

    ``tool_gate`` is an optional completeness gate applied to runs that pass
    :func:`judge`: it gets the run's tool-call records (from the backend's
    ``tool_records``) and returns an object with ``valid``, ``reason`` and
    ``to_dict()`` -- e.g. a partial of
    ``castia.evaluation.tool_errors.tool_error_gate``. A run whose tools were
    failing measures the outage, not the agent, so it is rejected.
    """
    path = Path(state_path)
    state = load_state(path, config)
    entries: dict[int, dict[str, Any]] = {e["index"]: e for e in state["runs"]}
    deadline = clock() + config.timeout

    def in_flight() -> list[dict[str, Any]]:
        return [e for e in entries.values() if e["decision"] == "pending"]

    while True:
        for index in range(1, config.runs + 1):
            if len(in_flight()) >= config.max_in_flight:
                break
            if index in entries:
                continue
            name = run_name(config.rung, config.rows, repeat=index)
            run_id = backend.submit(
                name, {"castia.rung": config.rung, "castia.repeat": str(index)}
            )
            entries[index] = {"index": index, "name": name, "run_id": run_id,
                              "decision": "pending", "reason": None, "snapshot": None}
            log(f"submitted     : {name} ({run_id})")
            state["runs"] = sorted(entries.values(), key=lambda e: e["index"])
            save_state(path, state)

        for entry in in_flight():
            snap = backend.poll(entry["run_id"])
            entry["snapshot"] = asdict(snap)
            if snap.status == TERMINAL_OK or snap.status in TERMINAL_BAD:
                reason = judge(snap, config)
                if reason is None and tool_gate is not None:
                    reason = _apply_tool_gate(backend, entry, tool_gate)
                entry["decision"] = "rejected" if reason else "accepted"
                entry["reason"] = reason
                log(f"{entry['decision']:<14}: {entry['name']}" + (f" -- {reason}" if reason else ""))
        state["runs"] = sorted(entries.values(), key=lambda e: e["index"])
        save_state(path, state)

        if len(entries) >= config.runs and not in_flight():
            return state
        if clock() >= deadline:
            raise TimeoutError(
                f"harness timed out after {config.timeout:g}s with "
                f"{len(in_flight())} run(s) in flight; rerun to resume from {path}"
            )
        sleep(config.poll_interval)


# -- reporting ------------------------------------------------------------


def _rate(passed: int, failed: int) -> tuple[float | None, int]:
    n = passed + failed
    return (passed / n if n else None), n


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    """Aggregate accepted runs: per criterion mean/min/max with runs and n."""
    accepted = [e for e in state["runs"] if e["decision"] == "accepted"]
    rows: dict[str, list[tuple[float, int]]] = {}
    for entry in accepted:
        snap = entry["snapshot"]
        overall, n = _rate(snap["passed"], snap["failed"])
        if overall is not None:
            rows.setdefault("(overall)", []).append((overall, n))
        for name, (p, f) in snap["criteria"].items():
            score, n = _rate(p, f)
            if score is not None:
                rows.setdefault(name, []).append((score, n))
    criteria = {}
    for name, values in rows.items():
        scores = [s for s, _ in values]
        criteria[name] = {
            "mean": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores),
            "runs": len(values),
            "n": sum(n for _, n in values),
            "min_n": min(n for _, n in values),
        }
    return {
        "requested": len(state["runs"]),
        "accepted": len(accepted),
        "rejected": sum(1 for e in state["runs"] if e["decision"] == "rejected"),
        "criteria": criteria,
    }


def format_report(state: dict[str, Any], *, requested: int) -> list[str]:
    """Human report: every score carries its n; aggregates carry runs and n."""
    lines = []
    for entry in state["runs"]:
        snap = entry["snapshot"] or {}
        head = f"run r{entry['index']:<3}: {entry['decision']:<9} {entry['name']} ({entry['run_id']})"
        if entry["reason"]:
            head += f" -- {entry['reason']}"
        lines.append(head)
        gate = entry.get("tool_gate")
        if gate:
            lines.append(f"    (tool calls)         {gate.get('errors', 0)}/{gate.get('total', 0)} failed "
                         f"(rate {gate.get('error_rate', 0.0):.1%})")
        if entry["decision"] != "accepted":
            continue
        overall, n = _rate(snap["passed"], snap["failed"])
        if overall is not None:
            lines.append(f"    (overall)            {overall:.3f} (n={n})")
        for name, (p, f) in snap["criteria"].items():
            score, n = _rate(p, f)
            if score is not None:
                lines.append(f"    {name:<20} {score:.3f} (n={n})")
    summary = summarize(state)
    lines.append(f"accepted      : {summary['accepted']}/{requested} runs")
    for name, c in summary["criteria"].items():
        lines.append(
            f"score         : {name:<20} {c['mean']:.3f} [{c['min']:.3f}, {c['max']:.3f}] "
            f"(runs={c['runs']}, n={c['n']})"
        )
        if c["min_n"] < SMALL_N:
            lines.append(f"warning       : {name} has n={c['min_n']} in some run (< {SMALL_N}); "
                         "differences under a few points are noise")
    return lines

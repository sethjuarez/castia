"""Offline checks for ``optimize run`` before a billable optimizer job.

Every rule here maps to a run that failed or misled after hours of spend (see
the Foundry field notes): turn-level evaluators that only fail at run time,
``item.*`` references to fields the optimizer never passes to evaluators, and
jobs sized past the service's 6-hour cap. The thresholds and field lists are
data, pinned in ``spec/conformance/optimization/preflight.json``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Row fields the native optimizer passes to evaluators. Waypoint (25e0ffe5)
# found this depends on the evaluator's registration type: PROMPT evaluators
# keep ``ground_truth``; RUBRIC (and unknown types) keep only ``query``, so
# ``item.ground_truth``/``item.retrieval_context`` score against nothing.
# --passthrough-field overrides this for every evaluator.
PASSTHROUGH_FIELDS: tuple[str, ...] = ("query",)
PASSTHROUGH_BY_TYPE: dict[str, tuple[str, ...]] = {
    "prompt": ("query", "ground_truth"),
    "rubric": ("query",),
}
KNOWN_DROPPED_FIELDS: tuple[str, ...] = ("ground_truth", "retrieval_context")

# Evaluator kinds that run per turn. The optimizer evaluates conversations, so
# these pass submit and fail at run time.
TURN_LEVEL_EVALUATOR_TYPES: tuple[str, ...] = (
    "string_check",
    "text_similarity",
    "python",
    "code",
    "label_model",
    "score_model",
)

PILOT_MAX_ROWS = 20
PILOT_MAX_CANDIDATES = 10
APPROVAL_HOURS = 1.0
SERVICE_CAP_HOURS = 6.0
DEFAULT_ROW_SECONDS = 30.0
ASSUMED_CANDIDATES = 10

_ITEM_REF = re.compile(r"\{\{\s*item\.([A-Za-z0-9_]+)")


@dataclass(frozen=True)
class OptimizeEstimate:
    """Rough wall-clock and cost estimate for one optimizer job."""

    rows: int | None
    candidates: int
    candidates_assumed: bool
    row_seconds: float
    concurrency: int
    hours: float | None
    cost_usd: float | None
    needs_approval: bool
    reasons: tuple[str, ...] = ()

    def line(self) -> str:
        rows = "unknown" if self.rows is None else str(self.rows)
        cands = f"{self.candidates}{' (assumed)' if self.candidates_assumed else ''}"
        if self.hours is None:
            return f"{rows} rows x {cands} candidates (pass --rows for a time estimate)"
        text = (
            f"{rows} rows x ({cands} candidates + baseline) x {self.row_seconds:g}s"
            f" / {self.concurrency} concurrent ~ {self.hours:.2f} h"
        )
        if self.cost_usd is not None:
            text += f" ~ ${self.cost_usd:.2f}"
        return text


@dataclass
class OptimizePreflight:
    """Problems block submit; warnings print and continue."""

    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimate: OptimizeEstimate | None = None


def item_references(value: Any) -> list[str]:
    """Every ``{{ item.<field> }}`` name in a nested evaluator definition."""

    found: list[str] = []
    for text in _strings(value):
        for name in _ITEM_REF.findall(text):
            if name not in found:
                found.append(name)
    return found


def evaluator_level_problems(
    evaluators: Iterable[Any],
    options: Mapping[str, Any] | None = None,
    definitions: Mapping[str, Any] | None = None,
) -> list[str]:
    """Flag turn-level evaluators and ``evaluation_level: turn``."""

    problems: list[str] = []
    level = str((options or {}).get("evaluation_level") or "").lower()
    if level == "turn":
        problems.append(
            "options.evaluation_level: 'turn' is rejected by the optimizer (400); "
            "leave evaluation_level unset"
        )
    for entry in evaluators:
        name = _evaluator_name(entry)
        for label, node in _evaluator_nodes(entry, name, definitions):
            kind = _turn_kind(node)
            if kind:
                problems.append(
                    f"evaluator {label}: {kind} runs per turn and fails at optimizer "
                    "run time; use a conversation-level evaluator"
                )
                break
    return problems


def item_field_problems(
    evaluators: Iterable[Any],
    rows: list[Mapping[str, Any]] | None,
    *,
    passthrough: Iterable[str] | None = None,
    definitions: Mapping[str, Any] | None = None,
) -> list[str]:
    """Check ``item.*`` references against pass-through fields and the dataset.

    With ``passthrough=None`` the allowed fields follow each evaluator's
    registration type (see ``PASSTHROUGH_BY_TYPE``).
    """

    problems: list[str] = []
    for entry in evaluators:
        name = _evaluator_name(entry)
        nodes = _evaluator_nodes(entry, name, definitions)
        refs: list[str] = []
        for _label, node in nodes:
            for ref in item_references(node):
                if ref not in refs:
                    refs.append(ref)
        kind = next((k for _label, n in nodes if (k := evaluator_type(n))), "")
        if passthrough is not None:
            allowed = set(passthrough)
        else:
            allowed = set(PASSTHROUGH_BY_TYPE.get(kind, PASSTHROUGH_FIELDS))
        scope = f"{kind} evaluators" if kind else "this evaluator"
        for ref in refs:
            if ref not in allowed:
                why = (
                    f"the optimizer drops it for {scope}"
                    if ref in KNOWN_DROPPED_FIELDS
                    else "the optimizer only passes "
                    + ", ".join(f"item.{f}" for f in sorted(allowed))
                    + f" to {scope}"
                )
                problems.append(
                    f"evaluator {name or '?'}: item.{ref} scores against nothing because "
                    f"{why}; move what the grader needs into a pass-through field"
                )
            elif rows is not None:
                missing = sum(1 for row in rows if ref not in row)
                if missing:
                    problems.append(
                        f"evaluator {name or '?'}: item.{ref} is missing from "
                        f"{missing}/{len(rows)} dataset rows"
                    )
    return problems


def estimate_optimize(
    *,
    rows: int | None,
    max_candidates: int | None,
    row_seconds: float = DEFAULT_ROW_SECONDS,
    concurrency: int | None = None,
    hourly_usd: float | None = None,
) -> OptimizeEstimate:
    """Estimate wall-clock hours as rows x (candidates + baseline) x row time."""

    assumed = max_candidates is None
    candidates = ASSUMED_CANDIDATES if assumed else int(max_candidates)
    conc = max(1, int(concurrency or 1))
    hours = None
    cost = None
    reasons: list[str] = []
    if rows is not None:
        hours = round(rows * (candidates + 1) * float(row_seconds) / conc / 3600.0, 4)
        if hourly_usd is not None:
            cost = round(hours * float(hourly_usd), 2)
        if rows > PILOT_MAX_ROWS:
            reasons.append(f"{rows} rows > pilot {PILOT_MAX_ROWS}")
        if hours > APPROVAL_HOURS:
            reasons.append(f"~{hours:.2f} h > {APPROVAL_HOURS:g} h")
    else:
        reasons.append("row count unknown (registered dataset)")
    if not assumed and candidates > PILOT_MAX_CANDIDATES:
        reasons.append(f"{candidates} candidates > pilot {PILOT_MAX_CANDIDATES}")
    return OptimizeEstimate(
        rows=rows,
        candidates=candidates,
        candidates_assumed=assumed,
        row_seconds=float(row_seconds),
        concurrency=conc,
        hours=hours,
        cost_usd=cost,
        needs_approval=bool(reasons),
        reasons=tuple(reasons),
    )


def preflight_request(
    request: Mapping[str, Any],
    *,
    evaluators: Iterable[Any] | None = None,
    definitions: Mapping[str, Any] | None = None,
    passthrough: Iterable[str] | None = None,
    rows: int | None = None,
    row_seconds: float = DEFAULT_ROW_SECONDS,
    hourly_usd: float | None = None,
) -> OptimizePreflight:
    """Run every offline check against a built optimizer request.

    ``evaluators`` defaults to the request's evaluator refs; pass the raw
    ``eval.yaml`` entries to also see ``type``/``level`` keys the wire drops.
    ``definitions`` maps evaluator name to a local definition (for example an
    exported evaluator) so its ``item.*`` references and type are checked too.
    """

    result = OptimizePreflight()
    opt = request.get("optimization_configuration") or {}
    evaluation = opt.get("evaluation_configuration") or {}
    search = opt.get("candidate_search_configuration") or {}
    options = {
        "max_candidates": search.get("max_candidates"),
        "max_concurrent_agent_runs": evaluation.get("max_concurrent_agent_runs"),
    }
    entries = list(evaluators if evaluators is not None else evaluation.get("evaluators") or [])
    train_rows = _inline_rows(evaluation.get("training_set"))

    result.problems.extend(evaluator_level_problems(entries, options, definitions))
    result.problems.extend(
        item_field_problems(entries, train_rows, passthrough=passthrough, definitions=definitions)
    )
    for entry in entries:
        name = _evaluator_name(entry)
        if not isinstance(entry, Mapping) and not (definitions or {}).get(name):
            result.warnings.append(
                f"evaluator {name}: no local definition; item.* references not checked "
                "(pass --evaluator-definition)"
            )

    if rows is None:
        counted = [
            len(r)
            for r in (train_rows, _inline_rows(evaluation.get("validation_set")))
            if r is not None
        ]
        rows = sum(counted) if train_rows is not None else None
    estimate = estimate_optimize(
        rows=rows,
        max_candidates=options.get("max_candidates"),
        row_seconds=row_seconds,
        concurrency=options.get("max_concurrent_agent_runs"),
        hourly_usd=hourly_usd,
    )
    result.estimate = estimate
    if estimate.hours is not None and estimate.hours > SERVICE_CAP_HOURS:
        result.warnings.append(
            f"estimate ~{estimate.hours:.1f} h exceeds the {SERVICE_CAP_HOURS:g} h service "
            f"cap; start with <= {PILOT_MAX_ROWS} rows and <= {PILOT_MAX_CANDIDATES} candidates"
        )
    return result


def evaluator_type(node: Any) -> str:
    """Registration type (``prompt``, ``rubric``, ...) of an evaluator entry or definition."""

    if not isinstance(node, Mapping):
        return ""
    for key in ("evaluator_type", "type"):
        if isinstance(node.get(key), str) and node[key]:
            return node[key].lower()
    definition = node.get("definition")
    if isinstance(definition, Mapping):
        return evaluator_type(definition)
    return ""


def _inline_rows(dataset: Any) -> list[Mapping[str, Any]] | None:
    """Inline test cases of a GA evaluation set (``{"source": {"type": "inline"}}``)."""

    source = dataset.get("source") if isinstance(dataset, Mapping) else None
    if isinstance(source, Mapping) and source.get("type") == "inline":
        items = source.get("test_cases")
        if isinstance(items, list):
            return [row for row in items if isinstance(row, Mapping)]
    return None


def _evaluator_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        return str(entry.get("name") or "")
    return ""


def _evaluator_nodes(
    entry: Any,
    name: str,
    definitions: Mapping[str, Any] | None,
) -> list[tuple[str, Any]]:
    nodes: list[tuple[str, Any]] = []
    if isinstance(entry, Mapping):
        nodes.append((name or "?", entry))
    definition = (definitions or {}).get(name)
    if definition is not None:
        nodes.append((f"{name} (definition)", definition))
    return nodes


def _turn_kind(node: Any) -> str | None:
    if isinstance(node, Mapping):
        for key in ("evaluation_level", "level"):
            if str(node.get(key) or "").lower() == "turn":
                return f"{key}: turn"
        kind = str(node.get("type") or "").lower()
        if kind in TURN_LEVEL_EVALUATOR_TYPES:
            return f"type {kind}"
        for value in node.values():
            found = _turn_kind(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _turn_kind(value)
            if found:
                return found
    return None


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


__all__ = [
    "APPROVAL_HOURS",
    "ASSUMED_CANDIDATES",
    "DEFAULT_ROW_SECONDS",
    "KNOWN_DROPPED_FIELDS",
    "PASSTHROUGH_BY_TYPE",
    "PASSTHROUGH_FIELDS",
    "PILOT_MAX_CANDIDATES",
    "PILOT_MAX_ROWS",
    "SERVICE_CAP_HOURS",
    "TURN_LEVEL_EVALUATOR_TYPES",
    "OptimizeEstimate",
    "OptimizePreflight",
    "estimate_optimize",
    "evaluator_level_problems",
    "evaluator_type",
    "item_field_problems",
    "item_references",
    "preflight_request",
]

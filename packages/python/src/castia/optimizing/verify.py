"""Post-optimize checks: seeding, held-out verify, leak diff, and status labels.

The optimizer score is the mean of the configured evaluators on a subsample, so
it can rise while the real metric stays flat, and candidate prompts can copy
training rows verbatim. These helpers keep that evidence separate:

- ``seed_from_version`` seeds a run from a deployed agent version.
- ``training_leaks`` flags candidate text copied from training rows.
- ``heldout_overlap`` flags held-out rows that also appear in training.
- ``verify_verdict`` compares baseline and candidate on a held-out metric.
- ``termination_label`` reads "Maximum consecutive stall limit" on a
  SUCCEEDED job as early stopping, not a failure.

Thresholds are pinned in ``spec/conformance/optimization/verify.json``.
"""

from __future__ import annotations

import copy
import difflib
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

LEAK_SHINGLE_WORDS = 6
LEAK_MIN_WORDS = 3
STALL_MARKERS: tuple[str, ...] = ("maximum consecutive stall",)
STOPPED_EARLY_LABEL = "stopped early (no improvement)"

_WORD = re.compile(r"[a-z0-9]+(?:['_.:/-][a-z0-9]+)*")


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text).lower())


def _row_strings(row: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(row, str):
        yield prefix or "value", row
    elif isinstance(row, Mapping):
        for key, value in row.items():
            yield from _row_strings(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(row, list):
        for index, value in enumerate(row):
            yield from _row_strings(value, f"{prefix}[{index}]")


def training_leaks(
    candidate_text: str,
    rows: Iterable[Any],
    *,
    shingle_words: int = LEAK_SHINGLE_WORDS,
    min_words: int = LEAK_MIN_WORDS,
    baseline_text: str = "",
) -> list[dict[str, Any]]:
    """Training text that appears verbatim in the candidate but not the baseline.

    Values of at least ``shingle_words`` words match on any shared run of that
    many words; shorter values of at least ``min_words`` words must appear
    whole. Matches already present in ``baseline_text`` are not reported.
    """

    cand = _words(candidate_text)
    base = _words(baseline_text)
    cand_joined = f" {' '.join(cand)} "
    base_joined = f" {' '.join(base)} "
    cand_shingles = _shingles(cand, shingle_words)
    base_shingles = _shingles(base, shingle_words)
    leaks: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        for field, text in _row_strings(row):
            words = _words(text)
            if len(words) >= shingle_words:
                hits = [
                    " ".join(words[i : i + shingle_words])
                    for i in range(len(words) - shingle_words + 1)
                ]
                hits = [s for s in hits if s in cand_shingles and s not in base_shingles]
                if hits:
                    leaks.append({"row": index, "field": field, "match": hits[0]})
            elif len(words) >= min_words:
                needle = f" {' '.join(words)} "
                if needle in cand_joined and needle not in base_joined:
                    leaks.append({"row": index, "field": field, "match": needle.strip()})
    return leaks


def _shingles(words: list[str], size: int) -> set[str]:
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def candidate_diff(baseline: str, candidate: str, *, name: str = "instructions.md") -> str:
    """Unified diff of baseline and candidate prompt text."""

    return "".join(
        difflib.unified_diff(
            baseline.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"baseline/{name}",
            tofile=f"candidate/{name}",
        )
    )


def heldout_overlap(
    training: Iterable[Mapping[str, Any]],
    heldout: Iterable[Mapping[str, Any]],
    *,
    key: str = "query",
) -> list[int]:
    """Indexes of held-out rows whose ``key`` text also appears in training."""

    seen = {" ".join(_words(row.get(key, ""))) for row in training if isinstance(row, Mapping)}
    seen.discard("")
    return [
        index
        for index, row in enumerate(heldout)
        if isinstance(row, Mapping) and " ".join(_words(row.get(key, ""))) in seen
    ]


def metric_values(rows: Iterable[Any], metric: str) -> list[float]:
    """Per-row numeric values for ``metric`` (booleans count as 1/0)."""

    values: list[float] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = row.get(metric)
        for nest in ("scores", "metrics", "results"):
            if value is None and isinstance(row.get(nest), Mapping):
                value = row[nest].get(metric)
        if isinstance(value, bool):
            values.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            values.append(float(value))
    return values


def verify_verdict(
    baseline: list[float],
    candidate: list[float],
    *,
    min_delta: float = 0.0,
    leaks: int = 0,
    overlap: int = 0,
) -> dict[str, Any]:
    """``pass`` when the candidate holds or beats baseline on held-out data.

    Nonfinite metric values or ``min_delta`` make the verdict ``incomplete``
    (``fail`` with leaks or overlap) rather than comparing NaN or infinity.
    """

    reasons: list[str] = []
    nonfinite = sum(1 for value in [*baseline, *candidate] if not math.isfinite(value))
    base_mean = (
        round(sum(baseline) / len(baseline), 4)
        if baseline and not nonfinite
        else None
    )
    cand_mean = (
        round(sum(candidate) / len(candidate), 4)
        if candidate and not nonfinite
        else None
    )
    delta = None
    if nonfinite:
        reasons.append(f"held-out metric has {nonfinite} nonfinite value(s)")
    elif not math.isfinite(min_delta):
        reasons.append("required held-out delta must be finite")
    elif base_mean is None or cand_mean is None:
        reasons.append("held-out metric missing for baseline or candidate")
    else:
        delta = round(cand_mean - base_mean, 4)
        if len(baseline) != len(candidate):
            reasons.append(
                f"row counts differ (baseline {len(baseline)}, candidate {len(candidate)})"
            )
        if delta < min_delta:
            reasons.append(f"held-out delta {delta:+.4f} < required {min_delta:+.4f}")
    if leaks:
        reasons.append(f"candidate copies training text ({leaks} match(es))")
    if overlap:
        reasons.append(f"{overlap} held-out row(s) also appear in training")
    status = "pass"
    if reasons:
        status = "incomplete" if delta is None and not leaks and not overlap else "fail"
    return {
        "status": status,
        "baseline_mean": base_mean,
        "candidate_mean": cand_mean,
        "delta": delta,
        "reasons": reasons,
    }


def termination_label(status: Mapping[str, Any]) -> str | None:
    """Return ``STOPPED_EARLY_LABEL`` when a stall limit ended the job."""

    texts = " ".join(text.lower() for _k, text in _row_strings(_without(status, "result")))
    result = status.get("result")
    if isinstance(result, Mapping):
        texts += " " + " ".join(
            text.lower() for _k, text in _row_strings(_without(result, "candidates"))
        )
    if any(marker in texts for marker in STALL_MARKERS):
        return STOPPED_EARLY_LABEL
    return None


def _without(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != key}


def seed_from_version(
    request: Mapping[str, Any],
    version: Mapping[str, Any],
    *,
    agent_version: str,
) -> tuple[dict[str, Any], list[str]]:
    """Seed the optimizer baseline from an agent version's prompt definition.

    ``request`` is a GA ``agent_optimization`` request and ``version`` is
    ``AgentVersionDetails.as_dict()``. Instructions, model and function tools in
    ``definition`` replace the baseline agent configuration (the deployed
    function tools always replace baseline ``tools``, so a tool-free version
    clears them), and the target is
    pinned to ``agent_version``. Raises ``ValueError`` when the request is not an
    ``agent_optimization`` job or the version has nothing to seed from.
    """

    seeded = copy.deepcopy(dict(request))
    config = seeded.get("optimization_configuration")
    if not isinstance(config, dict) or config.get("type") != "agent_optimization":
        raise ValueError("--from-version seeds an agent_optimization job (not traces:)")
    definition = version.get("definition") if isinstance(version, Mapping) else None
    if not isinstance(definition, Mapping):
        definition = {}
    opt = config.setdefault("baseline_agent_configuration", {})
    notes: list[str] = []
    instructions = definition.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        opt["system_prompt"] = instructions
        notes.append("instructions")
    model = definition.get("model")
    if isinstance(model, str) and model:
        opt["current_model"] = model
        notes.append("model")
    tools = [
        tool for tool in definition.get("tools") or []
        if isinstance(tool, Mapping) and tool.get("type") == "function"
    ]
    if not notes and not tools:
        raise ValueError(
            f"agent version {agent_version} has no prompt definition to seed from "
            "(hosted agents: apply a candidate locally and use --candidate)"
        )
    # The deployed function-tool surface is authoritative, even when it is empty.
    opt["tools"] = [_function_tool(tool) for tool in tools]
    notes.append(f"{len(tools)} function tool(s)")
    seeded.setdefault("target_configuration", {})["version"] = str(agent_version)
    return seeded, notes


def _function_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(tool.get("function"), Mapping):
        return {"type": "function", "function": dict(tool["function"])}
    function = {k: tool[k] for k in ("name", "description", "parameters") if k in tool}
    return {"type": "function", "function": function}


__all__ = [
    "LEAK_MIN_WORDS",
    "LEAK_SHINGLE_WORDS",
    "STALL_MARKERS",
    "STOPPED_EARLY_LABEL",
    "candidate_diff",
    "heldout_overlap",
    "metric_values",
    "seed_from_version",
    "termination_label",
    "training_leaks",
    "verify_verdict",
]

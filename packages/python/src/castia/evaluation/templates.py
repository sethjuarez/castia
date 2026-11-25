"""Evaluator templates written by ``python -m castia eval template``.

The gold-aware template pairs a deterministic ``string_check`` against the
row's ``ground_truth`` with a rubric whose correctness dimension is a gate.
Optimizer run-time rows keep only ``query`` and ``ground_truth``, so both
evaluators read nothing else from the row.

Caveat (observed live): when the Agent Optimizer registers evaluators, a
**prompt** evaluator keeps ``ground_truth`` but a **rubric** evaluator has it
stripped, so the rubric's ``{{item.ground_truth}}`` arrives empty. For
optimizer runs, register the gold check as a prompt evaluator; the rubric is
for ``castia eval run`` and other non-optimizer runs. ``eval check`` notes
any rubric that references ``{{item.ground_truth}}``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from castia.evaluation.suite import (
    DEFAULT_PASS_THRESHOLD,
    RubricDimension,
    compile_gates,
    rubric_to_wire,
)

GOLD_AWARE_DIMENSIONS: tuple[RubricDimension, ...] = (
    RubricDimension(
        id="matches_ground_truth",
        description=(
            "The response's final answer agrees with the reference answer in "
            "{{item.ground_truth}}. Paraphrase and extra context are fine; a "
            "different value, entity, decision, or a missing answer fails."
        ),
        gate=True,
    ),
    RubricDimension(
        id="grounded_reasoning",
        description=(
            "Any supporting explanation is consistent with the reference answer "
            "and does not invent facts."
        ),
        weight=1,
    ),
    RubricDimension(
        id="clarity_and_conciseness",
        description="The response states the answer clearly without unnecessary verbosity.",
        weight=1,
    ),
    RubricDimension(
        id="general_quality",
        description="Other important quality factors not captured by the dimensions above.",
        weight=1,
        always_applicable=True,
    ),
)

GOLD_STRING_CHECK: dict = {
    "type": "string_check",
    "name": "gold_answer_present",
    "input": "{{sample.output_text}}",
    "reference": "{{item.ground_truth}}",
    "operation": "ilike",
}

GOLD_SAMPLE_ROWS: tuple[dict, ...] = (
    {"query": "What is the capital of Australia?", "ground_truth": "Canberra"},
)


def _source_rubric() -> list[dict]:
    out = []
    for d in GOLD_AWARE_DIMENSIONS:
        entry: dict = {"id": d.id, "description": d.description}
        if d.weight is not None:
            entry["weight"] = int(d.weight)
        if d.always_applicable:
            entry["always_applicable"] = True
        if d.gate:
            entry["gate"] = True
        out.append(entry)
    return out


def write_gold_aware_template(
    out_dir: str | os.PathLike,
    *,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    overwrite: bool = False,
) -> list[Path]:
    """Write the gold-aware evaluator files into ``out_dir``.

    * ``gold_rubric.source.json`` -- the authored rubric with ``gate: true``.
      Keep it in the repo; Foundry does not keep the brief.
    * ``gold_rubric.json`` -- the compiled, wire-safe dimensions file to
      reference from ``eval.yaml`` as a rubric ``local_uri``.
    * ``gold_string_check.json`` -- a ``string_check`` testing criterion.
    * ``gold_sample.jsonl`` -- example rows using only ``query``/``ground_truth``.

    Refuses to overwrite existing files unless ``overwrite`` is set.
    """
    root = Path(out_dir)
    compiled = rubric_to_wire(
        compile_gates(GOLD_AWARE_DIMENSIONS, pass_threshold=pass_threshold)
    )
    files = {
        "gold_rubric.source.json": json.dumps(_source_rubric(), indent=2) + "\n",
        "gold_rubric.json": json.dumps(compiled, indent=2) + "\n",
        "gold_string_check.json": json.dumps(GOLD_STRING_CHECK, indent=2) + "\n",
        "gold_sample.jsonl": "".join(json.dumps(r) + "\n" for r in GOLD_SAMPLE_ROWS),
    }
    targets = [root / name for name in files]
    existing = [p for p in targets if p.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite: " + ", ".join(str(p) for p in existing)
        )
    root.mkdir(parents=True, exist_ok=True)
    for path, text in zip(targets, files.values(), strict=True):
        path.write_text(text, encoding="utf-8")
    return targets

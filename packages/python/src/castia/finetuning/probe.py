"""Grader probes and in-process grading for RFT (offline, pure except ``exec``).

Two failures cost real money before this existed:

- Foundry's ``graders/run`` returned ``0.0`` for Python graders that score
  correctly in-process, so a "remote check" said nothing about the grader.
  :func:`grade_local` runs the grader here, on your rows, so you know what score
  the trainer *should* see.
- A trainer reward that disagrees with the offline score means the grader, the
  templates, or the dataset fields are not what you think. A tiny probe with an
  always-true and an always-false grader proves the reward plumbing first, then
  :func:`probe_verdict` blocks a full submit when the real grader diverges.

Thresholds and the probe grader sources are pinned in
``spec/conformance/finetuning/probe.json``.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from collections.abc import Iterable, Mapping, Sequence

PROBE_ROWS = 10
PASS_FLOOR = 0.9  # always-true must reach at least this mean reward
FAIL_CEILING = 0.1  # always-false must stay at or under this mean reward
DEFAULT_TOLERANCE = 0.15  # max |trainer reward - offline score| for the real grader

ALWAYS_TRUE_SOURCE = "def grade(sample, item):\n    return 1.0\n"
ALWAYS_FALSE_SOURCE = "def grade(sample, item):\n    return 0.0\n"

_TEMPLATE = re.compile(r"\{\{\s*(sample|item)\.([A-Za-z0-9_.]+)\s*\}\}")
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCTIONS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "log": math.log}


class GraderRunError(RuntimeError):
    """A grader raised or returned a non-number for one row."""


def probe_graders() -> dict[str, dict]:
    """The always-true and always-false Python graders a probe submits."""
    return {
        "always_true": {"type": "python", "name": "probe_always_true",
                        "source": ALWAYS_TRUE_SOURCE},
        "always_false": {"type": "python", "name": "probe_always_false",
                         "source": ALWAYS_FALSE_SOURCE},
    }


def _lookup(root: object, path: str) -> object:
    value = root
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return ""
    return value


def render_template(template: str, sample: Mapping, item: Mapping) -> str:
    """Render ``{{ sample.* }}`` / ``{{ item.* }}`` references; unknown paths are ``""``."""
    scopes = {"sample": sample, "item": item}

    def replace(match: re.Match) -> str:
        value = _lookup(scopes[match.group(1)], match.group(2))
        return value if isinstance(value, str) else json.dumps(value)

    return _TEMPLATE.sub(replace, template)


def _calculate(expression: str, scores: Mapping[str, float]) -> float:
    def walk(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in scores:
            return float(scores[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FUNCTIONS and not node.keywords):
            return float(_FUNCTIONS[node.func.id](*(walk(arg) for arg in node.args)))
        raise GraderRunError(f"calculate_output: unsupported expression {ast.dump(node)}")

    return walk(ast.parse(expression, mode="eval"))


def grade_local(grader: Mapping, sample: Mapping, item: Mapping) -> float | None:
    """Score one row in-process. ``None`` when the grader type needs a model or
    endpoint (``score_model``, ``text_similarity``, ``endpoint``).

    Python graders run with ``exec`` in this process: only grade code you trust.
    """
    gtype = grader.get("type")
    if gtype == "python":
        namespace: dict = {"__name__": "castia_grader"}
        try:
            exec(compile(str(grader.get("source", "")), "<grader>", "exec"), namespace)  # noqa: S102
            score = namespace["grade"](dict(sample), dict(item))
        except Exception as exc:
            raise GraderRunError(f"{type(exc).__name__}: {exc}") from exc
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise GraderRunError(f"grade() returned {type(score).__name__}, not a number")
        return float(score)
    if gtype == "string_check":
        left = render_template(str(grader.get("input", "")), sample, item)
        right = render_template(str(grader.get("reference", "")), sample, item)
        op = grader.get("operation", "eq")
        result = {
            "eq": left == right,
            "ne": left != right,
            "like": right in left,
            "ilike": right.lower() in left.lower(),
        }.get(op)
        if result is None:
            raise GraderRunError(f"string_check: unknown operation {op!r}")
        return 1.0 if result else 0.0
    if gtype == "multi":
        scores: dict[str, float] = {}
        for key, sub in (grader.get("graders") or {}).items():
            score = grade_local(sub, sample, item)
            if score is None:
                return None
            scores[key] = score
        return _calculate(str(grader.get("calculate_output", "")), scores)
    return None


def sample_for(output: object) -> dict:
    """The ``sample`` namespace for one model output (text, plus JSON if it parses)."""
    text = output if isinstance(output, str) else json.dumps(output)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    return {"output_text": text, "output_json": parsed}


def grade_rows(grader: Mapping, items: Sequence[Mapping], outputs: Sequence[object]) -> dict:
    """Grade ``outputs[i]`` against ``items[i]`` and summarise (pure apart from exec)."""
    if len(items) != len(outputs):
        raise ValueError(f"{len(items)} rows but {len(outputs)} outputs")
    scores: list[float | None] = []
    errors: list[dict] = []
    skipped = False
    for index, (item, output) in enumerate(zip(items, outputs)):
        try:
            score = grade_local(grader, sample_for(output), item)
        except GraderRunError as exc:
            errors.append({"row": index, "error": str(exc)})
            scores.append(None)
            continue
        if score is None:
            skipped = True
            break
        scores.append(score)
    numbers = [s for s in scores if s is not None]
    report: dict = {
        "grader_type": grader.get("type"),
        "rows": len(items),
        "scored": len(numbers),
        "skipped": skipped,
        "errors": errors,
        "scores": [] if skipped else scores,
    }
    if skipped:
        report["reason"] = (
            f"{grader.get('type')} graders need a model or endpoint; "
            "grade-local scores python, string_check, and multi of those"
        )
    if numbers and not skipped:
        report.update(mean=round(sum(numbers) / len(numbers), 6),
                      min=min(numbers), max=max(numbers))
    return report


def probe_split(rows: Sequence, size: int = PROBE_ROWS) -> list:
    """``size`` rows for a probe split, cycling a short dataset to reach the floor."""
    if not rows:
        raise ValueError("probe needs at least one row")
    return [
        dict(row) if isinstance(row, Mapping) else row
        for row in (rows[i % len(rows)] for i in range(size))
    ]


def reward_from_events(events: Iterable[Mapping]) -> float | None:
    """The mean of every numeric ``*reward*`` metric in job events, or ``None``.

    Provisional: metric key names are not pinned by the service, so any numeric
    key containing ``reward`` (case-insensitive) in a ``metrics`` event's
    ``data`` counts.
    """
    values: list[float] = []
    for event in events:
        if event.get("type") != "metrics":
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        for key, value in data.items():
            if ("reward" in str(key).lower() and isinstance(value, (int, float))
                    and not isinstance(value, bool) and math.isfinite(value)):
                values.append(float(value))
    return round(sum(values) / len(values), 6) if values else None


def probe_verdict(
    rewards: Mapping[str, float | None],
    *,
    offline_score: float | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict:
    """Decide whether a probe clears a full submit (pure).

    ``rewards`` maps ``always_true``, ``always_false``, and optionally ``real``
    to the trainer's observed reward (``None`` while unobserved). Status is
    ``pass``, ``fail``, or ``pending``; ``fail`` blocks ``finetune submit``.
    """
    reasons: list[str] = []
    pending: list[str] = []
    true_reward = rewards.get("always_true")
    false_reward = rewards.get("always_false")
    if true_reward is None:
        pending.append("always_true reward not observed yet")
    elif true_reward < PASS_FLOOR:
        reasons.append(
            f"always_true reward {true_reward:g} < {PASS_FLOOR:g}: the trainer is not "
            "reading grader output as reward"
        )
    if false_reward is None:
        pending.append("always_false reward not observed yet")
    elif false_reward > FAIL_CEILING:
        reasons.append(
            f"always_false reward {false_reward:g} > {FAIL_CEILING:g}: reward does not "
            "track the grader"
        )
    if "real" in rewards:
        real = rewards["real"]
        if real is None:
            pending.append("real grader reward not observed yet")
        elif offline_score is None:
            pending.append("no offline score; run finetune grade-local")
        elif abs(real - offline_score) > tolerance:
            reasons.append(
                f"trainer reward {real:g} diverges from offline score "
                f"{offline_score:g} by more than {tolerance:g}: check grader templates "
                "and item.* fields"
            )
    status = "fail" if reasons else ("pending" if pending else "pass")
    return {"status": status, "reasons": reasons, "pending": pending}

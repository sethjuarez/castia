"""Reinforcement fine-tuning (RFT) tooling -- the last lifecycle step.

The agent lifecycle is *build -> evaluate -> optimize -> switch models*. The
first three are covered by the model runtime, :mod:`castia.evalsuite`, and
:mod:`castia.optimize`. This module is the fourth: it prepares and (behind one
guarded seam) submits a **reinforcement fine-tuning** job that mints a new,
stronger *reasoning-model* deployment. That deployment then becomes a candidate
in the optimizer's ``model_search_space`` -- i.e. "switch models via RFT" is
train -> deploy checkpoint -> point ``configured_model`` at it -> re-optimize.

RFT trains a reasoning model against a **grader** (a reward function) rather than
labeled answers. Submission is a data-plane operation on the OpenAI *fine-tuning*
API (``client.fine_tuning.jobs.create(method={"type": "reinforcement", ...})``),
**out of band** from the agent server -- so this is build-time tooling, not a
request path.

Same three thin seams as the eval-suite tooling:

* **Pure grader builders** -- :func:`string_check_grader`,
  :func:`text_similarity_grader`, :func:`score_model_grader`,
  :func:`python_grader`, :func:`multi_grader`, plus the cross-step bridge
  :func:`rubric_to_score_model` (an eval **rubric** -> a ``score_model`` grader).
  They return plain dicts and run nothing.
* **Pure offline validators** -- :func:`validate_grader`,
  :func:`validate_rft_example` / :func:`validate_rft_dataset` /
  :func:`validate_rft_splits`, and :func:`build_rft_job` (a pure payload
  builder). These are the free CI gate (``python -m castia finetune check``)
  and are exercised entirely against fixtures, no Azure, no spend.
* **One impure submit seam** -- :func:`submit_rft_job` (and :func:`upload_file`),
  guarded on credentials so the builders stay unit-testable. It shells to the
  Foundry OpenAI client and submits a **billable** training job.

.. note::
   **Validation status.** The grader/dataset *builders and validators* here are
   offline-tested. The *wire-acceptance* facts they encode -- the exact grader
   schema, the RFT hyperparameter names, and that
   ``fine_tuning.jobs.create`` accepts this payload -- are **doc-derived**
   (Foundry RFT how-to) and have **not** been confirmed against a live RFT job.
   Treat :func:`build_rft_job` / :func:`submit_rft_job` output as provisional
   until a real submission validates it. See ``evals/RFT.md``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .evalsuite import RubricDimension

# Grader types accepted by the RFT reward function. ``endpoint`` is preview.
GRADER_TYPES = (
    "string_check",
    "text_similarity",
    "score_model",
    "python",
    "multi",
    "endpoint",
)

# ``string_check`` comparison operations.
STRING_CHECK_OPS = ("eq", "ne", "like", "ilike")

# ``text_similarity`` metrics (BLEU family + fuzzy/embedding distances).
TEXT_SIMILARITY_METRICS = (
    "bleu",
    "gleu",
    "meteor",
    "rouge_1",
    "rouge_2",
    "rouge_3",
    "rouge_4",
    "rouge_5",
    "rouge_l",
    "cosine",
    "fuzzy_match",
)

# RFT-specific hyperparameters (plus the SFT-shared knobs RFT also accepts).
RFT_HYPERPARAMETERS = (
    "eval_interval",
    "eval_samples",
    "compute_multiplier",
    "reasoning_effort",
    "n_epochs",
    "batch_size",
    "learning_rate_multiplier",
)

# Template namespaces a grader may reference via {{ ... }} mustache expressions:
# ``sample.*`` (the model's output for a row) and ``item.*`` (ground-truth
# fields carried on the dataset row alongside ``messages``).
_TEMPLATE_NS = ("sample", "item")
_MUSTACHE = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")

DEFAULT_METHOD_TYPE = "reinforcement"


# --------------------------------------------------------------------------- #
# Grader builders (pure)                                                       #
# --------------------------------------------------------------------------- #


def string_check_grader(
    name: str,
    *,
    input: str,
    reference: str,
    operation: str = "eq",
) -> dict:
    """A ``string_check`` grader: exact/substring string comparison.

    ``input`` and ``reference`` are templates (typically
    ``"{{ sample.output_text }}"`` vs ``"{{ item.answer }}"``); ``operation`` is
    one of :data:`STRING_CHECK_OPS`.
    """
    if operation not in STRING_CHECK_OPS:
        raise ValueError(f"operation must be one of {STRING_CHECK_OPS}, got {operation!r}")
    return {
        "type": "string_check",
        "name": name,
        "input": input,
        "reference": reference,
        "operation": operation,
    }


def text_similarity_grader(
    name: str,
    *,
    input: str,
    reference: str,
    evaluation_metric: str = "fuzzy_match",
    pass_threshold: float | None = None,
) -> dict:
    """A ``text_similarity`` grader scoring ``input`` vs ``reference``.

    ``evaluation_metric`` is one of :data:`TEXT_SIMILARITY_METRICS`.
    """
    if evaluation_metric not in TEXT_SIMILARITY_METRICS:
        raise ValueError(
            f"evaluation_metric must be one of {TEXT_SIMILARITY_METRICS}, "
            f"got {evaluation_metric!r}"
        )
    grader: dict = {
        "type": "text_similarity",
        "name": name,
        "input": input,
        "reference": reference,
        "evaluation_metric": evaluation_metric,
    }
    if pass_threshold is not None:
        grader["pass_threshold"] = float(pass_threshold)
    return grader


def score_model_grader(
    name: str,
    *,
    model: str,
    input: Sequence[dict],
    range: Sequence[float] | None = None,
    pass_threshold: float | None = None,
    sampling_params: dict | None = None,
) -> dict:
    """A ``score_model`` grader: an LLM judge scores each sample.

    ``input`` is a list of chat messages (the judge prompt) whose content may
    reference ``{{ sample.output_text }}`` / ``{{ item.* }}``. ``range`` is the
    numeric score band (default ``[0, 1]``).
    """
    grader: dict = {
        "type": "score_model",
        "name": name,
        "model": model,
        "input": [dict(m) for m in input],
    }
    grader["range"] = [float(range[0]), float(range[1])] if range else [0.0, 1.0]
    if pass_threshold is not None:
        grader["pass_threshold"] = float(pass_threshold)
    if sampling_params is not None:
        grader["sampling_params"] = dict(sampling_params)
    return grader


def python_grader(name: str, *, source: str, image_tag: str | None = None) -> dict:
    """A ``python`` grader: sandboxed ``grade(sample, item) -> float``.

    ``source`` is the Python module text; it runs with no network access.
    """
    grader: dict = {"type": "python", "name": name, "source": source}
    if image_tag is not None:
        grader["image_tag"] = image_tag
    return grader


def multi_grader(
    name: str,
    *,
    graders: dict[str, dict],
    calculate_output: str,
) -> dict:
    """A ``multi`` grader combining sub-graders via ``calculate_output``.

    ``graders`` maps a sub-grader key to its grader dict; ``calculate_output`` is
    an arithmetic expression over those keys (e.g. ``"0.7 * acc + 0.3 * sim"``).
    """
    return {
        "type": "multi",
        "name": name,
        "graders": {k: dict(v) for k, v in graders.items()},
        "calculate_output": calculate_output,
    }


def rubric_to_score_model(
    dimensions: Iterable[RubricDimension],
    *,
    model: str,
    name: str = "rubric_score",
    output_template: str = "{{ sample.output_text }}",
) -> dict:
    """Bridge an eval **rubric** into a ``score_model`` RFT grader.

    The eval rubric (``id`` / ``description`` / ``weight`` dimensions from
    :func:`castia.evalsuite.read_rubric`) and the RFT grader are different
    shapes, but a rubric is exactly a weighted judging spec -- so it maps cleanly
    onto an LLM-judge (``score_model``) grader. Each dimension becomes a weighted
    line in the judge's system prompt; the judge returns a single weighted score
    in ``[0, 1]``. This is the natural cross-SDK tie-in between the *evaluate* and
    *switch-models* steps.
    """
    dims = list(dimensions)
    if not dims:
        raise ValueError("cannot build a grader from an empty rubric")

    lines = []
    for d in dims:
        weight = d.weight if d.weight is not None else 1.0
        tag = " (always applicable)" if getattr(d, "always_applicable", False) else ""
        lines.append(f"- {d.id} [weight {weight:g}]{tag}: {d.description}")
    rubric_block = "\n".join(lines)

    system = (
        "You are a strict grader. Score the assistant response against the "
        "weighted rubric below. Return only a number in [0, 1] equal to the "
        "weight-normalized fraction of rubric dimensions the response satisfies "
        "(partial credit allowed).\n\nRubric:\n" + rubric_block
    )
    user = (
        "Task input:\n{{ item.input }}\n\n"
        "Assistant response to grade:\n" + output_template
    )
    return score_model_grader(
        name,
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        range=[0.0, 1.0],
    )


# --------------------------------------------------------------------------- #
# Validators (pure, offline gate)                                             #
# --------------------------------------------------------------------------- #


def _template_refs(value) -> set[str]:
    """Collect every ``{{ ns.field }}`` reference in a grader value (recursive)."""
    refs: set[str] = set()
    if isinstance(value, str):
        refs.update(_MUSTACHE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            refs |= _template_refs(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            refs |= _template_refs(v)
    return refs


def grader_item_fields(grader: dict) -> set[str]:
    """The ``item.<field>`` names a grader references (ground-truth columns)."""
    return {
        ref.split(".", 1)[1]
        for ref in _template_refs(grader)
        if ref.startswith("item.") and "." in ref
    }


def validate_grader(grader: dict) -> list[str]:
    """Check a grader dict is structurally well-formed. Returns problems.

    Validates the ``type``, the per-type required fields, and that every
    ``{{ ... }}`` template references a known namespace (:data:`_TEMPLATE_NS`).
    An empty list means the grader is internally consistent (a clean gate).
    """
    problems: list[str] = []
    if not isinstance(grader, dict):
        return ["grader must be a mapping"]

    gtype = grader.get("type")
    if gtype not in GRADER_TYPES:
        problems.append(f"grader type {gtype!r} is not one of {GRADER_TYPES}")
    if not grader.get("name"):
        problems.append("grader has no 'name'")

    required = {
        "string_check": ("input", "reference", "operation"),
        "text_similarity": ("input", "reference", "evaluation_metric"),
        "score_model": ("model", "input"),
        "python": ("source",),
        "multi": ("graders", "calculate_output"),
        "endpoint": ("endpoint",),
    }.get(gtype, ())
    for field in required:
        if grader.get(field) in (None, "", [], {}):
            problems.append(f"{gtype} grader missing required field {field!r}")

    if gtype == "string_check" and grader.get("operation") not in (None, *STRING_CHECK_OPS):
        problems.append(
            f"string_check operation {grader.get('operation')!r} not in {STRING_CHECK_OPS}"
        )
    if gtype == "text_similarity":
        metric = grader.get("evaluation_metric")
        if metric is not None and metric not in TEXT_SIMILARITY_METRICS:
            problems.append(
                f"text_similarity metric {metric!r} not in {TEXT_SIMILARITY_METRICS}"
            )
    if gtype == "multi":
        subs = grader.get("graders")
        if isinstance(subs, dict):
            for key, sub in subs.items():
                for p in validate_grader(sub):
                    problems.append(f"sub-grader {key!r}: {p}")

    for ref in _template_refs(grader):
        ns = ref.split(".", 1)[0]
        if ns not in _TEMPLATE_NS:
            problems.append(
                f"template {{{{ {ref} }}}} uses unknown namespace {ns!r} "
                f"(expected one of {_TEMPLATE_NS})"
            )
    return problems


def validate_rft_example(row: dict) -> list[str]:
    """Validate one RFT dataset row. Returns problems.

    The row must carry a non-empty ``messages`` chat list whose **final message
    is role ``user``** (RFT rolls the model's turn forward from that prompt), and
    every message needs a valid ``role``. Extra top-level keys are allowed -- they
    are the ground-truth fields the grader reads via ``{{ item.* }}``.
    """
    problems: list[str] = []
    if not isinstance(row, dict):
        return ["example is not a JSON object"]

    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        problems.append("example has no non-empty 'messages' list")
        return problems

    valid_roles = {"system", "developer", "user", "assistant", "tool"}
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or "role" not in msg:
            problems.append(f"message #{i + 1} is not a role-bearing object")
            continue
        if msg.get("role") not in valid_roles:
            problems.append(f"message #{i + 1} has invalid role {msg.get('role')!r}")

    last = messages[-1]
    if isinstance(last, dict) and last.get("role") != "user":
        problems.append(
            f"final message role must be 'user', got {last.get('role')!r} "
            "(RFT generates the model's turn from the trailing user prompt)"
        )
    return problems


def validate_rft_dataset(
    rows: Sequence[dict],
    *,
    grader: dict | None = None,
    split: str = "dataset",
) -> list[str]:
    """Validate every row of one RFT split; optionally cross-check the grader.

    When ``grader`` is given, checks that each ``{{ item.<field> }}`` the grader
    references is present on every row (so grading won't fault at train time).
    """
    problems: list[str] = []
    if not rows:
        return [f"{split}: no examples"]
    for i, row in enumerate(rows):
        for p in validate_rft_example(row):
            problems.append(f"{split}[{i}]: {p}")

    if grader is not None:
        needed = grader_item_fields(grader)
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            missing = sorted(f for f in needed if f not in row)
            if missing:
                problems.append(
                    f"{split}[{i}]: grader references item fields not on the row: "
                    f"{', '.join(missing)}"
                )
    return problems


def validate_rft_splits(
    train: Sequence[dict],
    validation: Sequence[dict],
    *,
    grader: dict | None = None,
) -> list[str]:
    """Validate an RFT submission needs **both** a training and validation split.

    Returns the combined problem list (empty == ready to submit, modulo the
    doc-derived wire caveat).
    """
    problems: list[str] = []
    if grader is not None:
        problems.extend(f"grader: {p}" for p in validate_grader(grader))
    problems.extend(validate_rft_dataset(train, grader=grader, split="training"))
    problems.extend(
        validate_rft_dataset(validation, grader=grader, split="validation")
    )
    return problems


# --------------------------------------------------------------------------- #
# Payload builder (pure) + the impure submit seam                             #
# --------------------------------------------------------------------------- #


def build_rft_method(
    grader: dict,
    *,
    hyperparameters: dict | None = None,
    response_format: dict | None = None,
) -> dict:
    """Build the ``method`` block for ``fine_tuning.jobs.create`` (pure).

    Shapes ``{"type": "reinforcement", "reinforcement": {"grader": ..., ...}}``.
    Unknown hyperparameter names raise so a typo fails fast offline rather than
    at submit time.
    """
    if hyperparameters:
        unknown = sorted(set(hyperparameters) - set(RFT_HYPERPARAMETERS))
        if unknown:
            raise ValueError(
                f"unknown RFT hyperparameter(s): {', '.join(unknown)} "
                f"(known: {', '.join(RFT_HYPERPARAMETERS)})"
            )
    reinforcement: dict = {"grader": dict(grader)}
    if hyperparameters:
        reinforcement["hyperparameters"] = dict(hyperparameters)
    if response_format is not None:
        reinforcement["response_format"] = dict(response_format)
    return {"type": DEFAULT_METHOD_TYPE, "reinforcement": reinforcement}


def build_rft_job(
    *,
    model: str,
    training_file: str,
    validation_file: str,
    grader: dict,
    hyperparameters: dict | None = None,
    response_format: dict | None = None,
    suffix: str | None = None,
    seed: int | None = None,
) -> dict:
    """Build the full ``fine_tuning.jobs.create(**job)`` kwargs (pure, offline).

    ``training_file`` / ``validation_file`` are uploaded file IDs (see
    :func:`upload_file`). RFT **requires both** splits. Returns a dict ready to
    splat into the impure :func:`submit_rft_job`.
    """
    if not validation_file:
        raise ValueError("RFT requires a validation_file in addition to training_file")
    job: dict = {
        "model": model,
        "training_file": training_file,
        "validation_file": validation_file,
        "method": build_rft_method(
            grader,
            hyperparameters=hyperparameters,
            response_format=response_format,
        ),
    }
    if suffix is not None:
        job["suffix"] = suffix
    if seed is not None:
        job["seed"] = seed
    return job


def _openai_client(endpoint: str | None = None):
    """Build the Foundry-backed OpenAI client (deferred import, impure).

    Mirrors :class:`castia.model.Model`'s auth path so submission runs as the
    same identity. Raises a clean error when the project endpoint is unset.
    """
    resolved = endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not resolved:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT is not set -- RFT submission needs a Foundry "
            "project endpoint (or pass endpoint=...)."
        )
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=resolved, credential=DefaultAzureCredential())
    return project.get_openai_client()


def upload_file(path: str | os.PathLike, *, endpoint: str | None = None):
    """Upload an RFT split as a fine-tune file; returns the file id (impure).

    Billable-adjacent (stores data in the project). Kept separate from
    :func:`submit_rft_job` so a caller can upload once and reuse the ids.
    """
    client = _openai_client(endpoint)
    with open(path, "rb") as fh:
        created = client.files.create(file=fh, purpose="fine-tune")
    return created.id


def submit_rft_job(job: dict, *, endpoint: str | None = None):
    """Submit an RFT job to the Foundry fine-tuning API (the impure seam).

    Shells to ``fine_tuning.jobs.create(**job)`` -- a **billable** training job
    (grading + compute; auto-pauses at the $5,000 cap). Build ``job`` with
    :func:`build_rft_job`. Returns the created job object.

    .. warning::
       The payload shape is **doc-derived and unvalidated against a live job**.
       Prefer :func:`build_rft_job` + a dry-run print until a real submission
       confirms the wire acceptance.
    """
    client = _openai_client(endpoint)
    return client.fine_tuning.jobs.create(**job)


# --------------------------------------------------------------------------- #
# Small pure IO helpers (offline)                                             #
# --------------------------------------------------------------------------- #


def load_jsonl(path: str | os.PathLike) -> list[dict]:
    """Read a JSONL file into a list of dicts (skips blank lines)."""
    rows: list[dict] = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return rows


def load_grader(path: str | os.PathLike) -> dict:
    """Read a grader definition from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: grader file must be a JSON object")  # noqa: TRY004
    return data

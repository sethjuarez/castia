"""Eval-suite tooling: generate/update a rubric + dataset, and validate the suite.

The Foundry eval generator (``azd ai agent eval generate``) turns an agent
instruction -- plus, optionally, recent traces -- into a *scored* eval suite:
a synthesized JSONL dataset **and** an auto-generated **rubric**. The rubric is a
custom evaluator whose ``local_uri`` points at a "rubric dimensions file": a
bare JSON/YAML list (or a ``dimensions:`` list) where each entry is
``{id, description, weight}`` plus an optional ``always_applicable`` flag (the
generator's ``eval_api.EvaluatorDimension``). Both the dataset and the rubric
file are **versioned server-side**; after editing the local files you re-upload a
new version with ``azd ai agent eval update``.

This is a different eval shape from the hand-authored baseline we already have
(``evaluators: [builtin.task_adherence]`` + per-case ``criteria`` in
``eval.jsonl``): a generated rubric is one reusable, weighted, multi-dimension
custom evaluator rather than a per-case instruction. Both are valid entries in
``eval.yaml``'s ``evaluators:`` list, and -- because ``eval.yaml`` is the *same*
superset config the optimizer reads -- a generated rubric can drive both
``eval run`` (scoring) and ``optimize`` (improvement).

The framework gives that tool three thin, principled seams:

* :func:`build_generate_argv` / :func:`build_update_argv` / :func:`build_run_argv`
  -- **pure** builders for the ``azd ai agent eval {generate,update,run}`` command
  lines, anchored to this repo's baseline (instruction file, agent name, eval
  config) so a caller need not remember the flag soup. They never run anything.
* :func:`run_azd` -- the one impure seam: shell out to ``azd``. Kept separate so
  the builders stay unit-testable without Azure. The generator submits *billable*
  Foundry jobs, so nothing here runs by accident.
* :func:`load_suite` + :func:`validate_suite` -- a pure, offline gate: parse
  ``eval.yaml``, resolve every evaluator/dataset ``local_uri``, and check that
  each rubric dimensions file exists and is well-formed. Unlike ``tools.json`` a
  rubric is *authored/generated*, not derived from code, so this validates
  referential integrity rather than reconciling against a code source. Run via
  ``python -m castia eval check``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ruamel.yaml import YAML

DEFAULT_CONFIG = "eval.yaml"
DEFAULT_INSTRUCTION_FILE = ".agent_configs/baseline/instructions.md"
_AZD_EVAL: tuple[str, ...] = ("azd", "ai", "agent", "eval")


def _yaml() -> YAML:
    try:
        from ruamel.yaml import YAML
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(
            "The eval tooling needs ruamel.yaml. Install the build-time extra "
            "with:  pip install 'castia[deploy]'   (or: pip install "
            "ruamel.yaml)."
        ) from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


# --------------------------------------------------------------------------- #
# Suite model (parsed from eval.yaml)                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RubricDimension:
    """One scored dimension of a generated rubric (``EvaluatorDimension``).

    The on-disk field is ``id`` (a stable slug like ``correct_outcome``), not
    ``name``. ``always_applicable`` marks a catch-all dimension the grader scores
    on every sample regardless of relevance (the generator emits it on the
    trailing ``general_quality`` dimension).
    """

    id: str
    description: str
    weight: float | None = None
    always_applicable: bool = False


@dataclass(frozen=True)
class EvaluatorRef:
    """One entry of ``eval.yaml``'s ``evaluators:`` list.

    Two shapes coexist and both are valid: a **bare string**
    (``builtin.task_adherence``) or a **mapping** -- a custom/generated evaluator
    carrying ``name`` / ``kind`` / ``local_uri`` (the rubric dimensions file) /
    ``version``.
    """

    name: str
    kind: str | None = None
    local_uri: str | None = None
    version: str | None = None
    builtin: bool = False


@dataclass(frozen=True)
class DatasetRef:
    """A ``dataset:`` / ``validation_dataset:`` block from ``eval.yaml``."""

    role: str  # "dataset" | "validation_dataset"
    local_uri: str | None = None
    dataset_file: str | None = None
    version: str | None = None


@dataclass
class EvalSuite:
    """The parsed ``eval.yaml`` -- agent, evaluators, and datasets."""

    path: Path
    name: str | None
    agent: dict
    evaluators: list[EvaluatorRef]
    datasets: list[DatasetRef]
    options: dict = field(default_factory=dict)

    @property
    def root(self) -> Path:
        """Directory ``local_uri`` paths resolve against (eval.yaml's folder)."""
        return self.path.parent


def _version_str(value) -> str | None:
    return None if value is None else str(value)


def _parse_evaluator(entry) -> EvaluatorRef:
    if isinstance(entry, str):
        return EvaluatorRef(name=entry, builtin=entry.startswith("builtin."))
    if isinstance(entry, dict):
        name = entry.get("name") or entry.get("evaluator_name") or ""
        return EvaluatorRef(
            name=str(name),
            kind=entry.get("kind") or entry.get("type"),
            local_uri=entry.get("local_uri"),
            version=_version_str(entry.get("version")),
            builtin=str(name).startswith("builtin."),
        )
    return EvaluatorRef(name=str(entry))


def _parse_dataset(role: str, block) -> DatasetRef | None:
    if not isinstance(block, dict):
        return None
    return DatasetRef(
        role=role,
        local_uri=block.get("local_uri"),
        dataset_file=block.get("dataset_file"),
        version=_version_str(block.get("version")),
    )


def load_suite(path: str | os.PathLike = DEFAULT_CONFIG) -> EvalSuite:
    """Parse ``eval.yaml`` into an :class:`EvalSuite` (evaluators + datasets)."""
    p = Path(path)
    data = _yaml().load(p.read_text(encoding="utf-8")) or {}

    evaluators = [_parse_evaluator(e) for e in (data.get("evaluators") or [])]

    datasets: list[DatasetRef] = []
    for role in ("dataset", "validation_dataset"):
        ds = _parse_dataset(role, data.get(role))
        if ds is not None:
            datasets.append(ds)

    return EvalSuite(
        path=p,
        name=data.get("name"),
        agent=dict(data.get("agent") or {}),
        evaluators=evaluators,
        datasets=datasets,
        options=dict(data.get("options") or {}),
    )


def read_rubric(path: str | os.PathLike) -> list[RubricDimension]:
    """Parse a rubric dimensions file into a list of :class:`RubricDimension`.

    Accepts the generator's shape (a ``dimensions:`` list) or a bare top-level
    list, in YAML or JSON (a JSON file is valid YAML). Each entry needs ``id``
    and ``description``; ``weight`` is optional and, when present, numeric.
    ``name`` is accepted as a legacy alias for ``id``.
    """
    raw = _yaml().load(Path(path).read_text(encoding="utf-8"))
    items = raw.get("dimensions") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []

    dims: list[RubricDimension] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        weight = item.get("weight")
        dims.append(
            RubricDimension(
                id=str(item.get("id") or item.get("name") or ""),
                description=str(item.get("description") or ""),
                weight=float(weight) if isinstance(weight, (int, float)) else None,
                always_applicable=bool(item.get("always_applicable", False)),
            )
        )
    return dims


# --------------------------------------------------------------------------- #
# Validation (pure, offline drift gate)                                        #
# --------------------------------------------------------------------------- #


@dataclass
class SuitePlan:
    """The outcome of validating an :class:`EvalSuite` for CI."""

    suite: EvalSuite
    rubrics: dict[str, list[RubricDimension]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_suite(suite: EvalSuite) -> SuitePlan:
    """Check every ``local_uri`` resolves and every rubric file is well-formed.

    Returns a :class:`SuitePlan`; ``problems`` is empty when the suite is
    internally consistent (a clean CI gate). ``notes`` carries advisory findings
    that are not failures (e.g. no evaluators declared).
    """
    plan = SuitePlan(suite=suite)
    root = suite.root

    if not suite.evaluators:
        plan.notes.append(
            "no evaluators declared -- run 'python -m castia eval generate' "
            "to synthesize a rubric, or add builtin.<name>"
        )

    for ev in suite.evaluators:
        if not ev.local_uri:
            if not ev.builtin and not ev.name:
                plan.problems.append("an evaluator entry has neither a name nor a local_uri")
            continue
        rubric_path = root / ev.local_uri
        label = ev.name or ev.local_uri
        if not rubric_path.exists():
            plan.problems.append(
                f"evaluator {label!r}: rubric dimensions file {ev.local_uri!r} does not exist"
            )
            continue
        try:
            dims = read_rubric(rubric_path)
        except (OSError, ValueError) as exc:
            plan.problems.append(f"evaluator {label!r}: cannot read rubric {ev.local_uri!r}: {exc}")
            continue
        if not dims:
            plan.problems.append(
                f"evaluator {label!r}: rubric {ev.local_uri!r} has no 'dimensions'"
            )
            continue
        plan.rubrics[label] = dims
        for i, dim in enumerate(dims):
            if not dim.id:
                plan.problems.append(f"evaluator {label!r}: dimension #{i + 1} has no id")
            if not dim.description:
                plan.problems.append(
                    f"evaluator {label!r}: dimension {dim.id or f'#{i + 1}'!r} has no description"
                )

    for ds in suite.datasets:
        if ds.local_uri and not (root / ds.local_uri).exists():
            plan.problems.append(
                f"{ds.role}: local_uri {ds.local_uri!r} does not exist"
            )

    return plan


# --------------------------------------------------------------------------- #
# Command builders (pure) + the one impure runner                             #
# --------------------------------------------------------------------------- #


def _flag(argv: list[str], name: str, value) -> None:
    if value is not None and value != "":
        argv.extend([name, str(value)])


def build_generate_argv(
    *,
    agent: str | None = None,
    gen_instruction: str | None = None,
    gen_instruction_file: str | None = None,
    eval_model: str | None = None,
    max_samples: int | None = None,
    evaluators: Iterable[str] = (),
    dataset: str | None = None,
    out_file: str = DEFAULT_CONFIG,
    trace_days: int | None = None,
    name: str | None = None,
    reset_defaults: bool = False,
    no_wait: bool = False,
    no_prompt: bool = False,
    project_endpoint: str | None = None,
) -> list[str]:
    """Build the ``azd ai agent eval generate`` command line (runs nothing).

    ``--evaluator`` is repeatable; every other flag is emitted only when set.
    Mirrors the extension's flag names exactly.
    """
    argv = list(_AZD_EVAL) + ["generate"]
    _flag(argv, "--agent", agent)
    _flag(argv, "--gen-instruction", gen_instruction)
    _flag(argv, "--gen-instruction-file", gen_instruction_file)
    _flag(argv, "--eval-model", eval_model)
    _flag(argv, "--max-samples", max_samples)
    for ev in evaluators:
        _flag(argv, "--evaluator", ev)
    _flag(argv, "--dataset", dataset)
    _flag(argv, "--out-file", out_file)
    _flag(argv, "--trace-days", trace_days)
    _flag(argv, "--name", name)
    _flag(argv, "--project-endpoint", project_endpoint)
    if reset_defaults:
        argv.append("--reset-defaults")
    if no_wait:
        argv.append("--no-wait")
    if no_prompt:
        argv.append("--no-prompt")
    return argv


def build_update_argv(
    *,
    config: str = DEFAULT_CONFIG,
    evaluator_only: bool = False,
    dataset_only: bool = False,
    no_prompt: bool = False,
) -> list[str]:
    """Build the ``azd ai agent eval update`` command line (runs nothing).

    Re-uploads a new version of the local rubric (and/or dataset) files and
    rewrites the ``version:`` fields in ``config`` on success.
    """
    argv = list(_AZD_EVAL) + ["update"]
    _flag(argv, "--config", config)
    if evaluator_only:
        argv.append("--evaluator-only")
    if dataset_only:
        argv.append("--dataset-only")
    if no_prompt:
        argv.append("--no-prompt")
    return argv


def build_run_argv(
    *,
    config: str = DEFAULT_CONFIG,
    name: str | None = None,
    no_wait: bool = False,
    no_prompt: bool = False,
) -> list[str]:
    """Build the ``azd ai agent eval run`` command line (runs nothing)."""
    argv = list(_AZD_EVAL) + ["run"]
    _flag(argv, "--config", config)
    _flag(argv, "--name", name)
    if no_wait:
        argv.append("--no-wait")
    if no_prompt:
        argv.append("--no-prompt")
    return argv


def run_azd(argv: Sequence[str], *, cwd: str | os.PathLike | None = None) -> int:
    """Shell out to ``azd`` (the only impure seam). Returns the exit code.

    Raises :class:`FileNotFoundError` with a clean message when ``azd`` is not on
    PATH, so the CLI can report it rather than dumping a traceback.
    """
    exe = argv[0] if argv else "azd"
    if shutil.which(str(exe)) is None:
        raise FileNotFoundError(
            f"{exe!r} is not on PATH -- install the Azure Developer CLI and the "
            "azure.ai.agents extension (azd extension install azure.ai.agents)."
        )
    return subprocess.run(list(argv), cwd=cwd, check=False).returncode


def _tracked_instruction_file(root: str | os.PathLike = ".") -> str | None:
    """The baseline instruction file, if present -- the natural gen source."""
    candidate = Path(root) / DEFAULT_INSTRUCTION_FILE
    return DEFAULT_INSTRUCTION_FILE if candidate.exists() else None

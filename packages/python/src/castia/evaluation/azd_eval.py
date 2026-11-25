"""Bridge from castia's ``eval.yaml`` to the ``azd ai eval`` extension.

``azd ai agent eval`` was removed in ``azure.ai.agents`` 1.0.0-beta.22: it now
prints migration guidance and fails. Its replacement is the
``azure.ai.evaluations`` extension (``azd ai eval``), which reads its own
``evals/azure.eval.yaml`` and runs only evals that have been deployed with
``azd ai eval create`` (or ``azd up``).

castia keeps ``eval.yaml`` as the superset config the optimizer also reads, and
uses the extension's own scaffolder (``azd ai eval init``) to derive
``azure.eval.yaml`` instead of hand-writing a beta schema. :func:`check_drift`
then flags edits that make the two files disagree.

Everything here is pure except :func:`discover_extensions`, which runs the local
``azd extension list`` command. Nothing in this module contacts Azure.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from castia.evaluation.suite import EvalSuite

EVALUATIONS_EXTENSION = "azure.ai.evaluations"
AGENTS_EXTENSION = "azure.ai.agents"
LEGACY_AGENTS_CUTOFF = "1.0.0-beta.22"
BACKEND_ENV = "CASTIA_EVAL_BACKEND"
BACKENDS = ("evaluations", "agent")
DEFAULT_EVALS_PATH = "evals"
EVAL_CONFIG_FILE = "azure.eval.yaml"
_AZD_AI_EVAL: tuple[str, ...] = ("azd", "ai", "eval")


class EvalPortError(ValueError):
    """A castia eval request that cannot be expressed for the selected backend."""


# --------------------------------------------------------------------------- #
# Backend selection                                                            #
# --------------------------------------------------------------------------- #


def version_key(version: str) -> tuple:
    """Sort key for ``MAJOR.MINOR.PATCH[-pre.N]`` versions (pre-releases sort first)."""

    match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+\S*)?\s*", version)
    if not match:
        raise EvalPortError(f"unrecognized extension version {version!r}")
    core = tuple(int(part) for part in match.group(1, 2, 3))
    pre = match.group(4)
    if pre is None:
        return (*core, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (*core, 0, parts)


def select_backend(extensions: Sequence[dict[str, Any]]) -> str:
    """Pick ``evaluations`` or the legacy ``agent`` backend from installed extensions."""

    installed = {
        str(ext.get("id")): str(ext.get("installedVersion") or ext.get("version") or "")
        for ext in extensions
        if isinstance(ext, dict) and ext.get("id")
    }
    if EVALUATIONS_EXTENSION in installed:
        return "evaluations"
    agents = installed.get(AGENTS_EXTENSION)
    if agents and version_key(agents) < version_key(LEGACY_AGENTS_CUTOFF):
        return "agent"
    found = f" ({AGENTS_EXTENSION} {agents} no longer runs evals)" if agents else ""
    raise EvalPortError(
        f"no usable azd eval extension{found}: install it with "
        f"'azd extension install {EVALUATIONS_EXTENSION}' (needs azd >= 1.37)"
    )


Runner = Callable[..., subprocess.CompletedProcess]


def discover_extensions(runner: Runner = subprocess.run) -> list[dict[str, Any]]:
    """Return ``azd extension list --installed --output json`` (local, no Azure)."""

    argv = ["azd", "extension", "list", "--installed", "--output", "json"]
    try:
        proc = runner(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "'azd' is not on PATH -- install the Azure Developer CLI (>= 1.37) and "
            f"'azd extension install {EVALUATIONS_EXTENSION}'."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise EvalPortError(f"'azd extension list' failed (rc {proc.returncode}): {detail}")
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise EvalPortError("'azd extension list' did not return JSON") from exc
    if not isinstance(data, list):
        raise EvalPortError("'azd extension list' returned an unexpected shape")
    return data


def resolve_backend(
    explicit: str | None = None,
    *,
    discover: Callable[[], list[dict[str, Any]]] = discover_extensions,
) -> str:
    """Use an explicit backend (flag, then env) or detect one from installed extensions."""

    choice = (explicit or os.environ.get(BACKEND_ENV) or "auto").strip().lower()
    if choice in BACKENDS:
        return choice
    if choice != "auto":
        raise EvalPortError(f"unknown eval backend {choice!r}; use auto, evaluations or agent")
    return select_backend(discover())


# --------------------------------------------------------------------------- #
# azure.eval.yaml                                                              #
# --------------------------------------------------------------------------- #


def eval_config_path(path: str | os.PathLike = DEFAULT_EVALS_PATH) -> Path:
    """``<path>/azure.eval.yaml``, or ``path`` itself when it names a YAML file."""

    p = Path(path)
    return p if p.suffix in (".yaml", ".yml") else p / EVAL_CONFIG_FILE


def load_eval_definitions(path: str | os.PathLike = DEFAULT_EVALS_PATH) -> dict[str, Any]:
    """Parse ``azure.eval.yaml`` (raises :class:`FileNotFoundError` when absent)."""

    from castia.evaluation.suite import _yaml

    config = eval_config_path(path)
    data = _yaml().load(config.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise EvalPortError(f"{config}: expected a mapping")
    return data


def _evals(definitions: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in definitions.get("evals") or [] if isinstance(e, dict) and e.get("name")]


def select_eval(definitions: dict[str, Any], requested: str | None = None) -> str:
    """The eval to deploy or run: the requested one, or the only one declared."""

    names = [str(e["name"]) for e in _evals(definitions)]
    if requested:
        if requested not in names:
            raise EvalPortError(
                f"eval {requested!r} is not declared; declared: {', '.join(names) or '(none)'}"
            )
        return requested
    if len(names) == 1:
        return names[0]
    if not names:
        raise EvalPortError("azure.eval.yaml declares no evals; run 'castia eval init'")
    raise EvalPortError(f"several evals are declared ({', '.join(names)}); pass --eval NAME")


# --------------------------------------------------------------------------- #
# Command builders (pure)                                                      #
# --------------------------------------------------------------------------- #


def _flag(argv: list[str], name: str, value: object) -> None:
    if value is not None and value != "":
        argv.extend([name, str(value)])


def _common(argv: list[str], *, no_prompt: bool) -> list[str]:
    if no_prompt:
        argv.append("--no-prompt")
    return argv


def _relative(path: Path, cwd: Path) -> str:
    try:
        return os.path.relpath(path.resolve(), cwd.resolve())
    except ValueError:  # different drive on Windows
        return str(path.resolve())


def build_init_argv(
    suite: EvalSuite,
    *,
    path: str = DEFAULT_EVALS_PATH,
    name: str | None = None,
    evaluation_level: str | None = None,
    cwd: str | os.PathLike = ".",
) -> list[str]:
    """``azd ai eval init`` flags derived from castia's ``eval.yaml``."""

    target = suite.agent.get("name")
    if not target:
        raise EvalPortError(f"{suite.path}: agent.name is required to scaffold azure.eval.yaml")
    judge = suite.options.get("eval_model") or suite.agent.get("model")
    if not judge:
        raise EvalPortError(f"{suite.path}: options.eval_model (or agent.model) is required")
    dataset = next((d for d in suite.datasets if d.role == "dataset"), None)
    if dataset is None or not dataset.local_uri:
        raise EvalPortError(
            f"{suite.path}: dataset.local_uri (a local .jsonl) is required; "
            "registered datasets are not converted"
        )
    custom = [e.name or e.local_uri or "?" for e in suite.evaluators if not e.builtin]
    if custom:
        raise EvalPortError(
            "custom evaluators are not converted: "
            + ", ".join(custom)
            + ". Register each with 'azd ai eval evaluator create', declare it in "
            "azure.eval.yaml, and keep only builtin.<name> entries in eval.yaml for init"
        )

    argv = [*_AZD_AI_EVAL, "init", "--source", "dataset"]
    _flag(argv, "--dataset", _relative(suite.root / dataset.local_uri, Path(cwd)))
    _flag(argv, "--target", target)
    _flag(argv, "--judge-model", judge)
    builtins = [e.name for e in suite.evaluators if e.builtin]
    if builtins:
        _flag(argv, "--evaluator", ",".join(builtins))
    _flag(argv, "--evaluation-level", evaluation_level)
    _flag(argv, "--name", name)
    _flag(argv, "--path", path)
    return _common(argv, no_prompt=True)


def build_create_argv(
    eval_name: str,
    *,
    path: str = DEFAULT_EVALS_PATH,
    project_endpoint: str | None = None,
    no_prompt: bool = False,
) -> list[str]:
    """``azd ai eval create <name>``: deploy one eval definition."""

    argv = [*_AZD_AI_EVAL, "create", eval_name]
    _flag(argv, "--path", path)
    _flag(argv, "--project-endpoint", project_endpoint)
    return _common(argv, no_prompt=no_prompt)


def build_run_start_argv(
    *,
    eval_name: str,
    path: str = DEFAULT_EVALS_PATH,
    name: str | None = None,
    no_wait: bool = False,
    fail_on: str | None = None,
    max_samples: int | None = None,
    project_endpoint: str | None = None,
    no_prompt: bool = False,
) -> list[str]:
    """``azd ai eval run start``: run a deployed eval (billable)."""

    if fail_on and not re.fullmatch(r"any-failure|pass-rate=(0(\.\d+)?|1(\.0+)?)", fail_on):
        raise EvalPortError("--fail-on must be any-failure or pass-rate=<0..1>")
    argv = [*_AZD_AI_EVAL, "run", "start"]
    _flag(argv, "--path", path)
    _flag(argv, "--eval", eval_name)
    _flag(argv, "--name", name)
    _flag(argv, "--fail-on", fail_on)
    _flag(argv, "--max-samples", max_samples)
    _flag(argv, "--project-endpoint", project_endpoint)
    if no_wait:
        argv.append("--no-wait")
    return _common(argv, no_prompt=no_prompt)


def build_generate_argv(
    *,
    target: str | None = None,
    agent_instruction: str | None = None,
    agent_instruction_file: str | None = None,
    generation_model: str | None = None,
    max_samples: int | None = None,
    trace_days: int | None = None,
    artifact: str | None = None,
    dataset_name: str | None = None,
    evaluator_name: str | None = None,
    force: bool = False,
    no_wait: bool = False,
    path: str = DEFAULT_EVALS_PATH,
    project_endpoint: str | None = None,
    no_prompt: bool = False,
) -> list[str]:
    """``azd ai eval generate``: synthesize a dataset and/or rubric (billable)."""

    if agent_instruction and agent_instruction_file:
        raise EvalPortError("pass an agent instruction or an instruction file, not both")
    if artifact not in (None, "dataset", "evaluator"):
        raise EvalPortError("--artifact must be dataset or evaluator")
    argv = [*_AZD_AI_EVAL, "generate"]
    _flag(argv, "--target", target)
    _flag(argv, "--agent-instruction", agent_instruction)
    _flag(argv, "--agent-instruction-file", agent_instruction_file)
    _flag(argv, "--generation-model", generation_model)
    _flag(argv, "--max-samples", max_samples)
    _flag(argv, "--trace-days", trace_days)
    if artifact:
        argv.append(f"--{artifact}")
    _flag(argv, "--dataset-name", dataset_name)
    _flag(argv, "--evaluator-name", evaluator_name)
    _flag(argv, "--path", path)
    _flag(argv, "--project-endpoint", project_endpoint)
    if force:
        argv.append("--force")
    if no_wait:
        argv.append("--no-wait")
    return _common(argv, no_prompt=no_prompt)


def build_dataset_update_plan(
    definitions: dict[str, Any],
    *,
    path: str = DEFAULT_EVALS_PATH,
    project_endpoint: str | None = None,
    no_prompt: bool = False,
) -> list[list[str]]:
    """One ``azd ai eval dataset update`` per local dataset, after checking every file."""

    base = eval_config_path(path).parent
    plan: list[list[str]] = []
    missing: list[str] = []
    for ds in definitions.get("datasets") or []:
        if not isinstance(ds, dict) or not ds.get("name") or not ds.get("file"):
            continue
        file = base / str(ds["file"])
        if not file.exists():
            missing.append(f"{ds['name']} -> {file}")
            continue
        argv = [*_AZD_AI_EVAL, "dataset", "update", str(ds["name"]), "--from-file", str(file)]
        _flag(argv, "--project-endpoint", project_endpoint)
        plan.append(_common(argv, no_prompt=no_prompt))
    if missing:
        raise EvalPortError("dataset files not found: " + "; ".join(missing))
    if not plan:
        raise EvalPortError("azure.eval.yaml declares no local dataset files to update")
    return plan


# --------------------------------------------------------------------------- #
# Drift between eval.yaml and azure.eval.yaml                                  #
# --------------------------------------------------------------------------- #


def check_drift(
    suite: EvalSuite, definitions: dict[str, Any], *, path: str = DEFAULT_EVALS_PATH
) -> list[str]:
    """Problems where ``azure.eval.yaml`` no longer matches castia's ``eval.yaml``."""

    problems: list[str] = []
    base = eval_config_path(path).parent
    files = {
        str(d.get("name")): (base / str(d["file"])).resolve()
        for d in definitions.get("datasets") or []
        if isinstance(d, dict) and d.get("file")
    }
    agent = suite.agent.get("name")
    builtins = {e.name for e in suite.evaluators if e.builtin}
    dataset = next((d for d in suite.datasets if d.role == "dataset" and d.local_uri), None)
    local = (suite.root / dataset.local_uri).resolve() if dataset else None
    for ev in _evals(definitions):
        label = f"azure.eval.yaml eval {ev['name']!r}"
        target = (ev.get("target") or {}).get("name") if isinstance(ev.get("target"), dict) else None
        if agent and target and target != agent:
            problems.append(f"{label}: target {target!r} != eval.yaml agent.name {agent!r}")
        declared = {
            str(e.get("evaluator"))
            for e in ev.get("evaluators") or []
            if isinstance(e, dict) and str(e.get("evaluator", "")).startswith("builtin.")
        }
        if builtins and declared != builtins:
            problems.append(
                f"{label}: builtin evaluators {sorted(declared)} != eval.yaml {sorted(builtins)}"
            )
        used = files.get(str(ev.get("dataset")))
        if local and used and used != local:
            problems.append(f"{label}: dataset file {used} != eval.yaml dataset {local}")
    return problems

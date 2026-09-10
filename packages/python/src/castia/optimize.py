"""Code-first optimizer prep: keep the ``.agent_configs`` baseline in sync with the Agent.

The Agent Optimizer reads an on-disk **baseline** (``.agent_configs/baseline/``)
and searches for a better one. Two parts of that baseline are *derived from code*
and so can silently drift from what the agent actually serves:

* ``tools.json`` -- the tool set the optimizer is allowed to rewrite. Without it
  the optimizer can only tune the system prompt. This module generates it
  straight from the Agent's **declared** tools (:meth:`~castia.Router.tools`), so
  a tool's description/parameters in ``tools.json`` always match the code.
* ``metadata.yaml``'s ``tool_file`` pointer -- must reference ``tools.json`` for
  the optimizer to load it.

Like :mod:`castia.deploy`, this is a pure build-time reconciler: it reads the
composed :class:`~castia.Agent`, computes the desired baseline assets, and
either writes them or (``check=True``) reports drift for CI. It never runs the
agent or touches Azure. The human-authored parts of the baseline (``model``,
``instructions.md``) are validated but never invented. Run it via
``python -m castia optimize``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .application import Agent
from .optimization import tools_json, tools_json_names

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ruamel.yaml import YAML

DEFAULT_APP = "main:app"
DEFAULT_CONFIG_DIR = ".agent_configs"
DEFAULT_CANDIDATE = "baseline"
_TOOLS_FILE = "tools.json"
_METADATA_FILE = "metadata.yaml"


@dataclass
class OptimizePlan:
    """The outcome of reconciling ``.agent_configs/baseline`` with the Agent."""

    baseline_dir: Path
    tools: list[str]
    tools_path: Path
    metadata_path: Path
    tools_changed: bool
    metadata_changed: bool
    written: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.tools_changed or self.metadata_changed


def _yaml() -> YAML:
    try:
        from ruamel.yaml import YAML
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(
            "The optimize generator needs ruamel.yaml. Install the build-time "
            "extra with:  pip install 'castia[optimize]'   (or: pip install "
            "ruamel.yaml)."
        ) from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _read_tools_json(path: Path) -> list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # malformed -> treat as drift
        return None


def generate_optimizer_config(
    app: Agent,
    config_dir: str | os.PathLike = DEFAULT_CONFIG_DIR,
    *,
    candidate: str = DEFAULT_CANDIDATE,
    check: bool = False,
) -> OptimizePlan:
    """Reconcile the baseline ``tools.json`` (+ tool-file pointers) with ``app``.

    With ``check=True`` nothing is written; the returned :class:`OptimizePlan`
    reports whether a write *would* change the baseline (a CI drift gate). The
    system prompt (``instructions.md``) and ``model`` are validated for presence
    but left exactly as authored.
    """
    baseline = Path(config_dir) / candidate
    tools_path = baseline / _TOOLS_FILE
    metadata_path = baseline / _METADATA_FILE

    declared = app.registered_tools()
    desired = tools_json(declared)
    tool_names = tools_json_names(declared)
    notes: list[str] = []

    # --- tools.json drift ------------------------------------------------- #
    current = _read_tools_json(tools_path)
    if desired:
        tools_changed = current != desired
    else:
        # No declared tools: only "changed" if a stale tools.json exists.
        tools_changed = tools_path.exists()
        if not tools_path.exists():
            notes.append(
                "no tools declared on the Agent (app.tools(...)); tool-description "
                "optimization is inactive -- the optimizer can only tune the prompt"
            )

    # --- metadata.yaml tool-file pointers --------------------------------- #
    metadata_changed = False
    if not metadata_path.exists():
        notes.append(
            f"missing {metadata_path} -- the optimizer baseline needs a "
            "metadata.yaml (model + instruction_file)"
        )
    else:
        meta = _yaml().load(metadata_path.read_text(encoding="utf-8")) or {}
        instr = meta.get("instruction_file")
        if instr and not (baseline / instr).exists():
            notes.append(f"metadata instruction_file {instr!r} does not exist")
        if not meta.get("model"):
            notes.append("metadata.yaml has no 'model' -- authored baseline model missing")
        if desired and (
            meta.get("tool_file") != _TOOLS_FILE
            or meta.get("tools_file") != _TOOLS_FILE
        ):
            metadata_changed = True

    plan = OptimizePlan(
        baseline_dir=baseline,
        tools=tool_names,
        tools_path=tools_path,
        metadata_path=metadata_path,
        tools_changed=tools_changed,
        metadata_changed=metadata_changed,
        notes=notes,
    )

    if check or not plan.changed:
        return plan

    baseline.mkdir(parents=True, exist_ok=True)

    if tools_changed and desired:
        tools_path.write_text(
            json.dumps(desired, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    if metadata_changed and metadata_path.exists():
        yaml = _yaml()
        meta = yaml.load(metadata_path.read_text(encoding="utf-8")) or {}
        meta["tool_file"] = _TOOLS_FILE
        # Compatibility with azd ai agent optimize, whose Go-side metadata
        # schema currently uses "tools_file" while the Python optimizer package
        # uses "tool_file".
        meta["tools_file"] = _TOOLS_FILE
        with metadata_path.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.dump(meta, handle)

    plan.written = True
    return plan

"""``castia doctor``: offline checks for telemetry identity and model labelling.

Foundry Monitor and Optimizer attribute usage with two values that are easy to
get wrong without any error:

* the project ARM resource ID, stamped as ``microsoft.foundry.project.id``.
  Spans without it show $0 and 0 tokens in Monitor;
* ``AZURE_AI_MODEL_DEPLOYMENT_NAME``, which labels an agent version even when
  the code calls a different ``Model(deployment)``.

These checks read local files and the supplied environment only. They make no
network calls and never print environment values other than deployment names.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from castia.building.preflight import Diagnostic, Status

PROJECT_ID_KEYS = (
    "FOUNDRY_PROJECT_RESOURCE_ID",
    "AZURE_AI_PROJECT_RESOURCE_ID",
    "AZURE_AI_PROJECT_ID",
)
MODEL_ENV = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
_ENV_FIELDS = ("env", "env_vars", "environment_variables")
_PROJECT_ARM_ID = re.compile(
    r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
    r"Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+/?",
    re.IGNORECASE,
)
_SKIP_DIRS = {"node_modules", "__pycache__", "tests", "test", "venv", "site-packages"}
_MAX_SOURCE_FILES = 500


@dataclass(frozen=True)
class DoctorReport:
    root: Path
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not any(item.status == "fail" for item in self.diagnostics)

    @property
    def warnings(self) -> int:
        return sum(item.status == "warning" for item in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "ok": self.ok,
            "warnings": self.warnings,
            "scope": "offline",
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _present(env: Mapping[str, str], key: str) -> bool:
    value = env.get(key)
    return isinstance(value, str) and bool(value.strip())


def _hosted_services(root: Path) -> dict[str, dict[str, Any]] | None:
    """Hosted agent services from ``azure.yaml``; ``None`` when absent/unreadable."""
    manifest = root / "azure.yaml"
    if not manifest.is_file():
        return None
    from ruamel.yaml import YAML

    doc = YAML(typ="safe").load(manifest.read_text(encoding="utf-8"))
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict):
        return {}
    return {
        str(name): service
        for name, service in services.items()
        if isinstance(service, dict) and service.get("host") == "azure.ai.agent"
    }


def singular_toolbox_paths(root: str | os.PathLike[str]) -> list[str]:
    """Dotted paths in ``azure.yaml`` ``services`` that use the singular
    ``toolbox`` key, which azure.ai.agents 1.0.0-beta.22 rejects in favor of a
    ``toolboxes`` list."""
    manifest = Path(root) / "azure.yaml"
    if not manifest.is_file():
        return []
    from ruamel.yaml import YAML

    doc = YAML(typ="safe").load(manifest.read_text(encoding="utf-8"))
    services = doc.get("services") if isinstance(doc, dict) else None
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                child = f"{path}.{key}"
                if key == "toolbox":
                    found.append(child)
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    if isinstance(services, Mapping):
        walk(services, "services")
    return found


def _service_env(service: Mapping[str, Any]) -> dict[str, Any]:
    env: dict[str, Any] = {}
    for field in _ENV_FIELDS:
        value = service.get(field)
        if isinstance(value, Mapping):
            env.update({str(key): item for key, item in value.items()})
    return env


def _python_sources(root: Path) -> Iterator[Path]:
    count = 0
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in _SKIP_DIRS
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                count += 1
                if count > _MAX_SOURCE_FILES:
                    return
                yield Path(directory) / filename


def model_literals(root: str | os.PathLike[str]) -> list[tuple[str, int, str]]:
    """``(relative path, line, deployment)`` for literal ``Model(...)`` deployments.

    Static and best effort: only string literals passed as the first positional
    argument or as ``deployment=`` are seen. Tests and hidden/virtualenv folders
    are skipped.
    """
    base = Path(root).absolute()
    found: list[tuple[str, int, str]] = []
    for path in _python_sources(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name != "Model":
                continue
            candidates = [node.args[0]] if node.args else []
            candidates += [kw.value for kw in node.keywords if kw.arg == "deployment"]
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
                    relative = path.relative_to(base).as_posix()
                    found.append((relative, node.lineno, value.value.strip()))
    return found


def _baseline_model(root: Path) -> str | None:
    metadata = root / ".agent_configs" / "baseline" / "metadata.yaml"
    if not metadata.is_file():
        return None
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(metadata.read_text(encoding="utf-8"))
    model = data.get("model") if isinstance(data, dict) else None
    return model.strip() if isinstance(model, str) and model.strip() else None


def identity_checks(
    project: str | os.PathLike[str] = ".",
    *,
    env_file: str | os.PathLike[str] = ".env",
    environment: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Check the project resource ID and model-deployment label offline.

    ``environment`` replaces the process environment; ``env_file`` values are
    layered underneath it (process values win), as ``castia dev`` does.
    """
    from castia.building.dev import merged_env

    root = Path(project).absolute()
    env = merged_env(root, env_file=env_file, base=environment)
    checks: list[Diagnostic] = []

    def add(check: str, status: Status, message: str) -> None:
        checks.append(Diagnostic(check, status, message))

    # Project resource ID: local env.
    local_key = next((key for key in PROJECT_ID_KEYS if _present(env, key)), None)
    if local_key is None:
        add(
            "identity.project_resource_id",
            "warning",
            "No project ARM resource ID is set (FOUNDRY_PROJECT_RESOURCE_ID, "
            "AZURE_AI_PROJECT_RESOURCE_ID or AZURE_AI_PROJECT_ID). Spans will lack "
            "microsoft.foundry.project.id and Foundry Monitor will show $0 and 0 tokens.",
        )
    elif not _PROJECT_ARM_ID.fullmatch(env[local_key].strip()):
        add(
            "identity.project_resource_id",
            "warning",
            f"{local_key} is not a Foundry project ARM resource ID "
            "(/subscriptions/.../accounts/<account>/projects/<project>); "
            "an endpoint URL here does not identify the project in Monitor.",
        )
    else:
        add("identity.project_resource_id", "pass", f"{local_key} is a project ARM resource ID.")

    # Project resource ID and model label: hosted manifest.
    try:
        services = _hosted_services(root)
    except Exception as exc:  # noqa: BLE001 - YAML errors are reported, not raised
        services = None
        add("identity.manifest", "warning", f"azure.yaml could not be read ({type(exc).__name__}).")
    manifest_models: dict[str, str] = {}
    if services is None:
        add("identity.manifest", "skipped", "No azure.yaml; hosted env was not checked.")
    elif not services:
        add("identity.manifest", "skipped", "azure.yaml declares no azure.ai.agent services.")
    for name, service in (services or {}).items():
        service_env = _service_env(service)
        declared = [key for key in PROJECT_ID_KEYS if key in service_env]
        if declared:
            add(
                f"identity.manifest.{name}.project_resource_id",
                "pass",
                f"Hosted env declares {declared[0]}.",
            )
        else:
            add(
                f"identity.manifest.{name}.project_resource_id",
                "warning",
                "Hosted env does not declare the project ARM resource ID, so hosted "
                "spans will not reach Foundry Monitor with cost and tokens. Add "
                "`AZURE_AI_PROJECT_RESOURCE_ID: ${AZURE_AI_PROJECT_ID}` under the "
                "service env (FOUNDRY_* names are reserved for hosted agents).",
            )
        model_value = service_env.get(MODEL_ENV)
        if model_value is None:
            add(
                f"identity.manifest.{name}.model",
                "warning",
                f"Hosted env does not declare {MODEL_ENV}; Optimizer and Monitor "
                "cannot label this agent version with its model.",
            )
        elif isinstance(model_value, str) and "${" not in model_value and model_value.strip():
            manifest_models[f"azure.yaml services.{name}"] = model_value.strip()

    if services is not None:
        try:
            singular = singular_toolbox_paths(root)
        except Exception:  # noqa: BLE001 - already reported as identity.manifest
            singular = []
        if singular:
            add(
                "manifest.toolboxes",
                "warning",
                "azure.yaml uses the singular `toolbox` key at "
                + ", ".join(singular)
                + "; azd ai agents 1.0.0-beta.22 and later reject it. Replace it with a "
                "`toolboxes:` list.",
            )

    # Model label versus the deployments the code calls.
    configured = env.get(MODEL_ENV, "").strip()
    literals = model_literals(root)
    sources = {f"{path}:{line}": value for path, line, value in literals}
    try:
        baseline = _baseline_model(root)
    except Exception:  # noqa: BLE001 - preflight owns baseline validity
        baseline = None
    if baseline:
        sources[".agent_configs/baseline/metadata.yaml"] = baseline
    sources.update(manifest_models)
    if not configured:
        add(
            "identity.model_deployment",
            "warning",
            f"{MODEL_ENV} is not set; Optimizer and Monitor label versions with it.",
        )
    else:
        mismatched = {where: value for where, value in sources.items() if value != configured}
        if mismatched:
            details = "; ".join(f"{where} uses {value!r}" for where, value in mismatched.items())
            add(
                "identity.model_deployment",
                "warning",
                f"{MODEL_ENV}={configured!r} differs from: {details}. Optimizer and "
                "Monitor label the version with the env var, so usage is attributed "
                "to the wrong model. Make them agree before creating the version.",
            )
        else:
            add(
                "identity.model_deployment",
                "pass",
                f"{MODEL_ENV}={configured!r} matches every literal model reference found.",
            )
    return DoctorReport(root, tuple(checks))

"""Local readiness diagnostics. Passing is not cloud/deployment validation."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from ._offline import offline_scope
from .scaffold import _no_links

if TYPE_CHECKING:
    from ..application import Agent

Status = Literal["pass", "fail", "warning", "skipped"]


@dataclass(frozen=True)
class Diagnostic:
    check: str
    status: Status
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PreflightReport:
    root: Path
    app_target: str
    diagnostics: tuple[Diagnostic, ...]
    protocols: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(item.status == "fail" for item in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "app_target": self.app_target,
            "ok": self.ok,
            "scope": "offline",
            "protocols": list(self.protocols),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _local_file(root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or raw.drive or ".." in raw.parts:
        raise ValueError("Artifact references must remain inside the project.")
    path = root / raw
    _no_links(path)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("A required local artifact is missing or outside the project.")
    return path


def _load_agent(root: Path, target: str) -> Agent:
    """Load trusted local registration without leaving import/cache mutations."""
    from ..application import Agent

    module, sep, attr = target.partition(":")
    if (
        not sep
        or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", module)
        or not re.fullmatch(r"[A-Za-z_]\w*", attr)
    ):
        raise ValueError("Use a local 'module:attribute' target.")
    source = _local_file(root, str(Path(*module.split(".")).with_suffix(".py")))
    unique = f"_castia_preflight_{uuid4().hex}"
    # Preserve package context for relative imports from dotted entrypoints.
    qualified = f"{module.rpartition('.')[0]}.{unique}" if "." in module else unique
    spec = importlib.util.spec_from_file_location(qualified, source)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the local agent module.")
    original_path = list(sys.path)
    original_modules = dict(sys.modules)
    original_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(root))
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = loaded
        spec.loader.exec_module(loaded)
        app = getattr(loaded, attr, None)
        if not isinstance(app, Agent):
            raise TypeError("The module attribute must be a castia.Agent.")
        return app
    finally:
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
        for key, value in list(sys.modules.items()):
            location = getattr(value, "__file__", None)
            if key == qualified or (
                key not in original_modules
                and not key.startswith("castia")
                and location
                and Path(location).resolve().is_relative_to(root)
            ):
                sys.modules.pop(key, None)


def preflight(
    project: str | os.PathLike[str] = ".",
    *,
    app_target: str = "main:app",
    environment: Mapping[str, str] | None = None,
    required_env: Sequence[str] = (
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    ),
    deployment: bool = False,
) -> PreflightReport:
    """Check registration, configuration, manifest and lifecycle artifacts offline.

    ``environment`` replaces (not merges with) the environment used for presence
    checks. It is never installed into ``os.environ`` or included in diagnostics.
    The local entrypoint is trusted Python: registration is imported under
    outbound-I/O guards, but arbitrary Python side effects are not sandboxed.
    No handlers, model/dependency factories or registered tool providers run.
    No credential, RBAC, deployment or cloud endpoint validation is attempted.
    Code-deploy context is advisory unless ``deployment=True``. This checks
    supplied/process variables, not azd's persisted environment; export the same
    nonsecret context used by the selected azd environment before checking.
    Tenant resolution/authentication remains unverified without Azure calls.
    """
    root = Path(project).absolute()
    checks: list[Diagnostic] = []
    protocols: tuple[str, ...] = ()

    def add(check: str, status: Status, message: str) -> None:
        checks.append(Diagnostic(check, status, message))

    env = os.environ if environment is None else environment
    for key in required_env:
        present = isinstance(env.get(key), str) and bool(env[key].strip())
        add(
            f"configuration.{key}",
            "pass" if present else "fail",
            "Required setting is present." if present else "Required setting is missing.",
        )
    add("cloud", "skipped", "Credentials, RBAC, model availability and provisioning not checked.")
    try:
        _no_links(root)
        if not root.is_dir():
            raise ValueError("Project directory does not exist.")
    except (OSError, ValueError):
        add("project", "fail", "Project must be an existing directory without symlinks.")
        return PreflightReport(root, app_target, tuple(checks))

    with offline_scope():
        app = None
        try:
            app = _load_agent(root, app_target)
            protocols = tuple(app.registered_protocols())
            if not protocols:
                add("registration", "fail", "Agent has no registered protocol handlers.")
            else:
                from ..server import build_app

                build_app(app._routes, app._wire, app._invokes)
                add("registration", "pass", "Agent registration and ASGI route compilation succeeded.")
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - report import failures, redact details
            # Import/SDK errors can contain tokens, URLs or environment values.
            add("registration", "fail", f"Cannot load/compile Agent ({type(exc).__name__}).")
            app = None

        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
        except ImportError:
            add("artifacts", "fail", "Install castia[deploy] for local YAML validation.")
            return PreflightReport(root, app_target, tuple(checks), protocols)

        code_mode = False
        try:
            from ..deploy import generate_manifest

            manifest = _local_file(root, "azure.yaml")
            doc = yaml.load(manifest.read_text(encoding="utf-8"))
            services = doc["services"]
            if not isinstance(services, dict) or not services:
                raise ValueError("No services.")
            if app is None:
                add("manifest", "skipped", "Cannot compare deployment protocols without a valid Agent.")
            else:
                plan = generate_manifest(app, manifest, check=True)
                service = services[plan.service]
                code_mode = "codeConfiguration" in service
                if code_mode:
                    code = service["codeConfiguration"]
                    if not isinstance(code, dict) or not code.get("runtime"):
                        raise ValueError("Code deployment requires a runtime.")
                    _local_file(root, code["entryPoint"])
                    for key in ("AZURE_LOCATION", "AZURE_AI_PROJECT_ID", "AZURE_SUBSCRIPTION_ID"):
                        present = isinstance(env.get(key), str) and bool(env[key].strip())
                        add(
                            f"azd.{key}",
                            "pass" if present else "fail" if deployment else "warning",
                            "Deployment setting is present; resource existence is not checked."
                            if present else
                            "Code deployment needs this setting from your existing Foundry "
                            "project in the selected azd environment and supplied/process "
                            "environment. This check does not provision resources.",
                        )
                    project_id = env.get("AZURE_AI_PROJECT_ID", "")
                    if project_id and not re.fullmatch(
                        r"/subscriptions/[^/]+/resourceGroups/[^/]+/providers/"
                        r"Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+",
                        project_id.strip(), re.IGNORECASE,
                    ):
                        add(
                            "azd.project_arm_id",
                            "fail" if deployment else "warning",
                            "AZURE_AI_PROJECT_ID must be the project ARM resource ID, "
                            "not the project endpoint URL.",
                        )
                    add(
                        "azd.tenant",
                        "skipped",
                        "Tenant and login are not verified offline. azd resolves the tenant "
                        "from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login "
                        "targets the intended tenant. AZURE_TENANT_ID alone is not authentication.",
                    )
                    add(
                        "azd.environment",
                        "skipped",
                        "Only supplied/process settings were checked; the selected azd "
                        "environment was not read or modified.",
                    )
                versions_ok = all(
                    item.get("version") == "2.0.0" for item in service.get("protocols", [])
                )
                if plan.changed or not versions_ok:
                    add("manifest", "fail", "Manifest protocols/version differ from Agent registration.")
                elif service.get("host") != "azure.ai.agent":
                    add("manifest", "fail", "Service is not configured as an azure.ai.agent host.")
                elif service.get("kind") != "hosted" or not service.get("name"):
                    add(
                        "manifest", "fail",
                        "Inline agent definition requires kind: hosted and a name; "
                        "otherwise azd expects a separate agent definition file.",
                    )
                elif app.name and service["name"] != app.name:
                    add("manifest", "fail", "Inline hosted agent name differs from Agent registration.")
                else:
                    add("manifest", "pass", "Inline hosted definition and protocols match Agent registration.")
                if plan.skipped:
                    add("manifest.local_only", "warning", "Local-only protocols are not published.")
        except Exception as exc:  # noqa: BLE001 - YAML errors must be redacted diagnostics
            add("manifest", "fail", f"Invalid or missing azure.yaml ({type(exc).__name__}).")

        try:
            baseline = root / ".agent_configs" / "baseline"
            metadata = yaml.load(
                _local_file(baseline, "metadata.yaml").read_text(encoding="utf-8")
            )
            if not isinstance(metadata.get("model"), str) or not metadata["model"].strip():
                raise ValueError("Missing model.")
            instructions = _local_file(baseline, metadata["instruction_file"])
            if not instructions.read_text(encoding="utf-8").strip():
                raise ValueError("Empty instructions.")
            tool_path = metadata.get("tool_file") or metadata.get("tools_file")
            if metadata.get("tool_file", tool_path) != metadata.get("tools_file", tool_path):
                raise ValueError("Conflicting tool pointers.")
            tools = (
                json.loads(_local_file(baseline, tool_path).read_text(encoding="utf-8"))
                if tool_path else []
            )
            if not isinstance(tools, list):
                raise TypeError("Tools must be a list.")
            for tool in tools:
                if (
                    not isinstance(tool, dict)
                    or tool.get("type") != "function"
                    or not isinstance(tool.get("function"), dict)
                    or not tool["function"].get("name")
                ):
                    raise ValueError("Malformed baseline tool definition.")
            if app is not None and not app._tool_providers and tools:
                raise ValueError("Tools are not declared in the Agent.")
            add("baseline", "pass", "Baseline model, instructions and any tool pointers are valid.")
            if app is not None and app._tool_providers:
                add("baseline.tools", "skipped", "Tool providers were not executed; tool drift not checked.")
            if "responses" in protocols and len(protocols) > 1:
                add(
                    "optimizer",
                    "warning",
                    "Consider a Responses-only sibling via app.responses_only() "
                    "to isolate optimization from the multi-protocol Agent.",
                )
        except Exception as exc:  # noqa: BLE001 - YAML errors must be redacted diagnostics
            add("baseline", "fail", f"Invalid or missing baseline artifacts ({type(exc).__name__}).")

        try:
            from ..evalsuite import load_suite, validate_suite

            suite = load_suite(_local_file(root, "eval.yaml"))
            for evaluator in suite.evaluators:
                if evaluator.local_uri:
                    _local_file(root, evaluator.local_uri)
            for dataset in suite.datasets:
                if dataset.local_uri:
                    dataset_path = _local_file(root, dataset.local_uri)
                    if dataset_path.suffix == ".jsonl":
                        rows = [
                            json.loads(line)
                            for line in dataset_path.read_text(encoding="utf-8").splitlines()
                            if line.strip()
                        ]
                        if not rows or not all(isinstance(row, dict) for row in rows):
                            raise ValueError("Dataset must contain JSON objects.")
            if suite.agent.get("config"):
                _local_file(root, suite.agent["config"])
            validation = validate_suite(suite)
            if not validation.ok or not suite.evaluators or not suite.datasets:
                raise ValueError("Missing evaluators/datasets or invalid references.")
            add("evaluation", "pass", "Eval suite references and rubric structure are valid.")
        except Exception as exc:  # noqa: BLE001 - YAML errors must be redacted diagnostics
            add("evaluation", "fail", f"Invalid or missing eval suite ({type(exc).__name__}).")

        build_files = ("requirements.txt",) if code_mode else ("Dockerfile", "requirements.txt")
        for filename in build_files:
            try:
                if not _local_file(root, filename).read_text(encoding="utf-8").strip():
                    raise ValueError("Empty build artifact.")
                add(f"build.{filename}", "pass", "Local build artifact is present.")
            except (OSError, ValueError):
                add(f"build.{filename}", "fail", "Required build artifact is missing or empty.")
        if code_mode:
            add("build.Dockerfile", "skipped", "Dockerfile is optional for code deployment.")
        add("container", "skipped", "Remote build, image build and cloud deployment were not run.")
    return PreflightReport(root, app_target, tuple(checks), protocols)

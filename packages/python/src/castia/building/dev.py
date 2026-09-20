"""Local developer workflow for Castia applications.

The dev helper is intentionally local-first: it loads gitignored configuration,
validates the app shape and optimizer baseline, and can start the entrypoint.
It does not invoke a model, list remote toolbox tools, or touch Foundry.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from castia.building._offline import offline_scope
from castia.building.preflight import (
    Diagnostic,
    PreflightReport,
    _load_agent,
    preflight,
)
from castia.integrations.toolbox import resolve_toolbox_endpoint
from castia.optimizing.baseline import DEFAULT_CONFIG_DIR, generate_optimizer_config

Status = Literal["pass", "fail", "warning", "skipped"]


@dataclass(frozen=True)
class DevReport:
    root: Path
    app_target: str
    env_file: Path
    local_url: str
    readiness_url: str
    responses_url: str
    preflight: PreflightReport
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.preflight.ok and not any(
            item.status == "fail" for item in self.diagnostics
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "app_target": self.app_target,
            "ok": self.ok,
            "scope": "local",
            "env_file": str(self.env_file),
            "local_url": self.local_url,
            "readiness_url": self.readiness_url,
            "responses_url": self.responses_url,
            "preflight": self.preflight.to_dict(),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def merged_env(
    root: str | os.PathLike[str] = ".",
    *,
    env_file: str | os.PathLike[str] = ".env",
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return ``base`` plus values from ``env_file`` without mutating ``os.environ``.

    Process values win, matching ``python-dotenv``'s default ``override=False``
    behavior used by the scaffolded starter.
    """
    env = dict(os.environ if base is None else base)
    path = _env_path(root, env_file)
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key.startswith("#"):
            continue
        env.setdefault(key, _clean_env_value(value))
    return env


def dev_preflight(
    project: str | os.PathLike[str] = ".",
    *,
    app_target: str = "main:app",
    env_file: str | os.PathLike[str] = ".env",
    host: str = "127.0.0.1",
    port: int | None = None,
    require_prompty: bool = False,
    optimize_check: bool = True,
    environment: Mapping[str, str] | None = None,
) -> DevReport:
    """Load local env and run dry checks for the expected Castia dev loop."""
    root = Path(project).absolute()
    env_path = _env_path(root, env_file)
    env = merged_env(root, env_file=env_path, base=environment)
    selected_port = _port(port, env)
    diagnostics: list[Diagnostic] = []

    def add(check: str, status: Status, message: str) -> None:
        diagnostics.append(Diagnostic(check, status, message))

    add(
        "env_file",
        "pass" if env_path.is_file() else "warning",
        "Loaded local .env values for checks and process start."
        if env_path.is_file()
        else "No .env file found; process environment only.",
    )

    report = preflight(root, app_target=app_target, environment=env)
    _check_prompty(root, require=require_prompty, add=add)
    _check_toolbox(env, add=add)
    if optimize_check:
        _check_optimizer_drift(root, app_target=app_target, add=add)
    else:
        add("optimizer.drift", "skipped", "Optimizer baseline drift check disabled.")

    local_url = f"http://{host}:{selected_port}"
    return DevReport(
        root=root,
        app_target=app_target,
        env_file=env_path,
        local_url=local_url,
        readiness_url=f"{local_url}/readiness",
        responses_url=f"{local_url}/responses",
        preflight=report,
        diagnostics=tuple(diagnostics),
    )


def run_dev_server(
    project: str | os.PathLike[str] = ".",
    *,
    app_target: str = "main:app",
    env_file: str | os.PathLike[str] = ".env",
    port: int | None = None,
) -> int:
    """Start the local app entrypoint with the same merged environment."""
    root = Path(project).absolute()
    env = merged_env(root, env_file=env_file)
    if port is not None:
        env["PORT"] = str(port)
    entrypoint = _entrypoint(root, app_target)
    return subprocess.run(
        [sys.executable, str(entrypoint.relative_to(root))],
        cwd=root,
        env=env,
        check=False,
    ).returncode


def _env_path(root: str | os.PathLike[str], env_file: str | os.PathLike[str]) -> Path:
    path = Path(env_file)
    return path if path.is_absolute() else Path(root).absolute() / path


def _clean_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _port(port: int | None, env: Mapping[str, str]) -> int:
    raw = str(port if port is not None else env.get("PORT", "8088")).strip()
    try:
        selected = int(raw)
    except ValueError as exc:
        raise ValueError("PORT must be an integer.") from exc
    if not 1 <= selected <= 65535:
        raise ValueError("PORT must be between 1 and 65535.")
    return selected


def _entrypoint(root: Path, app_target: str) -> Path:
    module, sep, _attr = app_target.partition(":")
    if not sep:
        raise ValueError("Use a local 'module:attribute' target.")
    path = root / Path(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        raise ValueError("App target module file is missing.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("App target must remain inside the project.")
    return path


def _check_prompty(
    root: Path,
    *,
    require: bool,
    add,
) -> None:
    has_prompty_files = any(root.glob("*.prompty"))
    try:
        import castia.prompty as castia_prompty

        castia_prompty._prompty()
    except Exception as exc:  # noqa: BLE001 - optional dependency gate
        if require or has_prompty_files:
            add(
                "prompty.imports",
                "fail",
                f"Prompty support is unavailable ({type(exc).__name__}); install castia[prompty].",
            )
        else:
            add(
                "prompty.imports",
                "skipped",
                "Prompty files not detected; install castia[prompty] when using the Prompty harness.",
            )
    else:
        add("prompty.imports", "pass", "Prompty optional imports are available.")


def _check_toolbox(env: Mapping[str, str], *, add) -> None:
    endpoint = resolve_toolbox_endpoint(env)
    configured = any(
        env.get(key)
        for key in (
            "TOOLBOX_ENDPOINT",
            "TOOLBOX_MCP_ENDPOINT",
            "TOOLBOX_NAME",
        )
    )
    if not endpoint:
        add(
            "toolbox.config",
            "fail" if configured else "skipped",
            "Toolbox environment is incomplete; set TOOLBOX_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT + TOOLBOX_NAME."
            if configured
            else "No toolbox environment configured; toolbox checks are inactive.",
        )
        return
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc or "/mcp" not in parsed.path:
        add("toolbox.config", "fail", "Toolbox MCP endpoint must be an HTTPS /mcp URL.")
        return
    add(
        "toolbox.config",
        "pass",
        "Toolbox MCP endpoint resolved. Remote tools/list connectivity is not run by default.",
    )


def _check_optimizer_drift(root: Path, *, app_target: str, add) -> None:
    try:
        with offline_scope():
            app = _load_agent(root, app_target)
            plan = generate_optimizer_config(
                app,
                root / DEFAULT_CONFIG_DIR,
                candidate="baseline",
                check=True,
            )
    except Exception as exc:  # noqa: BLE001 - redacted diagnostic, no values
        add(
            "optimizer.drift",
            "fail",
            f"Cannot compare Agent tools with .agent_configs/baseline ({type(exc).__name__}).",
        )
        return
    if plan.changed:
        add(
            "optimizer.drift",
            "fail",
            "Agent-declared tools and .agent_configs/baseline are out of sync; run python -m castia optimize.",
        )
    else:
        add("optimizer.drift", "pass", "Agent-declared tools match .agent_configs/baseline.")
    for note in plan.notes:
        add("optimizer.note", "warning", note)


def print_text_status(report: DevReport) -> None:
    """Emit concise, canvas-friendly status without secret values."""
    print(f"root         : {report.root}")
    print(f"status       : {'ready' if report.ok else 'blocked'}")
    print(f"local        : {report.local_url}")
    print(f"readiness    : {report.readiness_url}")
    print(f"responses    : {report.responses_url}")
    print(f"env          : {report.env_file}")
    for item in [*report.preflight.diagnostics, *report.diagnostics]:
        print(f"{item.status:7} {item.check}: {item.message}")


def diagnostics_to_dict(items: Sequence[Diagnostic]) -> list[dict[str, str]]:
    return [asdict(item) for item in items]

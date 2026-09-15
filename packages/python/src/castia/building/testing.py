"""Run a project's real tests in a dedicated, offline Python process."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .scaffold import _no_links


@dataclass(frozen=True)
class ProjectTestReport:
    root: Path
    status: Literal["pass", "fail", "error", "timeout"]
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.update(root=str(self.root), ok=self.ok, scope="offline")
        return result


def run_project_tests(
    project: str | os.PathLike[str] = ".",
    *,
    timeout: float = 60,
) -> ProjectTestReport:
    """Run ``PROJECT/tests`` with pytest in the current Python environment.

    Missing pytest is reported, never installed. A separate process isolates
    module imports/fixtures from the caller and is killed on timeout. The worker
    blocks common network/process paths and telemetry, disables automatic third-
    party pytest plugins, and permits nested ``AgentTestHarness`` contexts.
    Test output is returned verbatim: use synthetic fixtures, not credentials.
    This executes trusted project tests; the guards are not a security sandbox.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number of seconds")
    root = Path(project).absolute()
    _no_links(root)
    tests = root / "tests"
    _no_links(tests)
    if not root.is_dir() or not tests.is_dir():
        raise ValueError("Project must contain a tests directory.")
    if not any(tests.rglob("test_*.py")):
        raise ValueError("Project tests directory contains no test_*.py files.")
    environment = dict(os.environ)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    output_dir = root / f".castia-test-output-{uuid4().hex}"
    output_dir.mkdir()
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "castia.building._test_runner",
                str(tests), str(output_dir / "pytest"),
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProjectTestReport(root, "timeout", 124, "Local tests exceeded the timeout.")
    finally:
        shutil.rmtree(output_dir)
    status = "pass" if result.returncode == 0 else "fail" if result.returncode == 1 else "error"
    return ProjectTestReport(root, status, result.returncode, result.stdout + result.stderr)

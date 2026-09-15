"""Offline project creation, readiness checks, and in-process protocol tests.

These tools never provision infrastructure. ``AgentTestHarness`` uses temporary
process-wide patches: use it in a dedicated test process, not alongside a live
server. Importing this package does not import the ASGI or Azure runtimes.
"""

from .harness import AgentTestHarness, CapturedEgress
from .preflight import Diagnostic, PreflightReport, preflight
from .scaffold import ScaffoldReport, scaffold_project
from .testing import ProjectTestReport, run_project_tests

__all__ = [
    "AgentTestHarness",
    "CapturedEgress",
    "Diagnostic",
    "PreflightReport",
    "ProjectTestReport",
    "ScaffoldReport",
    "preflight",
    "run_project_tests",
    "scaffold_project",
]

"""Explicit feature coverage and deterministic local functional drift reports.

Suites select stable feature IDs, not arbitrary import strings or executable
configuration. A missing probe is uncovered; unmet prerequisites are blocked.
Only a probe returning an explicit pass establishes coverage.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from castia.observe.telemetry import classify_exception

STATUSES = frozenset({"pass", "fail", "blocked", "uncovered"})


@dataclass(frozen=True)
class Feature:
    id: str
    description: str
    prerequisites: tuple[str, ...]


FEATURE_CATALOG = tuple(
    Feature(feature_id, description, tuple(prerequisites.split()))
    for feature_id, description, prerequisites in (
        ("project.read", "Read the selected Foundry project", "project_read_url"),
        ("runtime.responses", "Hosted Responses protocol end-to-end", "hosted_responses_url"),
        ("runtime.invocations", "Hosted Invocations protocol end-to-end", "hosted_invocations_url hosted_invocations_body hosted_invocations_output_field"),
        ("runtime.activity", "Authenticated Activity protocol delivery", "activity_fixture"),
        ("runtime.routing", "Router composition and dispatch", "runtime_fixture"),
        ("runtime.dependencies", "Dependency injection and request context", "runtime_fixture"),
        ("runtime.errors", "Runtime error boundaries", "runtime_fixture"),
        ("teams.direct", "Teams direct message delivery", "teams_fixture"),
        ("teams.mention", "Teams mention routing", "teams_fixture"),
        ("teams.conversation", "Conversation lifecycle delivery", "teams_fixture"),
        ("teams.streaming", "Teams streaming message updates", "teams_fixture"),
        ("teams.rich", "Adaptive cards and rich message rendering", "teams_rich_fixture"),
        ("teams.actions", "Card submit and invoke actions", "teams_rich_fixture"),
        ("teams.proactive", "Proactive Teams delivery", "teams_proactive_fixture"),
        ("identity.authentication", "Incoming token validation", "identity_fixture"),
        ("identity.authorization", "Caller authorization boundaries", "identity_fixture"),
        ("identity.obo", "Delegated on-behalf-of token acquisition", "obo_fixture"),
        ("graph.read", "Delegated Microsoft Graph read", "graph_fixture"),
        ("graph.write", "Explicitly authorized Graph write fixture", "graph_write_fixture"),
        ("model.respond", "Foundry model Responses text", "model_url model"),
        ("model.stream", "Foundry model Responses streaming", "model_url model"),
        ("model.function_call", "Forced local function call and output round-trip", "model_url model"),
        ("model.reasoning", "Reasoning model configuration", "reasoning_fixture"),
        ("tools.execution", "Local tool execution and failure handling", "tool_fixture"),
        ("tools.toolbox", "Explicit selected toolbox MCP tool execution", "model_url model toolbox_url toolbox_tools toolbox_prompt"),
        ("tools.knowledge", "Knowledge-base retrieval", "knowledge_fixture"),
        ("telemetry.query", "Scoped Application Insights trace query", "app_insights"),
        ("telemetry.ingestion", "Tagged probe trace ingestion", "app_insights probe_tag"),
        ("eval.check", "Offline evaluation schema validation", "eval_fixture"),
        ("eval.run", "Bounded live evaluation evidence", "eval_live_fixture"),
        ("optimizer.status", "Read selected optimizer job", "project_endpoint optimizer_job_id"),
        ("optimizer.candidate", "Read selected optimizer candidate", "project_endpoint optimizer_job_id optimizer_candidate_id"),
        ("optimizer.run", "Opt-in bounded optimizer submission and polling", "project_endpoint optimizer_request allow_optimizer_submit"),
        ("finetune.check", "Offline fine-tuning input validation only", "finetune_fixture"),
        ("finetune.consume", "Invoke an already deployed fine-tuned model", "finetuned_model_fixture"),
        ("deploy.check", "Validate generated deployment artifacts", "deploy_fixture"),
        ("deploy.health", "Read deployed agent health", "deployment_health_fixture"),
    )
)
_FEATURES = {feature.id: feature for feature in FEATURE_CATALOG}


@dataclass(frozen=True)
class FunctionalSuite:
    name: str
    features: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or isinstance(self.schema_version, bool):
            raise ValueError("suite schema_version must be 1")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("suite name is required")
        if not isinstance(self.features, (list, tuple)) or not self.features:
            raise ValueError("suite features must be a nonempty list of feature IDs")
        if any(not isinstance(feature, str) or feature not in _FEATURES for feature in self.features):
            raise ValueError("suite contains an unknown feature ID")
        if len(set(self.features)) != len(self.features):
            raise ValueError("suite feature IDs must be unique")
        object.__setattr__(self, "features", tuple(self.features))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FunctionalSuite:
        if not isinstance(data, Mapping) or set(data) - {"name", "features", "schema_version"}:
            raise ValueError("suite must contain only name, features, and schema_version")
        return cls(
            name=data.get("name"), features=data.get("features"),
            schema_version=data.get("schema_version", 1),
        )

    @classmethod
    def load(cls, path: str | Path) -> FunctionalSuite:
        """Load JSON; YAML requires the existing deploy/optimize YAML extra."""
        file = Path(path)
        text = file.read_text(encoding="utf-8")
        if file.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            from ruamel.yaml import YAML
            data = YAML(typ="safe").load(text)
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "name": self.name, "features": list(self.features)}


@dataclass(frozen=True)
class ProbeResult:
    status: str
    diagnostic: str
    category: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("probe status must be pass, fail, blocked, or uncovered")
        if not isinstance(self.diagnostic, str):
            raise TypeError("probe diagnostic must be text")
        if not isinstance(self.evidence, Mapping):
            raise TypeError("probe evidence must be a mapping")


@dataclass(frozen=True)
class FeatureResult:
    feature_id: str
    selected: bool
    status: str
    diagnostic: str
    category: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    duration_seconds: float | None = None


@dataclass(frozen=True)
class SuiteReport:
    name: str
    generated_at: str
    results: tuple[FeatureResult, ...]
    comparison: Mapping[str, Any] | None = None

    @property
    def passed(self) -> bool:
        selected = [row for row in self.results if row.selected]
        drift = self.comparison and (
            self.comparison.get("regressions") or self.comparison.get("lost_coverage")
        )
        return bool(selected) and all(row.status == "pass" for row in selected) and not drift

    def to_dict(self) -> dict[str, Any]:
        selected = [row for row in self.results if row.selected]
        return {
            "schema_version": 1,
            "name": self.name,
            "generated_at": self.generated_at,
            "passed": self.passed,
            "counts": {status: sum(row.status == status for row in selected) for status in sorted(STATUSES)},
            "results": [asdict(row) for row in sorted(self.results, key=lambda row: row.feature_id)],
            "comparison": self.comparison,
            "cost": None,
            "cost_status": "unknown",
        }

    def to_json(self) -> str:
        """Canonical key order, finite numbers only, and a trailing newline."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n"


def compare_reports(
    current: SuiteReport | Mapping[str, Any], baseline: SuiteReport | Mapping[str, Any]
) -> dict[str, Any]:
    """Compare selected feature outcomes, never treat dropped coverage as green."""
    def outcomes(report: SuiteReport | Mapping[str, Any]) -> dict[str, str]:
        data = report.to_dict() if isinstance(report, SuiteReport) else report
        if not isinstance(data, Mapping) or data.get("schema_version") != 1:
            raise ValueError("baseline must be a schema_version 1 report")
        if not isinstance(data.get("results"), list):
            raise TypeError("baseline results must be a list")
        result = {}
        seen = set()
        for row in data["results"]:
            if (
                not isinstance(row, Mapping)
                or not isinstance(row.get("feature_id"), str)
                or row.get("status") not in STATUSES
                or not isinstance(row.get("selected"), bool)
            ):
                raise ValueError("baseline contains an invalid feature result")
            feature_id = row["feature_id"]
            if feature_id in seen:
                raise ValueError("baseline contains duplicate feature results")
            seen.add(feature_id)
            if row["selected"]:
                result[feature_id] = row["status"]
        return result

    before, after = outcomes(baseline), outcomes(current)
    changed = [
        {"feature_id": key, "before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    ]
    return {
        "changed": changed,
        "regressions": sorted(key for key, value in before.items() if value == "pass" and after.get(key) != "pass"),
        "lost_coverage": sorted(set(before) - set(after)),
        "new_coverage": sorted(set(after) - set(before)),
    }


def run_suite(
    suite: FunctionalSuite, *, probes: Mapping[str, Callable[[], ProbeResult]],
    prerequisites: Mapping[str, Any] | None = None,
    baseline: SuiteReport | Mapping[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SuiteReport:
    """Run only selected registered probes; never interpret None/bool as pass.

    Probe adapters enforce their own I/O budgets. This synchronous runner cannot
    preempt arbitrary injected Python callables. Built-in live probes share a
    bounded transport and a single budget per ``live_probes`` invocation.
    """
    if baseline is not None:
        compare_reports({"schema_version": 1, "results": []}, baseline)
    prerequisites = prerequisites or {}
    results = []
    for feature in FEATURE_CATALOG:
        if feature.id not in suite.features:
            results.append(FeatureResult(feature.id, False, "uncovered", "Not selected."))
            continue
        missing = [key for key in feature.prerequisites if not prerequisites.get(key)]
        if missing:
            results.append(FeatureResult(
                feature.id, True, "blocked", "Missing prerequisites: " + ", ".join(missing),
                "prerequisite",
            ))
            continue
        probe = probes.get(feature.id)
        if probe is None:
            results.append(FeatureResult(feature.id, True, "uncovered", "No probe registered.", "coverage"))
            continue
        started = clock()
        try:
            outcome = probe()
            if not isinstance(outcome, ProbeResult):
                raise TypeError("probes must return ProbeResult")
            # Invalid evidence is a categorized failure, not a report writer crash.
            json.dumps(dict(outcome.evidence), allow_nan=False)
        except Exception as exc:  # noqa: BLE001 - turn arbitrary probe failures into categorized evidence
            error = classify_exception(exc)
            blocked = error.category in {
                "authentication", "authorization", "dependency", "budget", "prerequisite",
            }
            outcome = ProbeResult(
                "blocked" if blocked else "fail", str(error), error.category,
            )
        results.append(FeatureResult(
            feature.id, True, outcome.status, outcome.diagnostic, outcome.category,
            outcome.evidence, max(0., clock() - started),
        ))
    generated = now()
    if generated.tzinfo is None:
        raise ValueError("report clock must return a timezone-aware datetime")
    report = SuiteReport(
        suite.name, generated.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        tuple(results),
    )
    if baseline is not None:
        report = SuiteReport(
            report.name, report.generated_at, report.results, compare_reports(report, baseline),
        )
    return report

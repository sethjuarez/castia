import json
from datetime import UTC, datetime

import pytest

from castia.observe import (
    FEATURE_CATALOG,
    FunctionalSuite,
    ObserveError,
    ProbeResult,
    compare_reports,
    run_suite,
)


def selected(report):
    return {result.feature_id: result for result in report.results if result.selected}


def test_catalog_has_unique_ids_and_all_surfaces():
    identifiers = [feature.id for feature in FEATURE_CATALOG]
    assert len(identifiers) == len(set(identifiers))
    assert {
        "project", "runtime", "teams", "identity", "graph", "model", "tools",
        "telemetry", "eval", "optimizer", "finetune", "deploy",
    } <= {identifier.split(".")[0] for identifier in identifiers}
    assert not any("submit" in identifier for identifier in identifiers if identifier.startswith("finetune"))


@pytest.mark.parametrize("features", [[], ["model.respond", "model.respond"], ["unknown"], "model.respond", [1]])
def test_suite_rejects_invalid_coverage(features):
    with pytest.raises(ValueError):
        FunctionalSuite("daily", features)


def test_suite_schema_rejects_unrecognized_controls():
    with pytest.raises(ValueError):
        FunctionalSuite.from_dict({"name": "daily", "features": ["model.respond"], "submit_finetune": True})
    with pytest.raises(ValueError):
        FunctionalSuite("daily", ["model.respond"], schema_version=2)


def test_missing_prerequisite_blocks_even_with_passing_probe():
    calls = []
    report = run_suite(
        FunctionalSuite("daily", ("teams.direct", "model.respond")),
        probes={"teams.direct": lambda: calls.append(True), "model.respond": lambda: ProbeResult("pass", "ok")},
        prerequisites={"model": "model", "model_url": "url"},
    )
    rows = selected(report)
    assert rows["teams.direct"].status == "blocked"
    assert rows["model.respond"].status == "pass"
    assert not report.passed and not calls


def test_no_probe_is_uncovered_never_skipped_as_pass():
    report = run_suite(
        FunctionalSuite("daily", ("graph.read",)), probes={},
        prerequisites={"graph_fixture": True},
    )
    assert selected(report)["graph.read"].status == "uncovered"
    assert not report.passed


@pytest.mark.parametrize("result", [None, True, False, "pass", {"status": "pass"}])
def test_non_typed_probe_results_fail(result):
    report = run_suite(
        FunctionalSuite("daily", ("project.read",)),
        probes={"project.read": lambda: result},
        prerequisites={"project_read_url": "url"},
    )
    assert selected(report)["project.read"].status == "fail"
    assert selected(report)["project.read"].category == "invalid_response"


@pytest.mark.parametrize(
    ("error", "status", "category"),
    [
        (ObserveError("authorization", "No access."), "blocked", "authorization"),
        (ObserveError("budget", "Exhausted."), "blocked", "budget"),
        (ObserveError("throttled", "Throttled."), "fail", "throttled"),
        (RuntimeError("Bearer SECRET"), "fail", "unexpected"),
    ],
)
def test_errors_have_categorized_safe_diagnostics(error, status, category):
    def probe():
        raise error
    report = run_suite(
        FunctionalSuite("daily", ("project.read",)), probes={"project.read": probe},
        prerequisites={"project_read_url": "url"},
    )
    result = selected(report)["project.read"]
    assert (result.status, result.category) == (status, category)
    assert "SECRET" not in report.to_json()


def test_canonical_report_and_baseline_regression_including_lost_coverage():
    now = lambda: datetime(2026, 9, 15, tzinfo=UTC)
    baseline = run_suite(
        FunctionalSuite("daily", ("project.read", "model.respond")),
        probes={"project.read": lambda: ProbeResult("pass", "ok"), "model.respond": lambda: ProbeResult("pass", "ok")},
        prerequisites={"project_read_url": "url", "model": "model", "model_url": "url"},
        now=now, clock=lambda: 0.,
    )
    assert baseline.passed
    current = run_suite(
        FunctionalSuite("daily", ("project.read",)), probes={},
        prerequisites={"project_read_url": "url"}, baseline=json.loads(baseline.to_json()),
        now=now, clock=lambda: 0.,
    )
    assert current.comparison["regressions"] == ["model.respond", "project.read"]
    assert current.comparison["lost_coverage"] == ["model.respond"]
    encoded = current.to_json()
    assert encoded.endswith("\n")
    assert encoded == json.dumps(json.loads(encoded), sort_keys=True, indent=2, allow_nan=False) + "\n"
    assert current.to_dict()["counts"]["uncovered"] == 1
    assert current.to_dict()["cost"] is None


@pytest.mark.parametrize(
    "baseline", [{}, {"schema_version": 1, "results": [{}]}, {"schema_version": 1, "results": None}]
)
def test_invalid_baseline_is_not_silently_ignored(baseline):
    with pytest.raises((ValueError, TypeError)):
        compare_reports({"schema_version": 1, "results": []}, baseline)


def test_invalid_evidence_is_categorized_before_serialization():
    report = run_suite(
        FunctionalSuite("daily", ("project.read",)),
        probes={"project.read": lambda: ProbeResult("pass", "ok", evidence={"latency": float("nan")})},
        prerequisites={"project_read_url": "url"},
    )
    assert selected(report)["project.read"].status == "fail"
    assert json.loads(report.to_json())["passed"] is False


def test_invalid_baseline_rejected_before_any_probe_runs():
    calls = []
    with pytest.raises(ValueError):
        run_suite(
            FunctionalSuite("daily", ("project.read",)),
            probes={"project.read": lambda: calls.append(True)},
            prerequisites={"project_read_url": "url"},
            baseline={},
        )
    assert not calls

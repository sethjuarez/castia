"""Observe command registrar; the root CLI only needs ``register(subparsers)``.

The functional run consumes a JSON LiveConfig mapping with an optional nested
``limits`` mapping. Hosted and model URLs include explicit discovered protocol
paths/API versions; project/model configuration never falls back to azd or
environment defaults. ``suite run`` requires ``--live`` unless ``--dry-run`` is
selected, and optimizer submission additionally requires its own flag.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from castia.observe.live import Limits, LiveConfig, live_probes
from castia.observe.records import ExecutionRecord, summarize
from castia.observe.suite import (
    FEATURE_CATALOG,
    FunctionalSuite,
    compare_reports,
    run_suite,
)
from castia.observe.telemetry import (
    AppInsightsClient,
    ObserveError,
    TraceQuery,
    positive,
    verify_probe,
)


def _emit(data: Any, out: str | None) -> None:
    text = json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def _json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _jsonl(path: str) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    skipped_incomplete = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not text.endswith("\n"):
                skipped_incomplete += 1
                continue
            raise
        if not isinstance(value, dict):
            raise TypeError("local trace rows must be JSON objects")
        rows.append(value)
    return rows, skipped_incomplete


def _safe(command: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    @functools.wraps(command)
    def invoke(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except ObserveError as exc:
            _emit({"status": "fail", "category": exc.category, "diagnostic": str(exc)}, None)
            return 2
        except (OSError, ValueError, TypeError, KeyError):
            _emit({
                "status": "blocked", "category": "configuration",
                "diagnostic": "Observe configuration or input could not be read or validated.",
            }, None)
            return 2
        except ImportError:
            _emit({
                "status": "blocked", "category": "dependency",
                "diagnostic": "An optional parser/credential dependency is unavailable.",
            }, None)
            return 2
    return invoke


def _query(args: argparse.Namespace) -> TraceQuery:
    positive(args.request_timeout, "request_timeout")
    return TraceQuery(
        agent_name=args.agent,
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        limit=args.limit,
        agent_version=args.agent_version,
        probe_tag=args.probe_tag,
        include_content=args.include_content,
        anchor=args.anchor,
        trace_id=args.trace_id,
    )


@_safe
def _features(args: argparse.Namespace) -> int:
    _emit({"schema_version": 1, "features": [asdict(feature) for feature in FEATURE_CATALOG]}, args.out)
    return 0


@_safe
def _traces(args: argparse.Namespace) -> int:
    query = _query(args)
    if args.dry_run:
        _emit({"query": query.to_kql(), "live": False, "content_included": query.include_content}, args.out)
        return 0
    with AppInsightsClient(args.app_id, timeout_seconds=args.request_timeout) as client:
        rows = client.query(query)
    _emit({
        "schema_version": 1,
        "records": [row.to_dict() for row in rows],
        "summary": summarize(rows),
    }, args.out)
    return 0


@_safe
def _summarize(args: argparse.Namespace) -> int:
    data = _json(args.records)
    rows = data.get("records") if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        raise TypeError("records must be a list")
    _emit(summarize(ExecutionRecord(**row) for row in rows), args.out)
    return 0


@_safe
def _local_traces(args: argparse.Namespace) -> int:
    rows, skipped_incomplete = _jsonl(args.path)
    if args.status:
        rows = [row for row in rows if row.get("status") == args.status]
    if args.kind:
        rows = [row for row in rows if row.get("kind") == args.kind]
    if args.name_contains:
        rows = [row for row in rows if args.name_contains in str(row.get("name", ""))]
    if args.limit < 1:
        raise ValueError("limit must be positive")
    selected = rows[-args.limit:]
    status_counts = {
        status: sum(str(row.get("status", "unknown")) == status for row in selected)
        for status in sorted({str(row.get("status", "unknown")) for row in selected})
    }
    kind_counts = {
        kind: sum(str(row.get("kind", "unknown")) == kind for row in selected)
        for kind in sorted({str(row.get("kind", "unknown")) for row in selected})
    }
    _emit(
        {
            "schema_version": 1,
            "source": args.path,
            "records": selected,
            "summary": {
                "record_count": len(selected),
                "total_matching_records": len(rows),
                "skipped_incomplete_records": skipped_incomplete,
                "status_counts": status_counts,
                "kind_counts": kind_counts,
            },
        },
        args.out,
    )
    return 0


@_safe
def _verify(args: argparse.Namespace) -> int:
    query = _query(args)
    with AppInsightsClient(args.app_id, timeout_seconds=args.request_timeout) as client:
        result = verify_probe(
            lambda: client.query(query), probe_tag=args.probe_tag,
            timeout_seconds=args.timeout, poll_seconds=args.poll_seconds,
            max_attempts=args.max_attempts,
        )
    _emit(result.to_dict(), args.out)
    return 0 if result.status == "pass" else 1


@_safe
def _suite_init(args: argparse.Namespace) -> int:
    suite = FunctionalSuite(args.name, tuple(feature.id for feature in FEATURE_CATALOG))
    _emit(suite.to_dict(), args.out)
    return 0


@_safe
def _suite_check(args: argparse.Namespace) -> int:
    suite = FunctionalSuite.load(args.suite)
    _emit({
        "schema_version": 1, "status": "pass", "check": "suite_schema",
        "suite": suite.to_dict(), "live_coverage_established": False,
    }, args.out)
    return 0


def _config(args: argparse.Namespace) -> LiveConfig:
    data = _json(args.config) if args.config else {}
    if not isinstance(data, dict):
        raise TypeError("live config must be an object")
    values = dict(data)
    limits = values.pop("limits", {})
    if not isinstance(limits, Mapping):
        raise TypeError("limits must be an object")
    # A saved JSON file never silently authorizes a new billable optimizer job.
    values["allow_optimizer_submit"] = args.allow_optimizer_submit
    return LiveConfig(**values, limits=Limits(**limits))


@_safe
def _suite_run(args: argparse.Namespace) -> int:
    suite = FunctionalSuite.load(args.suite)
    config = _config(args)
    baseline = _json(args.baseline) if args.baseline else None
    with live_probes(config) as probes:
        if args.dry_run:
            requirements = {
                feature.id: {
                    "probe_registered": feature.id in probes,
                    "missing_prerequisites": [
                        key for key in feature.prerequisites if not config.prerequisites().get(key)
                    ],
                }
                for feature in FEATURE_CATALOG if feature.id in suite.features
            }
            _emit({
                "schema_version": 1, "live": False, "suite": suite.to_dict(),
                "coverage_plan": requirements, "limits": asdict(config.limits),
                "optimizer_submission_enabled": config.allow_optimizer_submit,
                "cost": None, "cost_status": "unknown", "coverage_established": False,
            }, args.out)
            return 0
        if not args.live:
            raise ObserveError("prerequisite", "Use --live to run configured probes, or --dry-run for an offline plan.")
        report = run_suite(
            suite, probes=probes, prerequisites=config.prerequisites(), baseline=baseline,
        )
    _emit(report.to_dict(), args.out)
    regressions = report.comparison and (
        report.comparison["regressions"] or report.comparison["lost_coverage"]
    )
    return 0 if report.passed and not regressions else 1


@_safe
def _compare(args: argparse.Namespace) -> int:
    current, baseline = _json(args.candidate), _json(args.baseline)
    comparison = compare_reports(current, baseline)
    selected = [row for row in current["results"] if row["selected"]]
    counts = {
        status: sum(row["status"] == status for row in selected)
        for status in ("pass", "fail", "blocked", "uncovered")
    }
    if counts["fail"] or comparison["regressions"] or comparison["lost_coverage"]:
        status = "fail"
    elif counts["blocked"]:
        status = "blocked"
    elif counts["uncovered"] or not selected:
        status = "uncovered"
    else:
        status = "pass"
    _emit({
        "schema_version": 1, "status": status, "passed": status == "pass",
        "counts": counts, "comparison": comparison,
    }, args.out)
    return 0 if status == "pass" else 1


def _output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", help="write canonical JSON to this local file (default: stdout)")


def _trace_options(parser: argparse.ArgumentParser, *, verify: bool = False) -> None:
    parser.add_argument("--app-id", default=os.environ.get("APPLICATIONINSIGHTS_APP_ID"),
                        help="Application Insights application ID (not its instrumentation key)")
    parser.add_argument("--agent", default=os.environ.get("FOUNDRY_AGENT_NAME"),
                        help="exact selected agent name")
    parser.add_argument("--agent-version")
    parser.add_argument("--start", required=True, help="inclusive ISO timestamp including UTC offset")
    parser.add_argument("--end", required=True, help="exclusive ISO timestamp including UTC offset")
    parser.add_argument("--limit", type=int, default=100, help="maximum trace rows, at most 10000")
    parser.add_argument("--probe-tag", required=verify, help="exact castia.probe_tag attribute")
    parser.add_argument("--anchor", choices=["requests", "invoke_agent"], default="requests",
                        help="hosted request roots or explicit local invoke_agent delivery spans")
    parser.add_argument("--trace-id", help="add an exact operation/trace ID filter")
    parser.add_argument("--include-content", action="store_true",
                        help="explicitly include recorded prompt/response content (default: excluded)")
    parser.add_argument("--request-timeout", type=float, default=30, help="individual HTTP timeout in seconds")
    _output(parser)


def _run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite", required=True)
    parser.add_argument("--config", help="local JSON LiveConfig with explicit discovered endpoint URLs")
    parser.add_argument("--baseline", help="previous canonical report JSON for drift comparison")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="authorize configured live calls; model calls are billable")
    mode.add_argument("--dry-run", action="store_true", help="show coverage/prerequisites without live calls")
    parser.add_argument("--allow-optimizer-submit", action="store_true",
                        help="additionally authorize bounded billable optimizer submission; never applies/deploys")
    _output(parser)
    parser.set_defaults(func=_suite_run)


def register(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``observe`` on root subparsers; handlers return process exit codes.

    Exit 0: selected assertions passed (or an explicitly offline schema/plan
    command completed). Exit 1: functional coverage or ingestion is incomplete.
    Exit 2: configuration, credentials, or transport prevented the command.
    """
    parser = subparsers.add_parser("observe", help="local telemetry and bounded functional drift evidence")
    commands = parser.add_subparsers(dest="observe_command", required=True)
    features = commands.add_parser("catalog", aliases=["features"],
                                   help="list stable feature IDs and explicit prerequisites")
    _output(features)
    features.set_defaults(func=_features)

    traces = commands.add_parser("traces", help="query scoped Application Insights traces")
    _trace_options(traces)
    traces.add_argument("--dry-run", action="store_true", help="print scoped KQL without network calls")
    traces.set_defaults(func=_traces)

    summary = commands.add_parser("summary", aliases=["summarize"],
                                  help="summarize a local normalized execution-record JSON file")
    summary.add_argument("--records", required=True)
    _output(summary)
    summary.set_defaults(func=_summarize)

    local_traces = commands.add_parser(
        "local-traces",
        help="read local Castia JSONL trace sink records without network calls",
    )
    local_traces.add_argument("--path", default=".castia/traces/live.jsonl")
    local_traces.add_argument("--limit", type=int, default=100,
                              help="return the newest matching records")
    local_traces.add_argument("--status", choices=["ok", "error", "cancelled"])
    local_traces.add_argument("--kind", help="filter by trace record kind")
    local_traces.add_argument("--name-contains", help="filter by trace record name substring")
    _output(local_traces)
    local_traces.set_defaults(func=_local_traces)

    verify = commands.add_parser("verify", help="wait for an already submitted tagged probe in telemetry")
    _trace_options(verify, verify=True)
    verify.add_argument("--timeout", type=float, default=60)
    verify.add_argument("--poll-seconds", type=float, default=5)
    verify.add_argument("--max-attempts", type=int, default=12)
    verify.set_defaults(func=_verify)

    drift = commands.add_parser("drift", help="run a configured feature suite and compare daily drift")
    _run_options(drift)
    compare = commands.add_parser("compare", help="compare two local feature reports without live calls")
    compare.add_argument("--baseline", required=True, help="baseline canonical report JSON")
    compare.add_argument("--candidate", "--current", dest="candidate", required=True,
                         help="candidate/current canonical report JSON")
    _output(compare)
    compare.set_defaults(func=_compare)

    suite = commands.add_parser("suite", help="configure and run a local daily feature drift suite")
    suite_commands = suite.add_subparsers(dest="observe_suite_command", required=True)
    initialize = suite_commands.add_parser("init", help="print a complete feature inventory suite")
    initialize.add_argument("--name", default="daily")
    _output(initialize)
    initialize.set_defaults(func=_suite_init)
    check = suite_commands.add_parser("check", help="validate suite schema offline (not feature coverage)")
    check.add_argument("--suite", required=True)
    _output(check)
    check.set_defaults(func=_suite_check)
    run = suite_commands.add_parser("run", help="run explicitly configured probes, with strict selected coverage")
    _run_options(run)
    return parser


register_commands = register

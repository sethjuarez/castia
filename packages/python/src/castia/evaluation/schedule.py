"""Scheduled evaluation rules and windowed trace evaluation runs.

Pure builders produce the azure-ai-projects 2.11 wire payloads (pinned in
``spec/evaluation/schedule.tsp``); the CLI handlers are thin live wrappers.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from typing import Any

from castia.lifecycle.output import (
    add_json_argument,
    command_envelope,
    emit_envelope,
    error_envelope,
    wants_json,
)

TRACE_SOURCE_TYPE = "azure_ai_trace_data_source"
FILTER_STRATEGIES = ("random_sampling", "smart_filtering")
SOURCE_KINDS = ("traces", "dataset")
DEFAULT_QUERY_TEMPLATE = "{{item.query}}"

_WINDOW_UNITS = {"m": 60, "h": 3600, "d": 86400}
_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)


def parse_window(text: str) -> int:
    """Parse a trace window such as ``30m``, ``24h`` or ``7d`` into seconds."""

    match = _WINDOW_RE.match(str(text))
    if not match:
        raise ValueError("window must look like 30m, 24h, or 7d")
    seconds = int(match.group(1)) * _WINDOW_UNITS[match.group(2).lower()]
    if seconds <= 0:
        raise ValueError("window must be greater than zero")
    return seconds


def _positive(name: str, value: int | None) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def trace_window_source(
    *,
    agent_name: str,
    window_seconds: int,
    end_time: int,
    agent_version: str | None = None,
    max_traces: int | None = None,
    filter_strategy: str | None = None,
) -> dict[str, Any]:
    """Build a ``TraceSourceEvalRunDataSource`` over an agent's recent traces."""

    if not agent_name:
        raise ValueError("agent_name is required")
    _positive("window_seconds", window_seconds)
    _positive("max_traces", max_traces)
    if filter_strategy is not None and filter_strategy not in FILTER_STRATEGIES:
        raise ValueError("filter_strategy must be one of " + ", ".join(FILTER_STRATEGIES))
    trace_source: dict[str, Any] = {
        "type": "agent_filter",
        "agent_name": agent_name,
        "start_time": int(end_time) - int(window_seconds),
        "end_time": int(end_time),
    }
    if agent_version:
        trace_source["agent_version"] = agent_version
    if max_traces is not None:
        trace_source["max_traces"] = max_traces
    if filter_strategy is not None:
        trace_source["filter_strategy"] = filter_strategy
    return {"type": TRACE_SOURCE_TYPE, "trace_source": trace_source}


def schedule_trigger(
    *,
    cron: str | None = None,
    every_hours: int | None = None,
    daily_hours: list[int] | None = None,
    time_zone: str | None = None,
) -> dict[str, Any]:
    """Build the timer trigger for a ``timerTrigger`` evaluation rule."""

    chosen = [x for x in (cron, every_hours, daily_hours) if x not in (None, [])]
    if len(chosen) != 1:
        raise ValueError("set exactly one of cron, every_hours, or daily_hours")
    if cron is not None:
        if len(cron.split()) != 5:
            raise ValueError("cron must have five fields: minute hour day month weekday")
        trigger: dict[str, Any] = {"type": "Cron", "expression": " ".join(cron.split())}
    elif every_hours is not None:
        _positive("every_hours", every_hours)
        trigger = {"type": "Recurrence", "interval": every_hours,
                   "schedule": {"type": "Hourly"}}
    else:
        hours = sorted(set(daily_hours or []))
        if any(h < 0 or h > 23 for h in hours):
            raise ValueError("daily_hours must be between 0 and 23")
        trigger = {"type": "Recurrence", "interval": 1,
                   "schedule": {"type": "Daily", "hours": hours}}
    if time_zone:
        trigger["timeZone"] = time_zone
    return trigger


def evaluation_rule(
    *,
    eval_id: str,
    agent_name: str,
    source_kind: str,
    trigger: dict[str, Any],
    dataset_file_id: str | None = None,
    agent_version: str | None = None,
    max_traces: int | None = None,
    query_template: str | None = None,
    display_name: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Build a scheduled (``timerTrigger``) dataset or trace evaluation rule."""

    if not eval_id:
        raise ValueError("eval_id is required")
    if not agent_name:
        raise ValueError("agent_name is required")
    _positive("max_traces", max_traces)
    if source_kind == "traces":
        if dataset_file_id:
            raise ValueError("dataset_file_id is only valid for dataset rules")
        source: dict[str, Any] = {"type": "azure_ai_traces", "agent_name": agent_name}
        if max_traces is not None:
            source["max_traces"] = max_traces
        action = {"type": "traceEvaluation", "evalId": eval_id, "source": source}
    elif source_kind == "dataset":
        if not dataset_file_id:
            raise ValueError("dataset rules require dataset_file_id")
        if max_traces is not None:
            raise ValueError("max_traces is only valid for trace rules")
        target: dict[str, Any] = {"type": "azure_ai_agent", "name": agent_name}
        if agent_version:
            target["version"] = agent_version
        action = {
            "type": "datasetEvaluation",
            "evalId": eval_id,
            "source": {
                "type": "azure_ai_target_completions",
                "source": {"type": "file_id", "id": dataset_file_id},
                "input_messages": {
                    "type": "template",
                    "template": [{
                        "type": "message",
                        "role": "user",
                        "content": {"type": "input_text",
                                    "text": query_template or DEFAULT_QUERY_TEMPLATE},
                    }],
                },
                "target": target,
            },
        }
    else:
        raise ValueError("source_kind must be one of " + ", ".join(SOURCE_KINDS))
    rule: dict[str, Any] = {
        "action": action,
        "eventType": "timerTrigger",
        "schedule": trigger,
        "enabled": bool(enabled),
    }
    if display_name:
        rule["displayName"] = display_name
    return rule


# --- CLI --------------------------------------------------------------------

_LIVE_READ = {"network": True}
_LIVE_WRITE = {"network": True, "mutating": True}


def register_commands(evsub: argparse._SubParsersAction) -> None:
    sched = evsub.add_parser(
        "schedule",
        help="scheduled dataset/trace evaluation rules (timer trigger)",
    )
    ssub = sched.add_subparsers(dest="schedule_command", required=True)

    create = ssub.add_parser("create", help="create or replace a scheduled evaluation rule")
    create.add_argument("rule_id", help="evaluation rule id")
    create.add_argument("--eval-id", dest="eval_id", required=True,
                        help="existing eval (group) id that scores each run")
    create.add_argument("--agent", required=True, help="agent name")
    create.add_argument("--agent-version", dest="agent_version", default=None)
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--traces", action="store_true",
                        help="score the agent's traces since the previous occurrence")
    source.add_argument("--dataset-file-id", dest="dataset_file_id", default=None,
                        help="score the agent against this uploaded JSONL file")
    when = create.add_mutually_exclusive_group(required=True)
    when.add_argument("--cron", default=None, help='five-field cron, e.g. "0 9 * * *"')
    when.add_argument("--every-hours", dest="every_hours", type=int, default=None)
    when.add_argument("--daily-at", dest="daily_at", default=None,
                      help="comma-separated UTC hours, e.g. 9 or 9,21")
    create.add_argument("--time-zone", dest="time_zone", default=None)
    create.add_argument("--max-traces", dest="max_traces", type=int, default=None)
    create.add_argument("--query-template", dest="query_template", default=None,
                        help=f"dataset prompt template (default: {DEFAULT_QUERY_TEMPLATE})")
    create.add_argument("--display-name", dest="display_name", default=None)
    create.add_argument("--disabled", action="store_true", help="create the rule disabled")
    create.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the rule payload and create nothing")
    create.set_defaults(func=_cmd_schedule_create)

    lst = ssub.add_parser("list", help="list evaluation rules")
    lst.add_argument("--agent", default=None, help="only rules for this agent")
    lst.set_defaults(func=_cmd_schedule_list)

    get = ssub.add_parser("get", help="show one evaluation rule")
    get.add_argument("rule_id")
    get.set_defaults(func=_cmd_schedule_get)

    delete = ssub.add_parser("delete", help="delete an evaluation rule")
    delete.add_argument("rule_id")
    delete.set_defaults(func=_cmd_schedule_delete)

    traces = evsub.add_parser(
        "traces",
        help="score an agent's recent traces once (TraceSourceEvalRunDataSource)",
    )
    traces.add_argument("--eval-id", dest="eval_id", required=True,
                        help="existing eval (group) id that scores the run")
    traces.add_argument("--agent", required=True, help="agent name")
    traces.add_argument("--agent-version", dest="agent_version", default=None)
    traces.add_argument("--window", required=True, help="lookback window: 30m, 24h, 7d")
    traces.add_argument("--max-traces", dest="max_traces", type=int, default=None)
    traces.add_argument("--filter-strategy", dest="filter_strategy", default=None,
                        choices=FILTER_STRATEGIES)
    traces.add_argument("--name", default=None, help="eval run name")
    traces.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the run payload and submit nothing")
    traces.set_defaults(func=_cmd_eval_traces)

    for parser in (create, lst, get, delete, traces):
        parser.add_argument("--project-endpoint", dest="project_endpoint", default=None)
        add_json_argument(parser)


def _endpoint(args: argparse.Namespace) -> str:
    endpoint = args.project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is not set (or pass --project-endpoint)")
    return endpoint


def _project_client(args: argparse.Namespace):
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    return AIProjectClient(endpoint=_endpoint(args), credential=DefaultAzureCredential())


def _as_dict(value: Any) -> Any:
    return value.as_dict() if hasattr(value, "as_dict") else value


def _emit(args, command, *, status, result, effects, next_actions=None, text=None) -> None:
    if wants_json(args):
        emit_envelope(command_envelope(command, status=status, result=result,
                                       next_actions=next_actions or [], effects=effects))
    else:
        import json

        for line in text or []:
            print(line)
        print(json.dumps(result, indent=2, default=str))


def _fail(args, command: str, exc: BaseException | str, effects: dict | None = None) -> int:
    if wants_json(args):
        emit_envelope(error_envelope(command, exc, effects=effects))
    else:
        import sys

        print(f"error: {exc}", file=sys.stderr)
    return 2


def _live_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [RuntimeError, ValueError, OSError]
    try:
        from azure.core.exceptions import AzureError

        errors.append(AzureError)
    except ModuleNotFoundError:  # pragma: no cover - deploy extra not installed
        pass
    return tuple(errors)


def _cmd_schedule_create(args: argparse.Namespace) -> int:
    command = "eval schedule create"
    try:
        daily = ([int(h) for h in args.daily_at.split(",") if h.strip()]
                 if args.daily_at else None)
        trigger = schedule_trigger(cron=args.cron, every_hours=args.every_hours,
                                   daily_hours=daily, time_zone=args.time_zone)
        rule = evaluation_rule(
            eval_id=args.eval_id, agent_name=args.agent,
            source_kind="traces" if args.traces else "dataset", trigger=trigger,
            dataset_file_id=args.dataset_file_id, agent_version=args.agent_version,
            max_traces=args.max_traces, query_template=args.query_template,
            display_name=args.display_name, enabled=not args.disabled,
        )
    except ValueError as exc:
        return _fail(args, command, exc)

    if args.dry_run:
        _emit(args, command, status="dry_run", result={"rule_id": args.rule_id, "rule": rule},
              effects={}, next_actions=["rerun without --dry-run to create the rule"],
              text=[f"rule          : {args.rule_id}", "result        : dry-run (nothing created)"])
        return 0
    try:
        with _project_client(args) as client:
            created = _as_dict(client.evaluation_rules.create_or_update(args.rule_id, rule))
    except _live_errors() as exc:
        return _fail(args, command, exc, _LIVE_WRITE)
    # Each occurrence starts an eval run, so the rule itself creates billable work.
    _emit(args, command, status="created", result={"rule_id": args.rule_id, "rule": created},
          effects={"network": True, "billable": True, "mutating": True},
          next_actions=[f"python -m castia eval schedule get {args.rule_id}"],
          text=[f"rule          : {args.rule_id}",
                "result        : created (each occurrence runs a billable evaluation)"])
    return 0


def _cmd_schedule_list(args: argparse.Namespace) -> int:
    command = "eval schedule list"
    try:
        with _project_client(args) as client:
            rules = [_as_dict(r) for r in client.evaluation_rules.list(agent_name=args.agent)]
    except _live_errors() as exc:
        return _fail(args, command, exc, _LIVE_READ)
    _emit(args, command, status="ok", result={"rules": rules}, effects=_LIVE_READ)
    return 0


def _cmd_schedule_get(args: argparse.Namespace) -> int:
    command = "eval schedule get"
    try:
        with _project_client(args) as client:
            rule = _as_dict(client.evaluation_rules.get(args.rule_id))
    except _live_errors() as exc:
        return _fail(args, command, exc, _LIVE_READ)
    status = "disabled" if rule.get("enabled") is False else "enabled"
    _emit(args, command, status=status, result={"rule_id": args.rule_id, "rule": rule},
          effects=_LIVE_READ)
    return 0


def _cmd_schedule_delete(args: argparse.Namespace) -> int:
    command = "eval schedule delete"
    try:
        with _project_client(args) as client:
            client.evaluation_rules.delete(args.rule_id)
    except _live_errors() as exc:
        return _fail(args, command, exc, _LIVE_WRITE)
    _emit(args, command, status="deleted", result={"rule_id": args.rule_id},
          effects=_LIVE_WRITE)
    return 0


def _cmd_eval_traces(args: argparse.Namespace) -> int:
    command = "eval traces"
    try:
        window = parse_window(args.window)
        data_source = trace_window_source(
            agent_name=args.agent, window_seconds=window, end_time=int(time.time()),
            agent_version=args.agent_version, max_traces=args.max_traces,
            filter_strategy=args.filter_strategy,
        )
    except ValueError as exc:
        return _fail(args, command, exc)
    name = args.name or f"{args.agent} traces {args.window}"
    payload = {"eval_id": args.eval_id, "name": name, "data_source": data_source}
    if args.dry_run:
        _emit(args, command, status="dry_run", result=payload, effects={},
              next_actions=["rerun without --dry-run to submit a billable eval run"],
              text=["result        : dry-run (nothing submitted)"])
        return 0
    live = {"network": True, "billable": True, "mutating": True}
    try:
        with _project_client(args) as project:
            run = project.get_openai_client().evals.runs.create(
                eval_id=args.eval_id, name=name, data_source=data_source,
            )
    except Exception as exc:  # noqa: BLE001 - azure and openai errors become envelopes
        return _fail(args, command, exc, live)
    result = run.model_dump(mode="json") if hasattr(run, "model_dump") else _as_dict(run)
    _emit(args, command, status=str(result.get("status") or "submitted"),
          result={"eval_id": args.eval_id, "run": result}, effects=live,
          text=[f"run           : {result.get('id')}",
                "result        : submitted (billable eval run)"])
    return 0

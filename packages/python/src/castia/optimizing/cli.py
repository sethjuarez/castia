"""Command registration and handlers for optimizing."""

from __future__ import annotations

import argparse
import sys

from castia.delivery.manifest import DEFAULT_APP, load_app
from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.optimizing.baseline import (
    DEFAULT_CONFIG_DIR,
    OptimizePlan,
    generate_optimizer_config,
)


def register_commands(sub: argparse._SubParsersAction) -> None:
    optimize = sub.add_parser(
        "optimize",
        help="sync the .agent_configs baseline (tools.json) from the Agent",
    )
    optimize.add_argument(
        "--app",
        default=DEFAULT_APP,
        help=f"entrypoint as module:attr (default: {DEFAULT_APP})",
    )
    optimize.add_argument(
        "--config-dir",
        default=DEFAULT_CONFIG_DIR,
        help=f"path to the .agent_configs directory (default: {DEFAULT_CONFIG_DIR})",
    )
    optimize.add_argument(
        "--candidate",
        default="baseline",
        help="candidate folder under the config dir (default: baseline)",
    )
    optimize.add_argument(
        "--check",
        action="store_true",
        help="report baseline drift and exit non-zero without writing",
    )
    optsub = optimize.add_subparsers(dest="optimize_command")

    opt_run = optsub.add_parser(
        "run",
        help="submit a Castia-native Foundry Agent Optimizer job",
    )
    opt_run.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                         help=f"path to eval config (default: {DEFAULT_EVAL_CONFIG})")
    opt_run.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                         help="Foundry project endpoint (default: FOUNDRY_PROJECT_ENDPOINT)")
    opt_run.add_argument("--agent", default=None, help="agent name (default: eval.yaml)")
    opt_run.add_argument("--agent-version", dest="agent_version", default=None,
                         help="hosted agent version (default: eval.yaml or empty)")
    opt_run.add_argument("--eval-model", dest="eval_model", default=None,
                         help="evaluator model (default: eval.yaml options.eval_model)")
    opt_run.add_argument("--optimize-model", dest="optimize_model", default=None,
                         help="optimizer model (default: eval.yaml options.optimization_model)")
    opt_run.add_argument("--max-candidates", dest="max_candidates", type=int, default=None,
                         help="candidate cap (default: eval.yaml options.max_candidates)")
    opt_run.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                         help=f"path to .agent_configs (default: {DEFAULT_CONFIG_DIR})")
    opt_run.add_argument("--candidate", default="baseline",
                         help="baseline candidate folder (default: baseline)")
    opt_run.add_argument("--dry-run", dest="dry_run", action="store_true",
                         help="print payload and submit nothing")
    opt_run.add_argument("--no-wait", dest="no_wait", action="store_true",
                         help="submit and return without polling once")
    opt_run.add_argument("--poll-interval", dest="poll_interval", type=float, default=15,
                         help="seconds between polls when waiting for completion")
    opt_run.set_defaults(func=_cmd_optimize_run)

    opt_status = optsub.add_parser(
        "status",
        help="inspect a Castia-native optimizer job",
    )
    opt_status.add_argument("job_id", nargs="?", default=None,
                            help="job id (default: latest Castia job)")
    opt_status.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                            help="Foundry project endpoint (default: latest Castia job)")
    opt_status.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                            help=f"path to .agent_configs (default: {DEFAULT_CONFIG_DIR})")
    opt_status.add_argument("--watch", action="store_true",
                            help="poll until the job reaches a terminal status")
    opt_status.add_argument("--poll-interval", dest="poll_interval", type=float, default=15,
                            help="seconds between polls when --watch is set")
    opt_status.set_defaults(func=_cmd_optimize_status)

    opt_cancel = optsub.add_parser(
        "cancel",
        help="cancel a Castia-native optimizer job",
    )
    opt_cancel.add_argument("job_id", nargs="?", default=None,
                            help="job id (default: latest Castia job)")
    opt_cancel.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                            help="Foundry project endpoint (default: latest Castia job)")
    opt_cancel.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                            help=f"path to .agent_configs (default: {DEFAULT_CONFIG_DIR})")
    opt_cancel.set_defaults(func=_cmd_optimize_cancel)

    opt_apply = optsub.add_parser(
        "apply",
        help="materialize a candidate into .agent_configs/<candidate>",
    )
    opt_apply.add_argument("--candidate", default="best",
                           help="candidate id to apply (default: best)")
    opt_apply.add_argument("--job-id", dest="job_id", default=None,
                           help="job id (default: latest Castia job)")
    opt_apply.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                           help="Foundry project endpoint (default: latest Castia job)")
    opt_apply.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                           help=f"path to .agent_configs (default: {DEFAULT_CONFIG_DIR})")
    opt_apply.set_defaults(func=_cmd_optimize_apply)
    optimize.set_defaults(func=_cmd_optimize)


def _format_list(protocols) -> str:
    return ", ".join(protocols) if protocols else "(none)"

def _print_optimize_plan(plan: OptimizePlan, *, check: bool) -> None:
    print(f"baseline      : {plan.baseline_dir}")
    print(f"declared tools: {_format_list(plan.tools)}")
    tools_state = "drift" if plan.tools_changed else "up to date"
    print(f"tools.json    : {plan.tools_path.name}  ({tools_state})")
    meta_state = "needs tool_file" if plan.metadata_changed else "ok"
    print(f"metadata.yaml : {plan.metadata_path.name}  ({meta_state})")
    for note in plan.notes:
        print(f"note          : {note}")

    if not plan.changed:
        print("result        : up to date")
    elif check:
        print("result        : OUT OF DATE (run without --check to write)")
    elif plan.written:
        print("result        : written")

def _cmd_optimize(args: argparse.Namespace) -> int:
    try:
        app = load_app(args.app)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"error: could not load app {args.app!r}: {exc}", file=sys.stderr)
        return 2

    try:
        plan = generate_optimizer_config(
            app, args.config_dir, candidate=args.candidate, check=args.check
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not reconcile baseline: {exc}", file=sys.stderr)
        return 2

    _print_optimize_plan(plan, check=args.check)

    if args.check and plan.changed:
        return 1
    return 0

def _cmd_optimize_run(args: argparse.Namespace) -> int:
    from pathlib import Path as _Path

    from castia.optimizing.jobs import (
        OptimizerClient,
        OptimizerState,
        build_optimizer_request,
        job_id,
        load_eval_config,
        save_optimizer_state,
    )

    try:
        cfg_path = args.config
        config_root = _Path(cfg_path).parent
        config_dir = _resolve_config_dir(args.config_dir, root=config_root)
        cfg = load_eval_config(cfg_path)
        request = build_optimizer_request(
            eval_config=cfg,
            root=str(config_root or "."),
            agent_name=args.agent,
            agent_version=args.agent_version,
            eval_model=args.eval_model,
            optimize_model=args.optimize_model,
            max_candidates=args.max_candidates,
            config_dir=config_dir,
            candidate=args.candidate,
        )
    except (OSError, TypeError, ValueError, KeyError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    import json as _json

    print(f"agent         : {request['agent']['agent_name']}")
    print(f"eval model    : {request['options']['eval_model']}")
    print(f"optimize model: {request['options']['optimization_model']}")
    tools = request["options"]["optimization_config"].get("tools") or []
    print(
        "tools         : "
        f"{_format_list([t.get('function', {}).get('name', '?') for t in tools])}"
    )

    if args.dry_run:
        print("payload       :")
        print(_json.dumps(request, indent=2))
        print("result        : dry-run (nothing submitted)")
        return 0

    endpoint = args.project_endpoint or __import__("os").environ.get(
        "FOUNDRY_PROJECT_ENDPOINT"
    )
    if not endpoint:
        print(
            "error: FOUNDRY_PROJECT_ENDPOINT is not set (or pass --project-endpoint)",
            file=sys.stderr,
        )
        return 2

    try:
        response = OptimizerClient(endpoint).start(request)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    submitted_job_id = job_id(response)
    if not submitted_job_id:
        print("error: optimizer response did not include a job id", file=sys.stderr)
        return 2
    save_optimizer_state(
        OptimizerState(
            job_id=str(submitted_job_id),
            project_endpoint=endpoint,
            agent_name=str(request["agent"]["agent_name"]),
        ),
        config_dir=config_dir,
    )
    print(f"job           : {submitted_job_id}")
    print(f"status        : {response.get('status', 'unknown')}")
    print("result        : submitted (billable optimizer job)")
    if not args.no_wait:
        args.job_id = str(submitted_job_id)
        args.project_endpoint = endpoint
        args.config_dir = str(config_dir)
        args.watch = True
        return _cmd_optimize_status(args)
    return 0

def _cmd_optimize_status(args: argparse.Namespace) -> int:
    from castia.optimizing.jobs import (
        OptimizerClient,
        is_terminal_status,
        load_optimizer_state,
    )

    try:
        config_dir = _resolve_config_dir(args.config_dir)
        state = (
            None
            if args.job_id and args.project_endpoint
            else load_optimizer_state(config_dir=config_dir)
        )
        job_id = args.job_id or state.job_id
        endpoint = args.project_endpoint or state.project_endpoint
        client = OptimizerClient(endpoint)
        while True:
            status = client.status(job_id)
            _print_optimizer_status(status)
            if not args.watch or is_terminal_status(status.get("status")):
                return 0 if str(status.get("status", "")).lower() != "failed" else 1
            __import__("time").sleep(args.poll_interval)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

def _cmd_optimize_cancel(args: argparse.Namespace) -> int:
    from castia.optimizing.jobs import OptimizerClient, load_optimizer_state

    try:
        config_dir = _resolve_config_dir(args.config_dir)
        state = (
            None
            if args.job_id and args.project_endpoint
            else load_optimizer_state(config_dir=config_dir)
        )
        job_id = args.job_id or state.job_id
        endpoint = args.project_endpoint or state.project_endpoint
        status = OptimizerClient(endpoint).cancel(job_id)
        print(f"job           : {job_id}")
        print(f"status        : {status.get('status', 'cancel requested')}")
        print("result        : cancel requested")
        return 0
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

def _cmd_optimize_apply(args: argparse.Namespace) -> int:
    from castia.optimizing.jobs import (
        OptimizerClient,
        apply_candidate_config,
        best_candidate_id,
        load_optimizer_state,
    )

    try:
        config_dir = _resolve_config_dir(args.config_dir)
        state = (
            None
            if args.job_id and args.project_endpoint
            else load_optimizer_state(config_dir=config_dir)
        )
        job_id = args.job_id or state.job_id
        endpoint = args.project_endpoint or state.project_endpoint
        client = OptimizerClient(endpoint)
        candidate = args.candidate
        if candidate == "best":
            candidate = best_candidate_id(client.status(job_id))
            if not candidate:
                raise ValueError("could not resolve best candidate from job status")
        config = client.candidate_config(job_id, candidate)
        plan = apply_candidate_config(candidate, config, config_dir=config_dir)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"candidate     : {candidate}")
    print(f"wrote         : {plan.metadata_path}")
    if plan.instructions_path:
        print(f"instructions  : {plan.instructions_path.name}")
    if plan.tools_path:
        print(f"tools         : {plan.tools_path.name}")
    if plan.skills_dir:
        print(f"skills        : {plan.skills_dir.name}")
    print(
        "next          : set OPTIMIZATION_LOCAL_DIR=.agent_configs and "
        f"OPTIMIZATION_CANDIDATE_ID={candidate}, then deploy with azd"
    )
    return 0

def _print_optimizer_status(status: dict) -> None:
    from castia.optimizing.jobs import job_id as optimizer_job_id

    print(f"job           : {optimizer_job_id(status)}")
    print(f"status        : {status.get('status')}")
    progress = status.get("progress") or {}
    if progress:
        print(f"baseline score: {progress.get('baseline_score', '-')}")
        print(f"best score    : {progress.get('best_score', '-')}")
        print(f"completed     : {progress.get('candidates_completed', '-')}")
    result = status.get("result") or {}
    candidates = result.get("candidates") if isinstance(result, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            print(
                "candidate     : "
                f"{candidate.get('name')} "
                f"{candidate.get('candidate_id') or candidate.get('candidateId')} "
                f"score={candidate.get('avg_score') or candidate.get('score')}"
            )

def _resolve_config_dir(config_dir: str, *, root=None):
    from pathlib import Path as _Path

    path = _Path(config_dir)
    if path.is_absolute():
        return path
    base = _Path(".") if root is None else _Path(root)
    return base / path

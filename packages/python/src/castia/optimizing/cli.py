"""Command registration and handlers for optimizing."""

from __future__ import annotations

import argparse
import sys

from castia.delivery.manifest import DEFAULT_APP, load_app
from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.lifecycle.output import (
    add_json_argument,
    command_envelope,
    diagnostic,
    emit_envelope,
    error_envelope,
    wants_json,
)
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
    opt_run.add_argument("--from-version", dest="from_version", default=None,
                         metavar="VERSION",
                         help="seed instructions/model/tools from a deployed agent version "
                              "(reads the project; implies --agent-version)")
    opt_run.add_argument("--evaluator-definition", dest="evaluator_definitions",
                         action="append", default=[], metavar="[NAME=]PATH",
                         help="local evaluator definition (JSON/YAML) to preflight "
                              "for item.* references and level; repeatable")
    opt_run.add_argument("--passthrough-field", dest="passthrough_fields",
                         action="append", default=None, metavar="FIELD",
                         help="row field the optimizer passes to evaluators "
                              "(default: by evaluator type, query for rubric, query and ground_truth for prompt); repeatable")
    opt_run.add_argument("--rows", type=int, default=None,
                         help="row count for the estimate when the dataset is registered")
    opt_run.add_argument("--row-seconds", dest="row_seconds", type=float, default=None,
                         help="measured seconds per row per candidate (default: 30)")
    opt_run.add_argument("--hourly-usd", dest="hourly_usd", type=float, default=None,
                         help="optional hourly rate for the cost estimate")
    opt_run.add_argument("--approve-run", dest="approve_run", action="store_true",
                         help="confirm a run larger than the pilot limits")
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

    from castia.optimizing.verify_cli import register_verify_commands

    register_verify_commands(optsub)
    optimize.set_defaults(func=_cmd_optimize)

    for parser in (optimize, opt_run, opt_status, opt_cancel, opt_apply):
        add_json_argument(parser)


def _fail(args: argparse.Namespace, command: str, exc: BaseException | str,
          *, effects: dict | None = None) -> int:
    if wants_json(args):
        emit_envelope(error_envelope(command, exc, effects=effects))
    else:
        print(f"error: {exc}", file=sys.stderr)
    return 2


def _status_name(status: dict) -> str:
    return str(status.get("status") or "unknown").lower()


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
        return _fail(args, "optimize", f"could not load app {args.app!r}: {exc}")

    try:
        plan = generate_optimizer_config(
            app, args.config_dir, candidate=args.candidate, check=args.check
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(args, "optimize", f"could not reconcile baseline: {exc}")

    drift = args.check and plan.changed
    if wants_json(args):
        if not plan.changed:
            status = "up_to_date"
        elif drift:
            status = "out_of_date"
        else:
            status = "written"
        emit_envelope(command_envelope(
            "optimize",
            status=status,
            result={
                "baseline_dir": str(plan.baseline_dir),
                "tools": plan.tools,
                "tools_path": str(plan.tools_path),
                "metadata_path": str(plan.metadata_path),
                "tools_changed": plan.tools_changed,
                "metadata_changed": plan.metadata_changed,
                "written": plan.written,
            },
            diagnostics=[diagnostic("info", note) for note in plan.notes]
            + ([diagnostic("error", "baseline drift", "baseline_drift")] if drift else []),
            next_actions=["python -m castia optimize"] if drift else [],
            effects={"mutating": plan.written},
        ))
    else:
        _print_optimize_plan(plan, check=args.check)

    if drift:
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

    as_json = wants_json(args)
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
        return _fail(args, "optimize run", exc)

    import json as _json

    if args.from_version:
        from castia.optimizing import verify_cli
        from castia.optimizing.verify import seed_from_version

        seed_endpoint = args.project_endpoint or __import__("os").environ.get(
            "FOUNDRY_PROJECT_ENDPOINT"
        )
        if not seed_endpoint:
            return _fail(
                args, "optimize run",
                "--from-version reads the project: set FOUNDRY_PROJECT_ENDPOINT "
                "(or pass --project-endpoint)",
            )
        try:
            details = verify_cli.fetch_agent_version(
                seed_endpoint, request["agent"]["agent_name"], args.from_version
            )
            request, seeded = seed_from_version(
                request, details, agent_version=args.from_version
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
            return _fail(args, "optimize run", f"--from-version {args.from_version}: {exc}")
        print(f"seeded from   : version {args.from_version} ({', '.join(seeded)})",
              file=sys.stderr if as_json else sys.stdout)

    tools = request["options"]["optimization_config"].get("tools") or []
    tool_names = [t.get("function", {}).get("name", "?") for t in tools]
    if not as_json:
        print(f"agent         : {request['agent']['agent_name']}")
        print(f"eval model    : {request['options']['eval_model']}")
        print(f"optimize model: {request['options']['optimization_model']}")
        print(f"tools         : {_format_list(tool_names)}")

    from castia.optimizing.preflight import (
        DEFAULT_ROW_SECONDS,
        preflight_request,
    )

    try:
        definitions = _load_evaluator_definitions(args.evaluator_definitions)
    except (OSError, TypeError, ValueError, ModuleNotFoundError) as exc:
        return _fail(args, "optimize run", f"--evaluator-definition: {exc}")
    check = preflight_request(
        request,
        evaluators=cfg.get("evaluators") or [],
        definitions=definitions,
        passthrough=args.passthrough_fields,
        rows=args.rows,
        row_seconds=args.row_seconds or DEFAULT_ROW_SECONDS,
        hourly_usd=args.hourly_usd,
    )
    estimate = check.estimate
    if as_json and check.problems:
        emit_envelope(command_envelope(
            "optimize run", status="blocked",
            result={"tools": tool_names, "estimate": estimate.line(), "submitted": False},
            diagnostics=[diagnostic("warning", w, "preflight") for w in check.warnings]
            + [diagnostic("error", p, "preflight") for p in check.problems],
            next_actions=["fix the preflight problems, then rerun"],
        ))
        return 1
    print(f"estimate      : {estimate.line()}", file=sys.stderr if as_json else sys.stdout)
    for warning in check.warnings:
        print(f"warning       : {warning}", file=sys.stderr)
    for problem in check.problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if check.problems:
        print("result        : BLOCKED BY PREFLIGHT (nothing submitted)", file=sys.stderr)
        return 1

    if args.dry_run:
        if as_json:
            emit_envelope(command_envelope(
                "optimize run", status="dry_run",
                result={"tools": tool_names, "request": request},
                next_actions=["rerun without --dry-run to submit a billable optimizer job"],
            ))
            return 0
        print("payload       :")
        print(_json.dumps(request, indent=2))
        print("result        : dry-run (nothing submitted)")
        return 0

    if estimate.needs_approval and not args.approve_run:
        if as_json:
            emit_envelope(error_envelope(
                "optimize run",
                "needs approval (" + "; ".join(estimate.reasons) + "); run a pilot "
                "(<= 20 rows, <= 10 candidates) or pass --approve-run",
                code="approval_required",
            ))
            return 1
        print(
            "result        : NEEDS APPROVAL ("
            + "; ".join(estimate.reasons)
            + "). Run a pilot (<= 20 rows, <= 10 candidates) or pass --approve-run",
            file=sys.stderr,
        )
        return 1

    endpoint = args.project_endpoint or __import__("os").environ.get(
        "FOUNDRY_PROJECT_ENDPOINT"
    )
    if not endpoint:
        return _fail(
            args, "optimize run",
            "FOUNDRY_PROJECT_ENDPOINT is not set (or pass --project-endpoint)",
        )

    live = {"network": True, "billable": True, "mutating": True}
    try:
        response = OptimizerClient(endpoint).start(request)
    except RuntimeError as exc:
        return _fail(args, "optimize run", exc, effects=live)

    submitted_job_id = job_id(response)
    if not submitted_job_id:
        return _fail(args, "optimize run", "optimizer response did not include a job id",
                     effects=live)
    save_optimizer_state(
        OptimizerState(
            job_id=str(submitted_job_id),
            project_endpoint=endpoint,
            agent_name=str(request["agent"]["agent_name"]),
        ),
        config_dir=config_dir,
    )
    if not as_json:
        print(f"job           : {submitted_job_id}")
        print(f"status        : {response.get('status', 'unknown')}")
        print("result        : submitted (billable optimizer job)")
    if not args.no_wait:
        args.job_id = str(submitted_job_id)
        args.project_endpoint = endpoint
        args.config_dir = str(config_dir)
        args.watch = True
        return _optimizer_status(args, command="optimize run", effects=live)
    if as_json:
        emit_envelope(command_envelope(
            "optimize run", status="submitted",
            result={"job_id": str(submitted_job_id), "job": response},
            next_actions=[f"python -m castia optimize status {submitted_job_id} --watch"],
            effects=live,
        ))
    return 0

def _resolve_job(args: argparse.Namespace):
    from castia.optimizing.jobs import load_optimizer_state

    config_dir = _resolve_config_dir(args.config_dir)
    state = (
        None
        if args.job_id and args.project_endpoint
        else load_optimizer_state(config_dir=config_dir)
    )
    return config_dir, args.job_id or state.job_id, args.project_endpoint or state.project_endpoint

def _cmd_optimize_status(args: argparse.Namespace) -> int:
    return _optimizer_status(args, command="optimize status", effects={"network": True})

def _optimizer_status(args: argparse.Namespace, *, command: str, effects: dict) -> int:
    from castia.optimizing.jobs import OptimizerClient, is_terminal_status

    as_json = wants_json(args)
    try:
        _, job_id, endpoint = _resolve_job(args)
        client = OptimizerClient(endpoint)
        while True:
            status = client.status(job_id)
            if not as_json:
                _print_optimizer_status(status)
            if not args.watch or is_terminal_status(status.get("status")):
                break
            __import__("time").sleep(args.poll_interval)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        return _fail(args, command, exc, effects=effects)
    code = 0 if _status_name(status) != "failed" else 1
    if as_json:
        emit_envelope(_optimizer_status_envelope(command, job_id, status, effects=effects))
    return code

def _optimizer_status_envelope(command: str, job_id: str, status: dict, *, effects: dict) -> dict:
    from castia.optimizing.jobs import best_candidate_id, is_terminal_status

    name = _status_name(status)
    progress = status.get("progress") or {}
    result = status.get("result") or {}
    candidates = result.get("candidates") if isinstance(result, dict) else None
    summary = [
        {
            "candidate_id": c.get("candidate_id") or c.get("candidateId"),
            "name": c.get("name"),
            "score": c.get("avg_score") if c.get("avg_score") is not None else c.get("score"),
        }
        for c in (candidates if isinstance(candidates, list) else [])
        if isinstance(c, dict)
    ]
    best = best_candidate_id(status) if name == "succeeded" else None
    if name == "succeeded":
        next_actions = [f"python -m castia optimize apply --job-id {job_id}"]
    elif not is_terminal_status(name):
        next_actions = [f"python -m castia optimize status {job_id} --watch"]
    else:
        next_actions = []
    return command_envelope(
        command,
        status=name,
        result={
            "job_id": job_id,
            "terminal": is_terminal_status(name),
            "progress": progress,
            "candidates": summary,
            "best_candidate_id": best,
            "job": status,
        },
        diagnostics=[diagnostic("error", f"optimizer job {name}", "job_failed")]
        if name == "failed" else [],
        next_actions=next_actions,
        effects=effects,
    )

def _cmd_optimize_cancel(args: argparse.Namespace) -> int:
    from castia.optimizing.jobs import OptimizerClient

    effects = {"network": True, "mutating": True}
    try:
        _, job_id, endpoint = _resolve_job(args)
        status = OptimizerClient(endpoint).cancel(job_id)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        return _fail(args, "optimize cancel", exc, effects=effects)
    if wants_json(args):
        emit_envelope(command_envelope(
            "optimize cancel", status="cancel_requested",
            result={"job_id": job_id, "job": status},
            next_actions=[f"python -m castia optimize status {job_id}"],
            effects=effects,
        ))
        return 0
    print(f"job           : {job_id}")
    print(f"status        : {status.get('status', 'cancel requested')}")
    print("result        : cancel requested")
    return 0

def _cmd_optimize_apply(args: argparse.Namespace) -> int:
    from castia.optimizing.jobs import (
        OptimizerClient,
        apply_candidate_config,
        best_candidate_id,
    )

    effects = {"network": True, "mutating": True}
    try:
        config_dir, job_id, endpoint = _resolve_job(args)
        client = OptimizerClient(endpoint)
        candidate = args.candidate
        if candidate == "best":
            candidate = best_candidate_id(client.status(job_id))
            if not candidate:
                raise ValueError("could not resolve best candidate from job status")
        config = client.candidate_config(job_id, candidate)
        plan = apply_candidate_config(candidate, config, config_dir=config_dir)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, ModuleNotFoundError) as exc:
        return _fail(args, "optimize apply", exc, effects=effects)

    next_step = (
        "set OPTIMIZATION_LOCAL_DIR=.agent_configs and "
        f"OPTIMIZATION_CANDIDATE_ID={candidate}, then deploy with azd"
    )
    if wants_json(args):
        emit_envelope(command_envelope(
            "optimize apply", status="applied",
            result={
                "job_id": job_id,
                "candidate_id": candidate,
                "candidate_dir": str(plan.candidate_dir),
                "metadata_path": str(plan.metadata_path),
                "instructions_path": str(plan.instructions_path) if plan.instructions_path else None,
                "tools_path": str(plan.tools_path) if plan.tools_path else None,
                "skills_dir": str(plan.skills_dir) if plan.skills_dir else None,
            },
            next_actions=[next_step],
            effects=effects,
        ))
        return 0
    print(f"candidate     : {candidate}")
    print(f"wrote         : {plan.metadata_path}")
    if plan.instructions_path:
        print(f"instructions  : {plan.instructions_path.name}")
    if plan.tools_path:
        print(f"tools         : {plan.tools_path.name}")
    if plan.skills_dir:
        print(f"skills        : {plan.skills_dir.name}")
    print(f"next          : {next_step}")
    return 0
def _print_optimizer_status(status: dict) -> None:
    from castia.optimizing.jobs import job_id as optimizer_job_id
    from castia.optimizing.verify import termination_label

    print(f"job           : {optimizer_job_id(status)}")
    label = termination_label(status)
    state = status.get("status")
    if label and str(state or "").lower() in {"succeeded", "completed"}:
        print(f"status        : {state} ({label})")
    else:
        print(f"status        : {state}")
        if label:
            print(f"note          : {label}")
    progress = status.get("progress") or {}
    if progress:
        print(f"baseline optimizer score: {progress.get('baseline_score', '-')}")
        print(f"best optimizer score    : {progress.get('best_score', '-')}")
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
                f"optimizer_score={candidate.get('avg_score') or candidate.get('score')}"
            )

def _load_evaluator_definitions(specs: list[str]) -> dict:
    """Load ``[NAME=]PATH`` evaluator definitions keyed by evaluator name."""

    import json as _json
    from pathlib import Path as _Path

    definitions: dict = {}
    for spec in specs or []:
        name, sep, path = spec.partition("=")
        if not sep:
            name, path = "", spec
        text = _Path(path).read_text(encoding="utf-8")
        if path.lower().endswith((".yaml", ".yml")):
            from castia.optimizing.jobs import _yaml

            data = _yaml().load(text)
        else:
            data = _json.loads(text)
        if not isinstance(data, dict):
            raise TypeError(f"{path}: expected a mapping")
        key = name or str(data.get("name") or "")
        if not key:
            raise ValueError(f"{path}: no 'name' field; use NAME=PATH")
        definitions[key] = data
    return definitions

def _resolve_config_dir(config_dir: str, *, root=None):
    from pathlib import Path as _Path

    path = _Path(config_dir)
    if path.is_absolute():
        return path
    base = _Path(".") if root is None else _Path(root)
    return base / path

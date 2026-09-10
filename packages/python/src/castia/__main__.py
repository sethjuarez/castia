"""``python -m castia`` -- the build-time CLI for the framework.

Subcommands:

    python -m castia deploy                 # rewrite azure.yaml from decorators
    python -m castia deploy --check         # report drift, write nothing

    python -m castia optimize               # sync .agent_configs baseline (tools.json)
    python -m castia optimize --check       # report baseline drift, write nothing
    python -m castia optimize run --dry-run # preview native optimizer payload
    python -m castia optimize run           # submit native optimizer job
    python -m castia optimize status        # inspect the latest native job
    python -m castia optimize apply         # materialize a candidate config

    python -m castia eval check             # validate eval.yaml + rubric files (offline)
    python -m castia eval generate ...      # azd ai agent eval generate (rubric + dataset)
    python -m castia eval update ...        # azd ai agent eval update (re-upload local edits)
    python -m castia eval run ...           # azd ai agent eval run (submit a scored run)

    python -m castia finetune check ...     # validate an RFT dataset + grader (offline)
    python -m castia finetune grader ...    # build a score_model grader from a rubric
    python -m castia finetune submit ...    # fine_tuning.jobs.create (billable RFT job)

``deploy`` / ``optimize`` import the entrypoint only to read its composed
decorators; ``eval check`` is a pure offline gate; and ``eval
{generate,update,run}`` are thin wrappers over the ``azd ai agent eval``
extension (they shell to ``azd`` and, for generate/run, submit **billable**
Foundry jobs -- use ``--dry-run`` to preview). ``optimize run`` submits a
Castia-owned **billable** optimizer job directly to Foundry (use ``--dry-run``
to preview). ``finetune check`` / ``finetune grader`` are pure offline gates;
``finetune submit`` shells to the Foundry fine-tuning API and starts a
**billable** RFT job (use ``--dry-run`` to print the resolved payload and submit
nothing).
"""

from __future__ import annotations

import argparse
import sys

from .deploy import (
    DEFAULT_APP,
    DEFAULT_MANIFEST,
    Plan,
    generate_manifest,
    load_app,
)
from .evalsuite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from .evalsuite import DEFAULT_INSTRUCTION_FILE
from .optimize import (
    DEFAULT_CONFIG_DIR,
    OptimizePlan,
    generate_optimizer_config,
)


def _format_list(protocols) -> str:
    return ", ".join(protocols) if protocols else "(none)"


def _print_plan(plan: Plan, *, check: bool) -> None:
    print(f"agent service : {plan.service}")
    print(f"manifest      : {plan.manifest}")
    print(f"publishable   : {_format_list(plan.desired)}")
    if plan.skipped:
        print(f"local-only    : {_format_list(plan.skipped)}  (not published)")
    print(
        f"service.protocols : {_format_list(plan.before_service)}"
        f"  ->  {_format_list(plan.after_service)}"
    )
    if plan.before_endpoint is not None:
        print(
            f"agentEndpoint     : {_format_list(plan.before_endpoint)}"
            f"  ->  {_format_list(plan.after_endpoint)}"
        )
    for note in plan.notes:
        print(f"note          : {note}")

    if not plan.changed:
        print("result        : up to date")
    elif check:
        print("result        : OUT OF DATE (run without --check to write)")
    elif plan.written:
        print("result        : written")


def _cmd_deploy(args: argparse.Namespace) -> int:
    try:
        app = load_app(args.app)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"error: could not load app {args.app!r}: {exc}", file=sys.stderr)
        return 2

    plan = generate_manifest(app, args.manifest, check=args.check)
    _print_plan(plan, check=args.check)

    # --check is a CI gate: non-zero when the manifest would change.
    if args.check and plan.changed:
        return 1
    return 0


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

    from .optimizer import (
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
    from .optimizer import (
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
    from .optimizer import OptimizerClient, load_optimizer_state

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
    from .optimizer import (
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
    from .optimizer import job_id as optimizer_job_id

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


def _print_suite_plan(plan) -> None:
    print(f"eval config   : {plan.suite.path}")
    ev_names = [e.name or e.local_uri or "(unnamed)" for e in plan.suite.evaluators]
    print(f"evaluators    : {_format_list(ev_names)}")
    for label, dims in plan.rubrics.items():
        names = ", ".join(d.id for d in dims)
        print(f"rubric        : {label}  ({len(dims)} dims: {names})")
    datasets = [d.local_uri or d.dataset_file or d.role for d in plan.suite.datasets]
    print(f"datasets      : {_format_list(datasets)}")
    for note in plan.notes:
        print(f"note          : {note}")
    for problem in plan.problems:
        print(f"problem       : {problem}")
    print("result        : " + ("ok" if plan.ok else "PROBLEMS FOUND"))


def _cmd_eval_check(args: argparse.Namespace) -> int:
    from .evalsuite import load_suite, validate_suite

    try:
        suite = load_suite(args.config)
    except OSError as exc:
        print(f"error: could not read {args.config}: {exc}", file=sys.stderr)
        return 2

    plan = validate_suite(suite)
    _print_suite_plan(plan)
    return 0 if plan.ok else 1


def _run_azd_argv(argv: list[str], *, dry_run: bool) -> int:
    from .evalsuite import run_azd

    print("command       : " + " ".join(argv))
    if dry_run:
        print("result        : dry-run (nothing submitted)")
        return 0
    try:
        return run_azd(argv)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _cmd_eval_generate(args: argparse.Namespace) -> int:
    from .evalsuite import (
        _tracked_instruction_file,
        build_generate_argv,
        load_suite,
    )

    # Anchor missing values to the canonical eval.yaml's agent/eval-model (NOT
    # the out-file, which may not exist yet on a fresh generate), and default the
    # generation instruction to the tracked baseline prompt when the caller
    # supplied neither an instruction nor a BYO dataset.
    agent, eval_model = args.agent, args.eval_model
    suite = None
    for source in (DEFAULT_EVAL_CONFIG, args.config):
        try:
            suite = load_suite(source)
            break
        except OSError:
            suite = None
    if suite is not None:
        agent = agent or suite.agent.get("name")
        eval_model = eval_model or suite.options.get("eval_model") or suite.agent.get("model")

    gen_file = args.gen_instruction_file
    if not gen_file and not args.gen_instruction and not args.gen_dataset:
        gen_file = _tracked_instruction_file()

    argv = build_generate_argv(
        agent=agent,
        gen_instruction=args.gen_instruction,
        gen_instruction_file=gen_file,
        eval_model=eval_model,
        max_samples=args.max_samples,
        evaluators=args.evaluator,
        dataset=args.gen_dataset,
        out_file=args.config,
        trace_days=args.trace_days,
        name=args.name,
        reset_defaults=args.reset_defaults,
        no_wait=args.no_wait,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)


def _cmd_eval_update(args: argparse.Namespace) -> int:
    from .evalsuite import build_update_argv

    argv = build_update_argv(
        config=args.config,
        evaluator_only=args.evaluator_only,
        dataset_only=args.dataset_only,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from .evalsuite import build_run_argv

    argv = build_run_argv(
        config=args.config,
        name=args.name,
        no_wait=args.no_wait,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)


def _cmd_finetune_check(args: argparse.Namespace) -> int:
    from .finetune import (
        load_grader,
        load_jsonl,
        validate_rft_dataset,
        validate_rft_splits,
    )

    try:
        train = load_jsonl(args.dataset)
        validation = load_jsonl(args.validation) if args.validation else None
        grader = load_grader(args.grader) if args.grader else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"training      : {args.dataset}  ({len(train)} examples)")
    if validation is not None:
        print(f"validation    : {args.validation}  ({len(validation)} examples)")
    if args.grader:
        print(f"grader        : {args.grader}  (type: {grader.get('type')!r})")

    if validation is not None:
        problems = validate_rft_splits(train, validation, grader=grader)
    else:
        print("note          : no --validation split given (RFT requires both splits)")
        problems = validate_rft_dataset(train, grader=grader, split="training")

    for problem in problems:
        print(f"problem       : {problem}")
    print("result        : " + ("ok" if not problems else "PROBLEMS FOUND"))
    return 0 if not problems else 1


def _cmd_finetune_grader(args: argparse.Namespace) -> int:
    from .evalsuite import read_rubric
    from .finetune import rubric_to_score_model, validate_grader

    try:
        dims = read_rubric(args.rubric)
    except (OSError, ValueError) as exc:
        print(f"error: could not read rubric {args.rubric}: {exc}", file=sys.stderr)
        return 2
    if not dims:
        print(f"error: rubric {args.rubric} has no dimensions", file=sys.stderr)
        return 2

    try:
        grader = rubric_to_score_model(dims, model=args.model, name=args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    problems = validate_grader(grader)
    for problem in problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if problems:
        return 1

    import json as _json

    rendered = _json.dumps(grader, indent=2)
    if args.out:
        from pathlib import Path as _Path

        _Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(f"rubric        : {args.rubric}  ({len(dims)} dims)")
        print(f"wrote         : {args.out}  (score_model grader)")
    else:
        print(rendered)
    return 0


def _cmd_finetune_submit(args: argparse.Namespace) -> int:
    from .finetune import (
        build_rft_job,
        load_grader,
        load_jsonl,
        validate_rft_splits,
    )

    try:
        grader = load_grader(args.grader)
        train = load_jsonl(args.dataset)
        validation = load_jsonl(args.validation)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    problems = validate_rft_splits(train, validation, grader=grader)
    for problem in problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if problems:
        print("result        : PROBLEMS FOUND (nothing submitted)", file=sys.stderr)
        return 1

    hyperparameters = None
    if args.reasoning_effort or args.eval_interval is not None:
        hyperparameters = {}
        if args.reasoning_effort:
            hyperparameters["reasoning_effort"] = args.reasoning_effort
        if args.eval_interval is not None:
            hyperparameters["eval_interval"] = args.eval_interval

    if args.dry_run:
        # Dry-run stays offline: build the payload from file *paths* (uploads and
        # the real file ids only happen on a live submit) so nothing is stored.
        import json as _json

        job = build_rft_job(
            model=args.model,
            training_file=args.dataset,
            validation_file=args.validation,
            grader=grader,
            hyperparameters=hyperparameters,
            suffix=args.suffix,
        )
        print(f"model         : {args.model}")
        print(f"training      : {args.dataset}  ({len(train)} examples)")
        print(f"validation    : {args.validation}  ({len(validation)} examples)")
        print("payload       :")
        print(_json.dumps(job, indent=2))
        print("result        : dry-run (nothing uploaded or submitted)")
        return 0

    from .finetune import submit_rft_job, upload_file

    try:
        train_id = upload_file(args.dataset)
        val_id = upload_file(args.validation)
        job = build_rft_job(
            model=args.model,
            training_file=train_id,
            validation_file=val_id,
            grader=grader,
            hyperparameters=hyperparameters,
            suffix=args.suffix,
        )
        created = submit_rft_job(job)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"job           : {getattr(created, 'id', created)}")
    print("result        : submitted (billable RFT job)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m castia",
        description="Build-time tooling for Castia agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    deploy = sub.add_parser(
        "deploy",
        help="rewrite azure.yaml's protocols from the Agent decorators",
    )
    deploy.add_argument(
        "--app",
        default=DEFAULT_APP,
        help=f"entrypoint as module:attr (default: {DEFAULT_APP})",
    )
    deploy.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help=f"path to azure.yaml (default: {DEFAULT_MANIFEST})",
    )
    deploy.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero without writing",
    )
    deploy.set_defaults(func=_cmd_deploy)

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

    ev = sub.add_parser(
        "eval",
        help="Foundry eval-suite tooling (validate, generate, update, run)",
    )
    evsub = ev.add_subparsers(dest="eval_command", required=True)

    ev_check = evsub.add_parser(
        "check",
        help="validate eval.yaml + rubric dimensions files (offline, no Azure)",
    )
    ev_check.add_argument(
        "--config",
        default=DEFAULT_EVAL_CONFIG,
        help=f"path to the eval config (default: {DEFAULT_EVAL_CONFIG})",
    )
    ev_check.set_defaults(func=_cmd_eval_check)

    ev_gen = evsub.add_parser(
        "generate",
        help="azd ai agent eval generate -- synthesize a rubric + dataset",
    )
    ev_gen.add_argument("--agent", default=None, help="agent name (default: eval.yaml agent.name)")
    ev_gen.add_argument(
        "--eval-model",
        dest="eval_model",
        default=None,
        help="model for generation + scoring (default: eval.yaml eval_model)",
    )
    ev_gen.add_argument(
        "--gen-instruction-file",
        dest="gen_instruction_file",
        default=None,
        help=f"instruction file (default: {DEFAULT_INSTRUCTION_FILE} if present)",
    )
    ev_gen.add_argument(
        "--gen-instruction",
        dest="gen_instruction",
        default=None,
        help="inline agent instruction (alternative to --gen-instruction-file)",
    )
    ev_gen.add_argument("--max-samples", dest="max_samples", type=int, default=None,
                        help="samples to synthesize (15-1000)")
    ev_gen.add_argument("--evaluator", action="append", default=[],
                        help="seed builtin/custom evaluator (repeatable)")
    ev_gen.add_argument("--dataset", dest="gen_dataset", default=None,
                        help="BYO dataset instead of generating one")
    ev_gen.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"output eval config path / --out-file (default: {DEFAULT_EVAL_CONFIG})")
    ev_gen.add_argument("--trace-days", dest="trace_days", type=int, default=None,
                        help="fold in traces from the last N days (0 = none)")
    ev_gen.add_argument("--name", default=None, help="name for the eval suite")
    ev_gen.add_argument("--reset-defaults", dest="reset_defaults", action="store_true",
                        help="overwrite an existing eval config")
    ev_gen.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="submit jobs and return immediately")
    ev_gen.add_argument("--no-prompt", dest="no_prompt", action="store_true",
                        help="run without interactive prompts")
    ev_gen.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the resolved azd command and submit nothing")
    ev_gen.set_defaults(func=_cmd_eval_generate)

    ev_up = evsub.add_parser(
        "update",
        help="azd ai agent eval update -- re-upload edited rubric/dataset files",
    )
    ev_up.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                       help=f"path to the eval config (default: {DEFAULT_EVAL_CONFIG})")
    ev_up.add_argument("--evaluator-only", dest="evaluator_only", action="store_true",
                       help="only update evaluators (the rubric)")
    ev_up.add_argument("--dataset-only", dest="dataset_only", action="store_true",
                       help="only update the dataset")
    ev_up.add_argument("--no-prompt", dest="no_prompt", action="store_true",
                       help="run without interactive prompts")
    ev_up.add_argument("--dry-run", dest="dry_run", action="store_true",
                       help="print the resolved azd command and run nothing")
    ev_up.set_defaults(func=_cmd_eval_update)

    ev_run = evsub.add_parser(
        "run",
        help="azd ai agent eval run -- submit a scored run against the agent",
    )
    ev_run.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"path to the eval config (default: {DEFAULT_EVAL_CONFIG})")
    ev_run.add_argument("--name", default=None, help="name for the eval run")
    ev_run.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="start the run and return immediately")
    ev_run.add_argument("--no-prompt", dest="no_prompt", action="store_true",
                        help="run without interactive prompts")
    ev_run.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the resolved azd command and submit nothing")
    ev_run.set_defaults(func=_cmd_eval_run)

    ft = sub.add_parser(
        "finetune",
        help="reinforcement fine-tuning (RFT) -- prepare/validate/submit a job",
    )
    ftsub = ft.add_subparsers(dest="finetune_command", required=True)

    ft_check = ftsub.add_parser(
        "check",
        help="validate an RFT dataset (+ optional grader) offline, no Azure",
    )
    ft_check.add_argument("--dataset", required=True,
                          help="training split JSONL (messages, final role=user)")
    ft_check.add_argument("--validation", default=None,
                          help="validation split JSONL (RFT requires both splits)")
    ft_check.add_argument("--grader", default=None,
                          help="grader JSON to cross-check item.* references against")
    ft_check.set_defaults(func=_cmd_finetune_check)

    ft_grader = ftsub.add_parser(
        "grader",
        help="build a score_model grader from an eval rubric (offline bridge)",
    )
    ft_grader.add_argument("--rubric", required=True,
                           help="rubric dimensions file (id/description/weight)")
    ft_grader.add_argument("--model", required=True, help="judge model deployment")
    ft_grader.add_argument("--name", default="rubric_score", help="grader name")
    ft_grader.add_argument("--out", default=None,
                           help="write the grader JSON here (default: stdout)")
    ft_grader.set_defaults(func=_cmd_finetune_grader)

    ft_submit = ftsub.add_parser(
        "submit",
        help="fine_tuning.jobs.create -- start a billable RFT job",
    )
    ft_submit.add_argument("--model", required=True,
                           help="base reasoning model to fine-tune (e.g. o4-mini)")
    ft_submit.add_argument("--dataset", required=True, help="training split JSONL")
    ft_submit.add_argument("--validation", required=True, help="validation split JSONL")
    ft_submit.add_argument("--grader", required=True, help="grader JSON")
    ft_submit.add_argument("--reasoning-effort", dest="reasoning_effort", default=None,
                           choices=("low", "medium", "high"),
                           help="RFT reasoning_effort hyperparameter")
    ft_submit.add_argument("--eval-interval", dest="eval_interval", type=int, default=None,
                           help="RFT eval_interval hyperparameter")
    ft_submit.add_argument("--suffix", default=None, help="fine-tuned model name suffix")
    ft_submit.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="print the resolved payload and submit nothing")
    ft_submit.set_defaults(func=_cmd_finetune_submit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command registration and handlers for evaluation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from castia.evaluation.azd_eval import DEFAULT_EVALS_PATH
from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.evaluation.suite import DEFAULT_INSTRUCTION_FILE


def register_commands(sub: argparse._SubParsersAction) -> None:
    ev = sub.add_parser(
        "eval",
        help="Foundry eval-suite tooling (check, init, generate, update, run)",
    )
    evsub = ev.add_subparsers(dest="eval_command", required=True)

    ev_check = evsub.add_parser(
        "check",
        help="validate eval.yaml, rubric files and azure.eval.yaml drift (offline, no Azure)",
    )
    ev_check.add_argument(
        "--config",
        default=DEFAULT_EVAL_CONFIG,
        help=f"path to the eval config (default: {DEFAULT_EVAL_CONFIG})",
    )
    _add_path(ev_check)
    ev_check.set_defaults(func=_cmd_eval_check)

    ev_init = evsub.add_parser(
        "init",
        help="azd ai eval init -- scaffold azure.eval.yaml from eval.yaml (offline)",
    )
    ev_init.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                         help=f"castia eval config to derive from (default: {DEFAULT_EVAL_CONFIG})")
    _add_path(ev_init)
    ev_init.add_argument("--name", default=None, help="name of the eval to declare")
    ev_init.add_argument("--evaluation-level", dest="evaluation_level", default=None,
                         choices=["turn", "conversation"], help="what one sample is")
    ev_init.add_argument("--dry-run", dest="dry_run", action="store_true",
                         help="print the resolved azd command and write nothing")
    ev_init.set_defaults(func=_cmd_eval_init)

    ev_gen = evsub.add_parser(
        "generate",
        help="azd ai eval generate -- synthesize a rubric + dataset (billable)",
    )
    ev_gen.add_argument("--agent", default=None, help="agent name (default: eval.yaml agent.name)")
    ev_gen.add_argument(
        "--eval-model",
        dest="eval_model",
        default=None,
        help="model that generates the artifacts (default: eval.yaml eval_model)",
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
    ev_gen.add_argument("--trace-days", dest="trace_days", type=int, default=None,
                        help="fold in traces from the last N days (0 = none)")
    ev_gen.add_argument("--artifact", choices=["dataset", "evaluator"], default=None,
                        help="generate only this artifact (default: both)")
    ev_gen.add_argument("--dataset-name", dest="dataset_name", default=None,
                        help="name for the generated dataset")
    ev_gen.add_argument("--evaluator-name", dest="evaluator_name", default=None,
                        help="name for the generated rubric evaluator")
    ev_gen.add_argument("--force", "--reset-defaults", dest="force", action="store_true",
                        help="overwrite existing generated files")
    ev_gen.add_argument("--evaluator", action="append", default=[],
                        help="legacy backend only: seed evaluator (repeatable)")
    ev_gen.add_argument("--dataset", dest="gen_dataset", default=None,
                        help="legacy backend only: BYO dataset instead of generating one")
    ev_gen.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"legacy backend only: output eval config (default: {DEFAULT_EVAL_CONFIG})")
    ev_gen.add_argument("--name", default=None, help="legacy backend only: name for the eval suite")
    ev_gen.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="submit jobs and return immediately")
    _add_azd_common(ev_gen)
    ev_gen.set_defaults(func=_cmd_eval_generate)

    ev_up = evsub.add_parser(
        "update",
        help="publish new versions of edited dataset (legacy: and rubric) files",
    )
    ev_up.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                       help=f"legacy backend only: eval config (default: {DEFAULT_EVAL_CONFIG})")
    only = ev_up.add_mutually_exclusive_group()
    only.add_argument("--evaluator-only", dest="evaluator_only", action="store_true",
                      help="only update evaluators (the rubric)")
    only.add_argument("--dataset-only", dest="dataset_only", action="store_true",
                      help="only update the dataset")
    _add_azd_common(ev_up)
    ev_up.set_defaults(func=_cmd_eval_update)

    ev_run = evsub.add_parser(
        "run",
        help="azd ai eval run start -- run a deployed eval against the agent (billable)",
    )
    ev_run.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"legacy backend only: eval config (default: {DEFAULT_EVAL_CONFIG})")
    ev_run.add_argument("--eval", dest="eval_name", default=None,
                        help="eval declared in azure.eval.yaml (default: the only one)")
    ev_run.add_argument("--name", default=None, help="name for the eval run")
    ev_run.add_argument("--deploy", action="store_true",
                        help="deploy the eval with 'azd ai eval create' before running it")
    ev_run.add_argument("--fail-on", dest="fail_on", default=None,
                        help="any-failure or pass-rate=<0..1>; exits 1 when missed")
    ev_run.add_argument("--max-samples", dest="max_samples", type=int, default=None,
                        help="cap local-source rows")
    ev_run.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="start the run and return immediately")
    _add_azd_common(ev_run)
    ev_run.set_defaults(func=_cmd_eval_run)


def _add_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", default=DEFAULT_EVALS_PATH,
                        help=f"directory holding azure.eval.yaml (default: {DEFAULT_EVALS_PATH})")


def _add_azd_common(parser: argparse.ArgumentParser) -> None:
    _add_path(parser)
    parser.add_argument("--backend", default=None, choices=["auto", "evaluations", "agent"],
                        help="azd extension to drive (default: auto-detect; env CASTIA_EVAL_BACKEND)")
    parser.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                        help="Foundry project endpoint (evaluations backend)")
    parser.add_argument("--no-prompt", dest="no_prompt", action="store_true",
                        help="run without interactive prompts")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the resolved azd command and submit nothing")


def _format_list(protocols) -> str:
    return ", ".join(protocols) if protocols else "(none)"


def _print_suite_plan(plan, drift: list[str] | None = None) -> None:
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
    for problem in [*plan.problems, *(drift or [])]:
        print(f"problem       : {problem}")
    ok = plan.ok and not drift
    print("result        : " + ("ok" if ok else "PROBLEMS FOUND"))


def _cmd_eval_check(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import (
        EvalPortError,
        check_drift,
        eval_config_path,
        load_eval_definitions,
    )
    from castia.evaluation.suite import load_suite, validate_suite

    try:
        suite = load_suite(args.config)
    except OSError as exc:
        print(f"error: could not read {args.config}: {exc}", file=sys.stderr)
        return 2

    plan = validate_suite(suite)
    drift: list[str] = []
    if eval_config_path(args.path).exists():
        try:
            drift = check_drift(suite, load_eval_definitions(args.path), path=args.path)
        except EvalPortError as exc:
            drift = [str(exc)]
        print(f"azd config    : {eval_config_path(args.path)}")
    _print_suite_plan(plan, drift)
    return 0 if plan.ok and not drift else 1


def _run_azd_argv(argv: list[str], *, dry_run: bool) -> int:
    from castia.evaluation.suite import run_azd

    print("command       : " + " ".join(argv))
    if dry_run:
        print("result        : dry-run (nothing submitted)")
        return 0
    try:
        return run_azd(argv)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_azd_plan(plans: list[list[str]], *, dry_run: bool) -> int:
    """Run commands in order, stopping at the first failure."""

    for done, argv in enumerate(plans):
        rc = _run_azd_argv(argv, dry_run=dry_run)
        if rc != 0:
            if done:
                print(f"error: stopped after {done} of {len(plans)} commands", file=sys.stderr)
            return rc
    return 0


def _backend(args: argparse.Namespace) -> str | None:
    from castia.evaluation.azd_eval import EvalPortError, resolve_backend

    try:
        backend = resolve_backend(args.backend)
    except (EvalPortError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    print(f"backend       : {backend}")
    if backend == "agent":
        print("note          : legacy 'azd ai agent eval' (removed in azure.ai.agents "
              "1.0.0-beta.22); install azure.ai.evaluations to move to 'azd ai eval'")
    return backend


def _legacy_only(args: argparse.Namespace, **flags: object) -> int:
    used = [name for name, value in flags.items() if value]
    if not used:
        return 0
    print("error: the legacy 'azd ai agent eval' backend is required for " + ", ".join(used)
          + "; see 'python -m castia eval generate --help'", file=sys.stderr)
    return 2


def _evaluations_only(args: argparse.Namespace, **flags: object) -> int:
    used = [name for name, value in flags.items() if value]
    if not used:
        return 0
    print("error: the azure.ai.evaluations backend is required for " + ", ".join(used)
          + " ('azd extension install azure.ai.evaluations')", file=sys.stderr)
    return 2


def _cmd_eval_init(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import (
        EvalPortError,
        build_init_argv,
        eval_config_path,
    )
    from castia.evaluation.suite import load_suite

    try:
        suite = load_suite(args.config)
    except OSError as exc:
        print(f"error: could not read {args.config}: {exc}", file=sys.stderr)
        return 2
    if not Path("azure.yaml").exists():
        print("error: run from the azd project root (azure.yaml); 'azd ai eval init' adds "
              "the eval service to it", file=sys.stderr)
        return 2
    try:
        argv = build_init_argv(suite, path=args.path, name=args.name,
                               evaluation_level=args.evaluation_level)
    except EvalPortError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rc = _run_azd_argv(argv, dry_run=args.dry_run)
    if rc == 0 and not args.dry_run:
        print(f"next          : review {eval_config_path(args.path)}, then "
              "'python -m castia eval run --deploy'")
    return rc


def _suite_defaults(*configs: str):
    from castia.evaluation.suite import load_suite

    for source in configs:
        try:
            return load_suite(source)
        except OSError:
            continue
    return None


def _cmd_eval_generate(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import EvalPortError
    from castia.evaluation.azd_eval import (
        build_generate_argv as build_ai_eval_generate_argv,
    )
    from castia.evaluation.suite import _tracked_instruction_file, build_generate_argv

    backend = _backend(args)
    if backend is None:
        return 2

    # Anchor missing values to the canonical eval.yaml's agent/eval-model (NOT
    # the out-file, which may not exist yet on a fresh generate), and default the
    # generation instruction to the tracked baseline prompt when the caller
    # supplied neither an instruction nor a BYO dataset.
    agent, eval_model = args.agent, args.eval_model
    suite = _suite_defaults(DEFAULT_EVAL_CONFIG, args.config)
    if suite is not None:
        agent = agent or suite.agent.get("name")
        eval_model = eval_model or suite.options.get("eval_model") or suite.agent.get("model")

    gen_file = args.gen_instruction_file
    if not gen_file and not args.gen_instruction and not args.gen_dataset:
        gen_file = _tracked_instruction_file()

    if backend == "agent":
        rc = _evaluations_only(args, **{
            "--artifact": args.artifact,
            "--dataset-name": args.dataset_name,
            "--evaluator-name": args.evaluator_name,
            "--project-endpoint": args.project_endpoint,
        })
        if rc:
            return rc
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
            reset_defaults=args.force,
            no_wait=args.no_wait,
            no_prompt=args.no_prompt,
        )
        return _run_azd_argv(argv, dry_run=args.dry_run)

    rc = _legacy_only(
        args,
        **{"--dataset": args.gen_dataset, "--evaluator": args.evaluator, "--name": args.name},
    )
    if rc:
        return rc
    try:
        argv = build_ai_eval_generate_argv(
            target=agent,
            agent_instruction=args.gen_instruction,
            agent_instruction_file=gen_file,
            generation_model=eval_model,
            max_samples=args.max_samples,
            trace_days=args.trace_days,
            artifact=args.artifact,
            dataset_name=args.dataset_name,
            evaluator_name=args.evaluator_name,
            force=args.force,
            no_wait=args.no_wait,
            path=args.path,
            project_endpoint=args.project_endpoint,
            no_prompt=args.no_prompt,
        )
    except EvalPortError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _run_azd_argv(argv, dry_run=args.dry_run)


def _cmd_eval_update(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import (
        EvalPortError,
        build_dataset_update_plan,
        load_eval_definitions,
    )
    from castia.evaluation.suite import build_update_argv

    backend = _backend(args)
    if backend is None:
        return 2
    if backend == "agent":
        rc = _evaluations_only(args, **{"--project-endpoint": args.project_endpoint})
        if rc:
            return rc
        argv = build_update_argv(
            config=args.config,
            evaluator_only=args.evaluator_only,
            dataset_only=args.dataset_only,
            no_prompt=args.no_prompt,
        )
        return _run_azd_argv(argv, dry_run=args.dry_run)

    if args.evaluator_only:
        print("error: publish an edited rubric with 'azd ai eval evaluator update <name> "
              "--from-file <rubric.json>'; castia updates datasets only on this backend",
              file=sys.stderr)
        return 2
    try:
        plans = build_dataset_update_plan(
            load_eval_definitions(args.path),
            path=args.path,
            project_endpoint=args.project_endpoint,
            no_prompt=args.no_prompt,
        )
    except (OSError, EvalPortError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.dataset_only:
        print("note          : rubric evaluators are not re-published; use "
              "'azd ai eval evaluator update <name> --from-file <rubric.json>'")
    return _run_azd_plan(plans, dry_run=args.dry_run)


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import (
        EvalPortError,
        build_create_argv,
        build_run_start_argv,
        load_eval_definitions,
        select_eval,
    )
    from castia.evaluation.suite import build_run_argv

    backend = _backend(args)
    if backend is None:
        return 2
    if backend == "agent":
        rc = _evaluations_only(args, **{
            "--eval": args.eval_name,
            "--deploy": args.deploy,
            "--fail-on": args.fail_on,
            "--max-samples": args.max_samples,
            "--project-endpoint": args.project_endpoint,
        })
        if rc:
            return rc
        argv = build_run_argv(
            config=args.config,
            name=args.name,
            no_wait=args.no_wait,
            no_prompt=args.no_prompt,
        )
        return _run_azd_argv(argv, dry_run=args.dry_run)

    try:
        eval_name = select_eval(load_eval_definitions(args.path), args.eval_name)
        plans = []
        if args.deploy:
            plans.append(build_create_argv(eval_name, path=args.path,
                                           project_endpoint=args.project_endpoint,
                                           no_prompt=args.no_prompt))
        plans.append(build_run_start_argv(
            eval_name=eval_name,
            path=args.path,
            name=args.name,
            no_wait=args.no_wait,
            fail_on=args.fail_on,
            max_samples=args.max_samples,
            project_endpoint=args.project_endpoint,
            no_prompt=args.no_prompt,
        ))
    except FileNotFoundError:
        print(f"error: no azure.eval.yaml under {args.path!r}; run 'python -m castia eval init'",
              file=sys.stderr)
        return 2
    except EvalPortError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _run_azd_plan(plans, dry_run=args.dry_run)

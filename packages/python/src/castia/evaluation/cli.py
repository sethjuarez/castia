"""Command registration and handlers for evaluation."""

from __future__ import annotations

import argparse
import sys

from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.evaluation.suite import DEFAULT_INSTRUCTION_FILE


def register_commands(sub: argparse._SubParsersAction) -> None:
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


def _format_list(protocols) -> str:
    return ", ".join(protocols) if protocols else "(none)"

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
    from castia.evaluation.suite import load_suite, validate_suite

    try:
        suite = load_suite(args.config)
    except OSError as exc:
        print(f"error: could not read {args.config}: {exc}", file=sys.stderr)
        return 2

    plan = validate_suite(suite)
    _print_suite_plan(plan)
    return 0 if plan.ok else 1

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

def _cmd_eval_generate(args: argparse.Namespace) -> int:
    from castia.evaluation.suite import (
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
    from castia.evaluation.suite import build_update_argv

    argv = build_update_argv(
        config=args.config,
        evaluator_only=args.evaluator_only,
        dataset_only=args.dataset_only,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)

def _cmd_eval_run(args: argparse.Namespace) -> int:
    from castia.evaluation.suite import build_run_argv

    argv = build_run_argv(
        config=args.config,
        name=args.name,
        no_wait=args.no_wait,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)

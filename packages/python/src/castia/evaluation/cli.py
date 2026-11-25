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
    ev_check.add_argument(
        "--require-pinned",
        dest="require_pinned",
        action="store_true",
        help="fail when a non-builtin evaluator has no version",
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
    ev_run.add_argument("--name", default=None,
                        help="explicit run name (prefer --rung; runs can't be renamed later)")
    ev_run.add_argument("--rung", default=None,
                        help="ladder label; names the run '<rung> · n<rows>'")
    ev_run.add_argument("--rows", type=int, default=None,
                        help="row count for the run name (default: lines in the eval.yaml dataset)")
    ev_run.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="start the run and return immediately")
    ev_run.add_argument("--no-prompt", dest="no_prompt", action="store_true",
                        help="run without interactive prompts")
    ev_run.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="print the resolved azd command and submit nothing")
    ev_run.add_argument("--allow-unpinned", dest="allow_unpinned", action="store_true",
                        help="run even when a custom evaluator has no version (not reproducible)")
    ev_run.set_defaults(func=_cmd_eval_run)

    ev_evr = evsub.add_parser(
        "evaluator",
        help="export/import custom evaluators by content hash and pin their versions",
    )
    evrsub = ev_evr.add_subparsers(dest="evaluator_command", required=True)
    ev_exp = evrsub.add_parser("export", help="write an evaluator version to a hashed bundle")
    ev_exp.add_argument("--name", required=True, help="evaluator name")
    ev_exp.add_argument("--version", default=None, help="version to export (default: latest)")
    ev_exp.add_argument("--out", default="evaluators", help="output folder (default: evaluators)")
    ev_exp.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                        help="Foundry project endpoint (default: FOUNDRY_PROJECT_ENDPOINT)")
    ev_exp.set_defaults(func=_cmd_eval_evaluator_export)
    ev_imp = evrsub.add_parser(
        "import", help="create (or reuse) an evaluator version matching a bundle's hash"
    )
    ev_imp.add_argument("bundle", help="bundle file written by 'eval evaluator export'")
    ev_imp.add_argument("--name", default=None, help="target evaluator name (default: bundle name)")
    ev_imp.add_argument("--pin-version", dest="pin_version", action="store_true",
                        help="write the resulting version into the eval config")
    ev_imp.add_argument("--config", default=DEFAULT_EVAL_CONFIG,
                        help=f"eval config to pin (default: {DEFAULT_EVAL_CONFIG})")
    ev_imp.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                        help="Foundry project endpoint (default: FOUNDRY_PROJECT_ENDPOINT)")
    ev_imp.set_defaults(func=_cmd_eval_evaluator_import)

    ev_del = evsub.add_parser(
        "delete",
        help="delete a Foundry eval or eval run (optionally wait until it is gone)",
    )
    ev_del.add_argument("--eval-id", dest="eval_id", required=True, help="eval id")
    ev_del.add_argument("--run-id", dest="run_id", default=None,
                        help="eval run id (omit to delete the whole eval)")
    ev_del.add_argument("--wait", action="store_true",
                        help="poll the delete location until the resource is gone")
    ev_del.add_argument("--timeout", type=float, default=600.0,
                        help="seconds to wait with --wait (default: 600)")
    ev_del.add_argument("--interval", type=float, default=10.0,
                        help="seconds between polls with --wait (default: 10)")
    ev_del.add_argument("--project-endpoint", dest="project_endpoint", default=None,
                        help="Foundry project endpoint (default: FOUNDRY_PROJECT_ENDPOINT)")
    ev_del.set_defaults(func=_cmd_eval_delete)


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
    if getattr(args, "require_pinned", False):
        from castia.evaluation.evaluators import find_unpinned_evaluators

        unpinned = find_unpinned_evaluators(suite)
        for name in unpinned:
            print(f"problem       : evaluator '{name}' has no version -- pin it "
                  "(python -m castia eval evaluator import ... --pin-version)")
        if unpinned:
            return 1
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

def _resolve_run_name(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Return ``(name, error)`` for ``eval run`` from --name / --rung / --rows."""
    from castia.evaluation.foundry import is_templated_run_name, run_name

    if args.name and args.rung:
        return None, "pass --name or --rung, not both"
    if args.name:
        if not is_templated_run_name(args.name):
            print(
                f"warning: run name {args.name!r} is not '<rung> · n<rows>'; "
                "Foundry runs can't be renamed later",
                file=sys.stderr,
            )
        return args.name, None
    if not args.rung:
        if args.dry_run:
            return None, None
        return None, "pass --rung (names the run '<rung> · n<rows>') or --name"
    rows = args.rows
    if rows is None:
        rows = _dataset_rows(args.config)
        if rows is None:
            return None, "could not count dataset rows from the eval config; pass --rows"
    try:
        return run_name(args.rung, rows), None
    except ValueError as exc:
        return None, str(exc)


def _dataset_rows(config: str) -> int | None:
    from castia.evaluation.foundry import count_jsonl_rows
    from castia.evaluation.suite import load_suite

    try:
        suite = load_suite(config)
    except OSError:
        return None
    for ds in suite.datasets:
        if ds.role == "dataset" and ds.local_uri:
            try:
                return count_jsonl_rows(suite.root / ds.local_uri)
            except OSError:
                return None
    return None


def _cmd_eval_run(args: argparse.Namespace) -> int:
    from castia.evaluation.suite import build_run_argv

    name, error = _resolve_run_name(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    error = _unpinned_error(args.config, allow=args.allow_unpinned)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    argv = build_run_argv(
        config=args.config,
        name=name,
        no_wait=args.no_wait,
        no_prompt=args.no_prompt,
    )
    return _run_azd_argv(argv, dry_run=args.dry_run)


def _project_endpoint(args: argparse.Namespace) -> str | None:
    import os

    return args.project_endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")


def _unpinned_error(config: str, *, allow: bool) -> str | None:
    from castia.evaluation.evaluators import find_unpinned_evaluators
    from castia.evaluation.suite import load_suite

    try:
        suite = load_suite(config)
    except OSError:
        return None  # azd reports a missing config itself
    unpinned = find_unpinned_evaluators(suite)
    if not unpinned:
        return None
    names = ", ".join(unpinned)
    if allow:
        print(f"warning: unpinned evaluators ({names}); scores are not reproducible",
              file=sys.stderr)
        return None
    return (f"unpinned evaluators: {names}. Pin a version in {config} "
            "(python -m castia eval evaluator import BUNDLE --pin-version) "
            "or pass --allow-unpinned")


def _eval_client(args: argparse.Namespace):
    from castia.evaluation.foundry import FoundryEvalClient

    endpoint = _project_endpoint(args)
    if not endpoint:
        print("error: FOUNDRY_PROJECT_ENDPOINT is not set (or pass --project-endpoint)",
              file=sys.stderr)
        return None
    return FoundryEvalClient(endpoint)


def _cmd_eval_evaluator_export(args: argparse.Namespace) -> int:
    from castia.evaluation.evaluators import export_evaluator

    client = _eval_client(args)
    if client is None:
        return 2
    try:
        bundle = export_evaluator(client, args.name, args.version)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    path = bundle.write(args.out)
    print(f"evaluator     : {bundle.name} v{bundle.version}")
    print(f"content hash  : {bundle.content_hash}")
    print(f"bundle        : {path}")
    return 0


def _cmd_eval_evaluator_import(args: argparse.Namespace) -> int:
    from castia.evaluation.evaluators import (
        EvaluatorBundle,
        import_evaluator,
        pin_evaluator_version,
    )

    try:
        bundle = EvaluatorBundle.load(args.bundle)
    except (OSError, KeyError, ValueError) as exc:
        print(f"error: could not load bundle {args.bundle}: {exc}", file=sys.stderr)
        return 2
    client = _eval_client(args)
    if client is None:
        return 2
    result = import_evaluator(client, bundle, name=args.name)
    action = "created" if result.created else "reused"
    print(f"evaluator     : {result.name} v{result.version} ({action})")
    print(f"content hash  : {result.content_hash}")
    if args.pin_version:
        if not pin_evaluator_version(args.config, result.name, result.version):
            print(f"error: no evaluator named '{result.name}' in {args.config}", file=sys.stderr)
            return 1
        print(f"pinned        : {args.config}")
    return 0


def _cmd_eval_delete(args: argparse.Namespace) -> int:
    from castia.evaluation.foundry import FoundryEvalClient

    endpoint = _project_endpoint(args)
    if not endpoint:
        print("error: FOUNDRY_PROJECT_ENDPOINT is not set (or pass --project-endpoint)",
              file=sys.stderr)
        return 2
    client = FoundryEvalClient(endpoint)
    target = f"run {args.run_id} of eval {args.eval_id}" if args.run_id else f"eval {args.eval_id}"
    try:
        if args.run_id:
            result = client.delete_eval_run(args.eval_id, args.run_id, wait=args.wait,
                                            timeout=args.timeout, interval=args.interval)
        else:
            result = client.delete_eval(args.eval_id, wait=args.wait,
                                        timeout=args.timeout, interval=args.interval)
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.get("deleted") is True or args.wait:
        print(f"deleted       : {target}")
    else:
        print(f"delete queued : {target} (pass --wait to confirm it is gone)")
    return 0

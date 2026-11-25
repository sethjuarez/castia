"""Command registration and handlers for evaluation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from castia.evaluation.azd_eval import DEFAULT_EVALS_PATH
from castia.evaluation.suite import DEFAULT_CONFIG as DEFAULT_EVAL_CONFIG
from castia.evaluation.suite import DEFAULT_INSTRUCTION_FILE
from castia.lifecycle.output import (
    add_json_argument,
    command_envelope,
    diagnostic,
    emit_envelope,
    error_envelope,
    wants_json,
)


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
    ev_check.add_argument(
        "--require-pinned",
        dest="require_pinned",
        action="store_true",
        help="fail when a non-builtin evaluator has no version",
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
                         choices=["turn", "conversation"],
                         help="what one sample is; conversation scaffolds static mode "
                              "(scores completed messages, no agent target)")
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
                        help=f"castia eval config: legacy run target, run-name row count and "
                             f"evaluator pinning (default: {DEFAULT_EVAL_CONFIG})")
    ev_run.add_argument("--eval", dest="eval_name", default=None,
                        help="eval declared in azure.eval.yaml (default: the only one)")
    ev_run.add_argument("--name", default=None,
                        help="explicit run name (prefer --rung; runs can't be renamed later)")
    ev_run.add_argument("--deploy", action="store_true",
                        help="deploy the eval with 'azd ai eval create' before running it")
    ev_run.add_argument("--fail-on", dest="fail_on", default=None,
                        help="any-failure or pass-rate=<0..1>; exits 1 when missed")
    ev_run.add_argument("--max-samples", dest="max_samples", type=int, default=None,
                        help="cap local-source rows")
    ev_run.add_argument("--rung", default=None,
                        help="ladder label; names the run '<rung> · n<rows>'")
    ev_run.add_argument("--rows", type=int, default=None,
                        help="row count for the run name (default: lines in the eval.yaml dataset)")
    ev_run.add_argument("--no-wait", dest="no_wait", action="store_true",
                        help="start the run and return immediately")
    ev_run.add_argument("--allow-unpinned", dest="allow_unpinned", action="store_true",
                        help="run even when a custom evaluator has no version (not reproducible)")
    harness = ev_run.add_argument_group(
        "repeated runs",
        "--runs N re-submits a template run's data source N times through the Foundry "
        "REST API, gates each run, and reports mean [min, max] with n",
    )
    harness.add_argument("--runs", type=int, default=None,
                         help="number of repeats (enables the harness)")
    harness.add_argument("--eval-id", dest="eval_id", default=None, help="eval id to repeat")
    harness.add_argument("--template-run", dest="template_run", default=None,
                         help="existing run of --eval-id whose data source is reused")
    harness.add_argument("--max-in-flight", dest="max_in_flight", type=int, default=3,
                         help="concurrent runs (default: 3)")
    harness.add_argument("--min-scored", dest="min_scored", type=float, default=0.95,
                         help="reject runs scoring fewer than this fraction of rows (default: 0.95)")
    harness.add_argument("--max-errors", dest="max_errors", type=int, default=10,
                         help="reject runs with more errored rows (default: 10; -1 = no limit)")
    harness.add_argument("--max-tool-error-rate", dest="max_tool_error_rate", type=float,
                         default=None,
                         help="also reject runs whose tool calls fail above this rate "
                              "(e.g. 0.1; reads per-row outputs; needs castia.evaluation.tool_errors)")
    harness.add_argument("--min-tool-calls", dest="min_tool_calls", type=int, default=0,
                         help="with --max-tool-error-rate, reject runs with fewer tool calls "
                              "(catches tools silently dropped; default: 0)")
    harness.add_argument("--state", default=None,
                         help="resumable state file (default: .castia/eval_runs/<rung>-n<rows>.json)")
    harness.add_argument("--poll-interval", dest="poll_interval", type=float, default=30.0,
                         help="seconds between polls (default: 30)")
    harness.add_argument("--timeout", type=float, default=4 * 3600.0,
                         help="seconds before giving up; rerun to resume (default: 14400)")
    _add_azd_common(ev_run)
    ev_run.set_defaults(func=_cmd_eval_run)

    ev_rubric = evsub.add_parser("rubric", help="offline rubric authoring helpers")
    rubric_sub = ev_rubric.add_subparsers(dest="rubric_command", required=True)
    ev_compile = rubric_sub.add_parser(
        "compile",
        help="resolve gate: true dimensions into enforcing weights (offline)",
    )
    ev_compile.add_argument("source", help="authored rubric file (may contain gate: true)")
    ev_compile.add_argument("--out", required=True, help="compiled dimensions file to write")
    ev_compile.add_argument("--pass-threshold", dest="pass_threshold", type=float,
                            default=0.5, help="rubric Likert pass threshold (default: 0.5)")
    ev_compile.set_defaults(func=_cmd_eval_rubric_compile)

    ev_tpl = evsub.add_parser("template", help="write an evaluator template (offline)")
    ev_tpl.add_argument("kind", choices=["gold-aware"], help="template to write")
    ev_tpl.add_argument("--out", default="evaluators", help="output folder (default: evaluators)")
    ev_tpl.add_argument("--pass-threshold", dest="pass_threshold", type=float,
                        default=0.5, help="rubric Likert pass threshold (default: 0.5)")
    ev_tpl.add_argument("--force", action="store_true", help="overwrite existing files")
    ev_tpl.set_defaults(func=_cmd_eval_template)

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

    for parser in (ev_check, ev_gen, ev_up, ev_run):
        add_json_argument(parser)

    from castia.evaluation import schedule

    schedule.register_commands(evsub)


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
        find_unpinned_eval_evaluators,
        load_eval_definitions,
    )
    from castia.evaluation.suite import load_suite, validate_suite

    try:
        suite = load_suite(args.config)
    except OSError as exc:
        if wants_json(args):
            emit_envelope(error_envelope("eval check", exc))
        else:
            print(f"error: could not read {args.config}: {exc}", file=sys.stderr)
        return 2

    plan = validate_suite(suite)
    unpinned: list[str] = []
    if getattr(args, "require_pinned", False):
        from castia.evaluation.evaluators import find_unpinned_evaluators

        unpinned = find_unpinned_evaluators(suite)
    extra_problems = [
        f"evaluator '{name}' has no version -- pin it "
        "(python -m castia eval evaluator import ... --pin-version)"
        for name in unpinned
    ]
    azd_config = None
    if eval_config_path(args.path).exists():
        azd_config = str(eval_config_path(args.path))
        try:
            definitions = load_eval_definitions(args.path)
            extra_problems += check_drift(suite, definitions, path=args.path)
            if getattr(args, "require_pinned", False):
                for spec in definitions.get("evals") or []:
                    eval_name = str(spec.get("name")) if isinstance(spec, dict) else ""
                    extra_problems += [
                        f"{azd_config}: eval '{eval_name}' evaluator '{name}' has no version"
                        for name in find_unpinned_eval_evaluators(definitions, eval_name)
                    ]
        except EvalPortError as exc:
            extra_problems.append(str(exc))
    if wants_json(args):
        emit_envelope(_suite_plan_envelope(plan, extra_problems=extra_problems,
                                           azd_config=azd_config))
    else:
        if azd_config:
            print(f"azd config    : {azd_config}")
        _print_suite_plan(plan, extra_problems)
    return 0 if plan.ok and not extra_problems else 1

def _suite_plan_envelope(
    plan, extra_problems: list[str] | None = None, *, azd_config: str | None = None,
) -> dict:
    problems = list(plan.problems) + list(extra_problems or [])
    return command_envelope(
        "eval check",
        status="ok" if not problems else "problems",
        result={
            "config": str(plan.suite.path),
            "azd_config": azd_config,
            "evaluators": [e.name or e.local_uri for e in plan.suite.evaluators],
            "rubrics": {label: [d.id for d in dims] for label, dims in plan.rubrics.items()},
            "datasets": [d.local_uri or d.dataset_file or d.role for d in plan.suite.datasets],
        },
        diagnostics=[diagnostic("info", note) for note in plan.notes]
        + [diagnostic("error", problem) for problem in problems],
        next_actions=[] if not problems else ["fix the problems, then rerun python -m castia eval check"],
    )

# Live azd effects per eval subcommand: (billable, mutating).
_AZD_EFFECTS = {
    "eval generate": (True, True),
    "eval update": (False, True),
    "eval run": (True, True),
}

def _run_azd_argv(
    argv: list[str], *, dry_run: bool, command: str = "eval run", json_output: bool = False,
) -> int:
    from castia.evaluation.suite import run_azd

    if not json_output:
        print("command       : " + " ".join(argv))
    if dry_run:
        if json_output:
            emit_envelope(command_envelope(
                command, status="dry_run", result={"argv": argv},
                next_actions=["rerun without --dry-run to submit (live azd call)"],
            ))
        else:
            print("result        : dry-run (nothing submitted)")
        return 0
    billable, mutating = _AZD_EFFECTS.get(command, (False, False))
    effects = {"network": True, "billable": billable, "mutating": mutating}
    try:
        # In JSON mode azd's own text goes to stderr so stdout stays one envelope.
        code = run_azd(argv, stdout=sys.stderr if json_output else None)
    except FileNotFoundError as exc:
        if json_output:
            emit_envelope(error_envelope(command, exc, code="azd_not_found"))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    if json_output:
        emit_envelope(command_envelope(
            command,
            status="succeeded" if code == 0 else "failed",
            result={"argv": argv, "exit_code": code},
            diagnostics=[] if code == 0 else [
                diagnostic("error", f"azd exited with code {code}; see stderr", "azd_failed")
            ],
            effects=effects,
        ))
    return code


def _eval_fail(args: argparse.Namespace, command: str, exc: BaseException | str,
               *, code: str | None = None) -> int:
    """Report a usage/config error: one error envelope in JSON mode, else stderr."""

    if wants_json(args):
        emit_envelope(error_envelope(command, exc, code=code))
    else:
        print(f"error: {exc}", file=sys.stderr)
    return 2


def _run_azd_plan(
    plans: list[list[str]], *, dry_run: bool, command: str = "eval run",
    json_output: bool = False,
) -> int:
    """Run commands in order, stopping at the first failure.

    In JSON mode the whole plan reports as one envelope and azd's own output
    goes to stderr, so stdout stays a single document.
    """

    if not json_output:
        for done, argv in enumerate(plans):
            rc = _run_azd_argv(argv, dry_run=dry_run, command=command)
            if rc != 0:
                if done:
                    print(f"error: stopped after {done} of {len(plans)} commands",
                          file=sys.stderr)
                return rc
        return 0

    from castia.evaluation.suite import run_azd

    if dry_run:
        emit_envelope(command_envelope(
            command, status="dry_run", result={"argv": plans},
            next_actions=["rerun without --dry-run to submit (live azd call)"],
        ))
        return 0
    billable, mutating = _AZD_EFFECTS.get(command, (False, False))
    effects = {"network": True, "billable": billable, "mutating": mutating}
    results: list[dict] = []
    code = 0
    for argv in plans:
        try:
            code = run_azd(argv, stdout=sys.stderr)
        except FileNotFoundError as exc:
            emit_envelope(error_envelope(command, exc, code="azd_not_found"))
            return 2
        results.append({"argv": argv, "exit_code": code})
        if code != 0:
            break
    emit_envelope(command_envelope(
        command,
        status="succeeded" if code == 0 else "failed",
        result={"commands": results, "exit_code": code},
        diagnostics=[] if code == 0 else [
            diagnostic("error", f"azd exited with code {code} after {len(results)} of "
                       f"{len(plans)} commands; see stderr", "azd_failed")
        ],
        effects=effects,
    ))
    return code


def _backend(args: argparse.Namespace, command: str) -> str | None:
    from castia.evaluation.azd_eval import EvalPortError, resolve_backend

    try:
        backend = resolve_backend(args.backend)
    except (EvalPortError, FileNotFoundError) as exc:
        _eval_fail(args, command, exc)
        return None
    out = sys.stderr if wants_json(args) else sys.stdout
    print(f"backend       : {backend}", file=out)
    if backend == "agent":
        print("note          : legacy 'azd ai agent eval' (removed in azure.ai.agents "
              "1.0.0-beta.22); install azure.ai.evaluations to move to 'azd ai eval'", file=out)
    return backend


def _legacy_only(args: argparse.Namespace, command: str, **flags: object) -> int:
    used = [name for name, value in flags.items() if value]
    if not used:
        return 0
    return _eval_fail(
        args, command,
        "the legacy 'azd ai agent eval' backend is required for " + ", ".join(used)
        + "; see 'python -m castia eval generate --help'",
        code="unsupported_option",
    )


def _evaluations_only(args: argparse.Namespace, command: str, **flags: object) -> int:
    used = [name for name, value in flags.items() if value]
    if not used:
        return 0
    return _eval_fail(
        args, command,
        "the azure.ai.evaluations backend is required for " + ", ".join(used)
        + " ('azd extension install azure.ai.evaluations')",
        code="unsupported_option",
    )


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

    backend = _backend(args, "eval generate")
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
        rc = _evaluations_only(args, "eval generate", **{
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
        return _run_azd_argv(
            argv, dry_run=args.dry_run, command="eval generate", json_output=wants_json(args),
        )

    rc = _legacy_only(
        args, "eval generate",
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
        return _eval_fail(args, "eval generate", exc)
    return _run_azd_argv(
        argv, dry_run=args.dry_run, command="eval generate", json_output=wants_json(args),
    )


def _cmd_eval_update(args: argparse.Namespace) -> int:
    from castia.evaluation.azd_eval import (
        EvalPortError,
        build_dataset_update_plan,
        load_eval_definitions,
    )
    from castia.evaluation.suite import build_update_argv

    backend = _backend(args, "eval update")
    if backend is None:
        return 2
    if backend == "agent":
        rc = _evaluations_only(args, "eval update",
                               **{"--project-endpoint": args.project_endpoint})
        if rc:
            return rc
        argv = build_update_argv(
            config=args.config,
            evaluator_only=args.evaluator_only,
            dataset_only=args.dataset_only,
            no_prompt=args.no_prompt,
        )
        return _run_azd_argv(
            argv, dry_run=args.dry_run, command="eval update", json_output=wants_json(args),
        )

    if args.evaluator_only:
        return _eval_fail(
            args, "eval update",
            "publish an edited rubric with 'azd ai eval evaluator update <name> "
            "--from-file <rubric.json>'; castia updates datasets only on this backend",
            code="unsupported_option",
        )
    try:
        plans = build_dataset_update_plan(
            load_eval_definitions(args.path),
            path=args.path,
            project_endpoint=args.project_endpoint,
            no_prompt=args.no_prompt,
        )
    except (OSError, EvalPortError) as exc:
        return _eval_fail(args, "eval update", exc)
    if not args.dataset_only:
        print("note          : rubric evaluators are not re-published; use "
              "'azd ai eval evaluator update <name> --from-file <rubric.json>'",
              file=sys.stderr if wants_json(args) else sys.stdout)
    return _run_azd_plan(plans, dry_run=args.dry_run, command="eval update",
                         json_output=wants_json(args))


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
    from castia.evaluation.azd_eval import (
        EvalPortError,
        build_create_argv,
        build_run_start_argv,
        eval_config_path,
        find_unpinned_eval_evaluators,
        load_eval_definitions,
        select_eval,
    )
    from castia.evaluation.suite import build_run_argv

    if args.runs is not None:
        if wants_json(args):
            emit_envelope(error_envelope(
                "eval run", ValueError("--json is not supported with --runs yet"),
                code="unsupported_option",
            ))
            return 2
        return _cmd_eval_run_repeated(args)
    name, error = _resolve_run_name(args)
    if error:
        return _eval_fail(args, "eval run", error)

    backend = _backend(args, "eval run")
    if backend is None:
        return 2
    if backend == "agent":
        rc = _evaluations_only(args, "eval run", **{
            "--eval": args.eval_name,
            "--deploy": args.deploy,
            "--fail-on": args.fail_on,
            "--max-samples": args.max_samples,
            "--project-endpoint": args.project_endpoint,
        })
        if rc:
            return rc
        error = _unpinned_error(args.config, allow=args.allow_unpinned)
        if error:
            return _eval_fail(args, "eval run", error)
        argv = build_run_argv(
            config=args.config,
            name=name,
            no_wait=args.no_wait,
            no_prompt=args.no_prompt,
        )
        return _run_azd_argv(
            argv, dry_run=args.dry_run, command="eval run", json_output=wants_json(args),
        )

    try:
        definitions = load_eval_definitions(args.path)
        eval_name = select_eval(definitions, args.eval_name)
        unpinned = find_unpinned_eval_evaluators(definitions, eval_name)
        error = _unpinned_message(unpinned, str(eval_config_path(args.path)),
                                  allow=args.allow_unpinned,
                                  hint="add 'version:' to each custom evaluator reference")
        if error:
            return _eval_fail(args, "eval run", error)
        plans = []
        if args.deploy:
            plans.append(build_create_argv(eval_name, path=args.path,
                                           project_endpoint=args.project_endpoint,
                                           no_prompt=args.no_prompt))
        plans.append(build_run_start_argv(
            eval_name=eval_name,
            path=args.path,
            name=name,
            no_wait=args.no_wait,
            fail_on=args.fail_on,
            max_samples=args.max_samples,
            project_endpoint=args.project_endpoint,
            no_prompt=args.no_prompt,
        ))
    except FileNotFoundError:
        return _eval_fail(args, "eval run",
                          f"no azure.eval.yaml under {args.path!r}; run 'python -m castia eval init'")
    except EvalPortError as exc:
        return _eval_fail(args, "eval run", exc)
    return _run_azd_plan(plans, dry_run=args.dry_run, command="eval run",
                         json_output=wants_json(args))


def _cmd_eval_rubric_compile(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from castia.evaluation.suite import compile_gates, read_rubric, rubric_to_wire

    try:
        dims = read_rubric(args.source)
        compiled = compile_gates(dims, pass_threshold=args.pass_threshold)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not compiled:
        print(f"error: {args.source} has no dimensions", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(rubric_to_wire(compiled), indent=2) + "\n",
                              encoding="utf-8")
    gates = {d.id for d in dims if d.gate}
    for d in compiled:
        tag = "  (gate)" if d.id in gates else ""
        print(f"dimension     : {d.id}  weight={d.weight:g}{tag}")
    print(f"pass threshold: {args.pass_threshold:g}")
    print(f"wrote         : {args.out}")
    return 0

def _cmd_eval_template(args: argparse.Namespace) -> int:
    from castia.evaluation.templates import write_gold_aware_template

    try:
        paths = write_gold_aware_template(
            args.out, pass_threshold=args.pass_threshold, overwrite=args.force
        )
    except (FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for p in paths:
        print(f"wrote         : {p}")
    print("next          : reference gold_rubric.json as a rubric local_uri in eval.yaml;")
    print("                keep gold_rubric.source.json as the authored brief")
    print("warning       : the Agent Optimizer strips ground_truth from rubric evaluator")
    print("                registrations; for optimizer runs, register the gold check as a")
    print("                prompt evaluator (see README: gold-aware evaluators)")
    return 0


def _cmd_eval_run_repeated(args: argparse.Namespace) -> int:
    from pathlib import Path

    from castia.evaluation.foundry import run_name
    from castia.evaluation.harness import (
        HarnessConfig,
        format_report,
        run_harness,
        summarize,
    )

    missing = [flag for flag, value in (("--rung", args.rung), ("--eval-id", args.eval_id),
                                        ("--template-run", args.template_run)) if not value]
    if args.name:
        print("error: --runs names each run '<rung> · n<rows> · r<i>'; use --rung, not --name",
              file=sys.stderr)
        return 2
    if missing:
        print(f"error: --runs needs {', '.join(missing)}", file=sys.stderr)
        return 2
    error = _unpinned_error(args.config, allow=args.allow_unpinned)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    tool_gate, error = _tool_gate(args)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    backend = None
    rows = args.rows or _dataset_rows(args.config)
    if rows is None and not args.dry_run:
        backend = _harness_backend(args)
        if backend is None:
            return 2
        rows = backend.default_rows()
    if rows is None:
        print("error: could not determine the row count; pass --rows", file=sys.stderr)
        return 2
    try:
        config = HarnessConfig(
            rung=args.rung, rows=rows, runs=args.runs, max_in_flight=args.max_in_flight,
            min_scored=args.min_scored,
            max_errors=None if args.max_errors is not None and args.max_errors < 0 else args.max_errors,
            poll_interval=args.poll_interval, timeout=args.timeout,
        )
        names = [run_name(args.rung, rows, repeat=i) for i in range(1, args.runs + 1)]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    safe_rung = "".join(c if c.isalnum() or c in "-_." else "_" for c in args.rung)
    state_path = Path(args.state or Path(".castia") / "eval_runs" / f"{safe_rung}-n{rows}.json")

    print(f"eval          : {args.eval_id} (template run {args.template_run})")
    print(f"runs          : {args.runs} x '{names[0].rsplit(' · ', 1)[0]}', "
          f"max {config.max_in_flight} in flight")
    print(f"accept        : status completed, scored >= {config.min_scored:.0%}"
          + (f", errors <= {config.max_errors}" if config.max_errors is not None else "")
          + (f", tool error rate <= {args.max_tool_error_rate:.0%}"
             if tool_gate is not None else ""))
    print(f"state         : {state_path}")
    if args.dry_run:
        print("result        : dry-run (nothing submitted)")
        return 0

    backend = backend or _harness_backend(args)
    if backend is None:
        return 2
    try:
        state = run_harness(backend, config, state_path, log=print, tool_gate=tool_gate)
    except (TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in format_report(state, requested=args.runs):
        print(line)
    return 0 if summarize(state)["accepted"] == args.runs else 1


def _tool_gate(args: argparse.Namespace):
    """Return ``(gate, error)`` for --max-tool-error-rate."""
    if args.max_tool_error_rate is None:
        return None, None
    if not 0.0 <= args.max_tool_error_rate <= 1.0:
        return None, "--max-tool-error-rate must be between 0 and 1"
    if args.min_tool_calls < 0:
        return None, "--min-tool-calls must be >= 0"
    try:
        from castia.evaluation.tool_errors import tool_error_gate
        from castia.observe.genai import (
            server_tool_calls,  # noqa: F401 - used by the backend
        )
    except ImportError:
        return None, ("--max-tool-error-rate needs castia.evaluation.tool_errors and "
                      "castia.observe.genai (castia tool telemetry), which this install lacks")
    rate, min_calls = args.max_tool_error_rate, args.min_tool_calls

    def gate(records):
        return tool_error_gate(records, max_error_rate=rate, min_calls=min_calls)

    return gate, None


def _harness_backend(args: argparse.Namespace):
    from castia.evaluation.harness import FoundryRestBackend

    client = _eval_client(args)
    if client is None:
        return None
    return FoundryRestBackend(client, args.eval_id, args.template_run)


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
    return _unpinned_message(
        unpinned, config, allow=allow,
        hint="python -m castia eval evaluator import BUNDLE --pin-version",
    )


def _unpinned_message(unpinned: list[str], config: str, *, allow: bool, hint: str) -> str | None:
    if not unpinned:
        return None
    names = ", ".join(unpinned)
    if allow:
        print(f"warning: unpinned evaluators ({names}); scores are not reproducible",
              file=sys.stderr)
        return None
    return f"unpinned evaluators: {names}. Pin a version in {config} ({hint}) or pass --allow-unpinned"


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

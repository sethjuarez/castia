"""Thin argparse adapters for offline project creation and readiness."""

from __future__ import annotations

import argparse
import json
import sys


def register_dev_command(subparsers: argparse._SubParsersAction) -> None:
    """Register ``dev`` on the root subparser action."""
    dev = subparsers.add_parser(
        "dev",
        help="load .env, validate local agent assets, and start the dev server",
    )
    dev.add_argument("project", nargs="?", default=".", help="project directory")
    dev.add_argument("--app", default="main:app", help="local module:Agent target")
    dev.add_argument("--env-file", default=".env", help="local env file to load")
    dev.add_argument("--host", default="127.0.0.1", help="status host to print")
    dev.add_argument("--port", type=int, default=None, help="local server port")
    dev.add_argument(
        "--check-only",
        action="store_true",
        help="run dry checks and do not start the entrypoint",
    )
    dev.add_argument(
        "--require-prompty",
        action="store_true",
        help="fail if optional Prompty imports are unavailable",
    )
    dev.add_argument(
        "--no-optimize-check",
        action="store_true",
        help="skip Agent tool drift comparison against .agent_configs/baseline",
    )
    dev.add_argument(
        "--json",
        action="store_true",
        help="print the status report as JSON",
    )
    dev.set_defaults(func=run_dev)


def register_doctor_command(subparsers: argparse._SubParsersAction) -> None:
    """Register ``doctor`` (offline identity checks) and ``doctor quota``."""
    doctor = subparsers.add_parser(
        "doctor",
        help="check telemetry identity and model labelling; `doctor quota` reads regional quota",
    )
    doctor.add_argument("--project", default=".", help="project directory")
    doctor.add_argument("--env-file", default=".env", help="local env file layered under the process env")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")
    doctor.set_defaults(func=run_doctor)
    commands = doctor.add_subparsers(dest="doctor_command")
    quota = commands.add_parser(
        "quota",
        help="read-only: list serving, fine-tune and developer-tier quota meters in a region",
    )
    quota.add_argument("--region", required=True, help="Azure location, e.g. swedencentral")
    quota.add_argument("--model", help="model name, e.g. gpt-4.1 (default: every meter)")
    quota.add_argument(
        "--subscription",
        help="subscription UUID (default: AZURE_SUBSCRIPTION_ID or the project ARM ID's subscription)",
    )
    quota.add_argument("--json", action="store_true", help="print the report as JSON")
    quota.set_defaults(func=run_doctor_quota)


def run_doctor(args: argparse.Namespace) -> int:
    """Return 0 when no check fails (warnings still print), 2 on usage/I/O errors."""
    from castia.building.doctor import identity_checks

    try:
        report = identity_checks(args.project, env_file=args.env_file)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"root         : {report.root}")
        for item in report.diagnostics:
            print(f"{item.status:7} {item.check}: {item.message}")
        print(f"result       : {'ok' if report.ok else 'PROBLEMS FOUND'} ({report.warnings} warning(s))")
    return 0 if report.ok else 1


def run_doctor_quota(args: argparse.Namespace) -> int:
    """Read-only ARM quota listing; 0 on success, 2 on configuration/auth/transport errors."""
    from castia.building.dev import merged_env
    from castia.building.quota import (
        QuotaError,
        format_report,
        list_quota,
        subscription_from_env,
    )

    subscription = args.subscription or subscription_from_env(
        merged_env(args.project, env_file=args.env_file)
    )
    if not subscription:
        print(
            "error: pass --subscription or set AZURE_SUBSCRIPTION_ID / AZURE_AI_PROJECT_ID",
            file=sys.stderr,
        )
        return 2
    try:
        report = list_quota(subscription, args.region, model=args.model)
    except (QuotaError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report))
    return 0


def register_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register ``build scaffold/check/test`` on the root subparser action."""
    build = subparsers.add_parser("build", help="create and check an agent project offline")
    commands = build.add_subparsers(dest="build_command", required=True)
    create = commands.add_parser("scaffold", aliases=["init"], help="create a new agent project")
    create.add_argument("target", help="new or empty project directory")
    create.add_argument("--name", default="my-agent", help="agent/service name")
    create.add_argument("--model", default="gpt-4o", help="existing model deployment name")
    create.set_defaults(func=run, build_action="init")
    check = commands.add_parser(
        "check", aliases=["preflight"], help="check local registration and readiness"
    )
    check.add_argument("project", nargs="?", default=".", help="project directory")
    check.add_argument("--app", default="main:app", help="local module:Agent target")
    check.add_argument(
        "--deployment", action="store_true",
        help="require code-deploy region, project ARM ID and subscription in process settings",
    )
    check.set_defaults(func=run, build_action="check")
    test = commands.add_parser("test", help="run project tests in an offline subprocess")
    test.add_argument("project", nargs="?", default=".", help="project directory")
    test.add_argument("--timeout", type=float, default=60, help="local test timeout in seconds")
    test.set_defaults(func=run, build_action="test")


def run(args: argparse.Namespace) -> int:
    """Print JSON; return 0 for success, 1 for readiness failure, 2 for usage/I/O."""
    from castia.building.preflight import preflight
    from castia.building.scaffold import scaffold_project
    from castia.building.testing import run_project_tests

    try:
        if args.build_action == "init":
            report = scaffold_project(args.target, name=args.name, model=args.model)
            print(json.dumps(report.to_dict(), indent=2))
            return 0
        if args.build_action == "test":
            report = run_project_tests(args.project, timeout=args.timeout)
            print(json.dumps(report.to_dict(), indent=2))
            return 0 if report.ok else 1
        report = preflight(args.project, app_target=args.app, deployment=args.deployment)
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.ok else 1
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        # Exception text can originate in imported registration/configuration.
        print(
            json.dumps({
                "ok": False,
                "error": type(exc).__name__,
                "message": "Build operation failed; check the target, arguments and local files.",
            }),
            file=sys.stderr,
        )
        return 2


def run_dev(args: argparse.Namespace) -> int:
    """Run dry dev checks, then optionally start the local entrypoint."""
    from castia.building.dev import dev_preflight, print_text_status, run_dev_server

    try:
        report = dev_preflight(
            args.project,
            app_target=args.app,
            env_file=args.env_file,
            host=args.host,
            port=args.port,
            require_prompty=args.require_prompty,
            optimize_check=not args.no_optimize_check,
        )
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(
            json.dumps({
                "ok": False,
                "error": type(exc).__name__,
                "message": "Dev preflight failed; check the target, arguments and local files.",
            }),
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_text_status(report)

    if not report.ok or args.check_only:
        return 0 if report.ok else 1
    print("start        : launching local entrypoint (no model request is sent)")
    return run_dev_server(
        args.project,
        app_target=args.app,
        env_file=args.env_file,
        port=args.port,
    )

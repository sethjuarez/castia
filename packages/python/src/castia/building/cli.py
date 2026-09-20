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

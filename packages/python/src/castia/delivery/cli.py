"""Command registration and handlers for delivery."""

from __future__ import annotations

import argparse
import sys

from castia.delivery.manifest import (
    DEFAULT_APP,
    DEFAULT_MANIFEST,
    Plan,
    generate_manifest,
    load_app,
)


def register_commands(sub: argparse._SubParsersAction) -> None:
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

    try:
        plan = generate_manifest(app, args.manifest, check=args.check)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(
            f"error: could not validate manifest {args.manifest!r}: {exc}",
            file=sys.stderr,
        )
        return 2
    _print_plan(plan, check=args.check)

    # --check is a CI gate: non-zero when the manifest would change.
    if args.check and plan.changed:
        return 1
    return 0

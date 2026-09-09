"""``python -m castia`` -- the build-time CLI for the framework.

Currently one subcommand, ``deploy``, which rewrites ``azure.yaml`` so its
declared protocols match the decorators on your ``Agent``. It never runs the
agent; it just imports the entrypoint to read the composed protocol set.

    python -m castia deploy                 # rewrite azure.yaml in place
    python -m castia deploy --check         # report drift, write nothing
    python -m castia deploy --app main:app --manifest azure.yaml
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

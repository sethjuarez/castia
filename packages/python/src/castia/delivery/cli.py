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
from castia.delivery.microsoft365 import build_publish_plan, write_redacted_artifact


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

    publish = sub.add_parser(
        "publish",
        help="prepare Microsoft 365 publishing payloads for hosted agents",
    )
    publish_sub = publish.add_subparsers(dest="publish_command", required=True)
    m365 = publish_sub.add_parser(
        "microsoft365",
        help="dry-run Teams/Copilot or Autopilot publish payloads",
    )
    m365.add_argument(
        "--app",
        default=DEFAULT_APP,
        help=f"entrypoint as module:attr (default: {DEFAULT_APP})",
    )
    m365.add_argument("--mode", choices=["teams", "autopilot"], required=True)
    action = m365.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and write a redacted publish payload artifact",
    )
    action.add_argument(
        "--submit",
        action="store_true",
        help="reserved for the live Foundry publish request; not implemented yet",
    )
    action.add_argument(
        "--download-package",
        action="store_true",
        help="reserved for microsoft365/zip package download; not implemented yet",
    )
    m365.add_argument("--project-endpoint", required=True)
    m365.add_argument("--agent-name", required=True)
    m365.add_argument("--agent-display-name", required=True)
    m365.add_argument("--app-version", default="1.0.0")
    m365.add_argument("--publish-scope", choices=["Shared", "Tenant"], default="Shared")
    m365.add_argument("--short-description", required=True)
    m365.add_argument("--full-description", required=True)
    m365.add_argument("--developer-name", required=True)
    m365.add_argument("--developer-website-url", required=True)
    m365.add_argument("--privacy-url")
    m365.add_argument("--terms-of-use-url")
    m365.add_argument("--color-icon", required=True)
    m365.add_argument("--outline-icon", required=True)
    m365.add_argument(
        "--can-respond-without-mention",
        action="store_true",
        help="set canRespondWithoutMention=true in the publish payload",
    )
    m365.add_argument(
        "--blueprint-client-id",
        help="Autopilot agent.blueprint.client_id from azd ai agent show",
    )
    m365.add_argument(
        "--bot-service-arm-id",
        help="Teams route Bot Service ARM ID required by the stable REST API",
    )
    m365.add_argument(
        "--output",
        default="castia-microsoft365-publish.dry-run.json",
        help="path for the redacted dry-run artifact",
    )
    m365.set_defaults(func=_cmd_publish_microsoft365)


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


def _cmd_publish_microsoft365(args: argparse.Namespace) -> int:
    if args.submit:
        print(
            "error: --submit is not implemented yet; run --dry-run to validate "
            "and review the redacted payload first",
            file=sys.stderr,
        )
        return 2
    if args.download_package:
        print(
            "error: --download-package is not implemented yet; package download "
            "will use microsoft365/zip in a later slice",
            file=sys.stderr,
        )
        return 2

    try:
        app = load_app(args.app)
        plan = build_publish_plan(
            app=app,
            mode=args.mode,
            project_endpoint=args.project_endpoint,
            agent_name=args.agent_name,
            agent_display_name=args.agent_display_name,
            app_version=args.app_version,
            publish_scope=args.publish_scope,
            color_icon=args.color_icon,
            outline_icon=args.outline_icon,
            short_description=args.short_description,
            full_description=args.full_description,
            developer_name=args.developer_name,
            developer_website_url=args.developer_website_url,
            privacy_url=args.privacy_url,
            terms_of_use_url=args.terms_of_use_url,
            can_respond_without_mention=args.can_respond_without_mention,
            blueprint_client_id=args.blueprint_client_id,
            bot_service_arm_id=args.bot_service_arm_id,
        )
        artifact = write_redacted_artifact(plan, args.output)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"error: could not prepare Microsoft 365 publish payload: {exc}", file=sys.stderr)
        return 2

    print(f"mode          : {plan.mode}")
    print(f"method        : {plan.method}")
    print(f"url           : {plan.url}")
    print(f"artifact      : {artifact}")
    for name, summary in plan.icon_summaries.items():
        print(
            f"{name:<14}: {summary['width']}x{summary['height']} "
            f"sha256={summary['sha256']}"
        )
    print("result        : dry-run artifact written; no Foundry request was submitted")
    return 0

"""CLI for explicit inspection of existing fine-tuning work."""

from __future__ import annotations

import argparse
import json
import sys


def register_commands(subparsers: argparse._SubParsersAction) -> None:
    for name in ("list", "status", "events", "checkpoints", "results", "cancel", "handoff"):
        parser = subparsers.add_parser(name, help=f"{name} existing fine-tuning jobs")
        parser.add_argument("--project-endpoint")
        parser.add_argument("--request-timeout", type=float, default=30)
        if name != "list":
            parser.add_argument("job_id", nargs="?")
            parser.add_argument("--reference", help="saved endpoint/job reference JSON")
        if name in {"list", "events", "checkpoints"}:
            parser.add_argument("--limit", type=int, default=20)
            parser.add_argument("--after")
        if name == "status":
            parser.add_argument("--watch", action="store_true")
            parser.add_argument("--timeout", type=float, default=300)
            parser.add_argument("--poll-interval", type=float, default=10)
            parser.add_argument("--save-reference")
        if name == "results":
            parser.add_argument("--file-id", help="download only this job-owned result file")
            parser.add_argument("--out", help="new destination file; never overwritten")
            parser.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024)
        parser.set_defaults(func=run, management_command=name)


def run(args: argparse.Namespace) -> int:
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    from .jobs import FineTuningClient, JobReference

    try:
        job_id = getattr(args, "job_id", None)
        endpoint = args.project_endpoint
        if getattr(args, "reference", None):
            ref = JobReference.load(args.reference)
            if job_id and job_id != ref.job_id:
                raise ValueError("job ID conflicts with the saved reference")
            if endpoint and endpoint.rstrip("/") != ref.project_endpoint.rstrip("/"):
                raise ValueError("endpoint conflicts with the saved reference")
            job_id, endpoint = ref.job_id, ref.project_endpoint
        command = args.management_command
        if command != "list" and not job_id:
            raise ValueError("job_id or --reference is required")
        with FineTuningClient(endpoint, request_timeout=args.request_timeout) as client:
            if command == "list":
                result = [j.model_dump(mode="json") for j in client.list(
                    limit=args.limit, after=args.after,
                )]
            elif command in {"events", "checkpoints"}:
                method = client.events if command == "events" else client.checkpoints
                result = [j.model_dump(mode="json") for j in method(
                    job_id, limit=args.limit, after=args.after,
                )]
            elif command == "handoff":
                result = client.deployment_handoff(job_id)
            elif command == "results":
                if bool(args.file_id) != bool(args.out):
                    raise ValueError("--file-id and --out must be supplied together")
                if args.file_id:
                    result = {"path": str(client.download_result(
                        job_id, args.file_id, args.out, max_bytes=args.max_bytes,
                    ))}
                else:
                    result = {"result_files": client.result_files(job_id)}
            else:
                if command == "cancel":
                    job = client.cancel(job_id)
                elif args.watch:
                    job = client.watch(
                        job_id, timeout=args.timeout, poll_interval=args.poll_interval,
                    )
                else:
                    job = client.status(job_id)
                result = job.model_dump(mode="json")
                if getattr(args, "save_reference", None):
                    if not client.endpoint:
                        raise ValueError("project endpoint is required to save a reference")
                    JobReference(client.endpoint, job_id).save(args.save_reference)
        print(json.dumps(result, indent=2, allow_nan=False))
        if command == "status" and result.get("status") in {"failed", "cancelled", "canceled"}:
            return 1
        return 0
    except (ValueError, TypeError, OSError, RuntimeError, AzureError, OpenAIError) as exc:
        print(f"error: fine-tuning {args.management_command}: {exc}", file=sys.stderr)
        return 2

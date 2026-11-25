"""CLI handlers for ``finetune grade-local``, ``probe``, and ``probe-check``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register_probe_commands(ftsub: argparse._SubParsersAction) -> None:
    grade = ftsub.add_parser(
        "grade-local",
        help="run an RFT grader in-process on your rows (offline; trusts grader code)",
    )
    grade.add_argument("--grader", required=True, help="RFT grader JSON")
    grade.add_argument("--dataset", required=True, help="rows JSONL (the item.* namespace)")
    _add_output_args(grade)
    grade.add_argument("--out", default=None, help="write the JSON report here")
    grade.set_defaults(func=_cmd_grade_local)

    probe = ftsub.add_parser(
        "probe",
        help="plan (default) or submit tiny always-true/always-false RFT probe jobs",
    )
    probe.add_argument("--model", required=True, help="base model to fine-tune")
    probe.add_argument("--dataset", required=True, help="training split JSONL")
    probe.add_argument("--validation", required=True, help="validation split JSONL")
    probe.add_argument("--grader", default=None,
                       help="real RFT grader JSON; adds a third probe job")
    _add_output_args(probe, required=False)
    probe.add_argument("--project-endpoint")
    probe.add_argument("--seconds-per-step", dest="seconds_per_step", type=float,
                       default=None)
    probe.add_argument("--hourly-usd", dest="hourly_usd", type=float, default=None)
    probe.add_argument("--tolerance", type=float, default=None,
                       help="max |trainer reward - offline score| for the real grader")
    probe.add_argument("--out", required=True, help="probe record JSON to write")
    probe.add_argument("--approve-spend", dest="approve_spend", action="store_true",
                       help="upload the probe splits and submit the billable probe jobs")
    probe.set_defaults(func=_cmd_probe)

    check = ftsub.add_parser(
        "probe-check",
        help="read probe job rewards and record a pass/fail/pending verdict",
    )
    check.add_argument("--record", required=True, help="probe record JSON from probe")
    check.add_argument("--project-endpoint")
    check.add_argument("--request-timeout", type=float, default=30)
    check.set_defaults(func=_cmd_probe_check)


def _add_output_args(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--outputs", default=None,
                       help="JSONL of model outputs, one per row (string or "
                            "{\"output_text\": ...})")
    group.add_argument("--output-field", dest="output_field", default=None,
                       help="use this row field as the model output (e.g. a reference answer)")


def _outputs(args: argparse.Namespace, rows: list[dict]) -> list[object] | None:
    from castia.finetuning.rft import load_jsonl

    if args.outputs:
        return [
            o.get("output_text", o) if isinstance(o, dict) else o
            for o in load_jsonl(args.outputs)
        ]
    if args.output_field:
        missing = [i for i, row in enumerate(rows) if args.output_field not in row]
        if missing:
            raise ValueError(f"--output-field {args.output_field!r} missing in row(s) {missing[:5]}")
        return [row[args.output_field] for row in rows]
    return None


def _cmd_grade_local(args: argparse.Namespace) -> int:
    from castia.finetuning.probe import grade_rows
    from castia.finetuning.rft import load_grader, load_jsonl

    try:
        grader = load_grader(args.grader)
        rows = load_jsonl(args.dataset)
        report = grade_rows(grader, rows, _outputs(args, rows))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"grader        : {args.grader}  (type: {report['grader_type']!r})")
    print(f"rows          : {report['rows']}  (scored {report['scored']})")
    if report["skipped"]:
        print(f"result        : skipped -- {report['reason']}")
    elif "mean" in report:
        print(f"score         : mean {report['mean']:g}, min {report['min']:g}, "
              f"max {report['max']:g}")
    for error in report["errors"][:5]:
        print(f"error         : row {error['row']}: {error['error']}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"wrote         : {args.out}")
    if report["errors"]:
        return 1
    return 3 if report["skipped"] else 0


def _cmd_probe(args: argparse.Namespace) -> int:
    from castia.finetuning.cli import _validate_splits
    from castia.finetuning.preflight import estimate_training, spend_line
    from castia.finetuning.probe import (
        DEFAULT_TOLERANCE,
        PROBE_ROWS,
        grade_rows,
        probe_graders,
        probe_split,
    )
    from castia.finetuning.rft import load_grader, load_jsonl

    try:
        train = load_jsonl(args.dataset)
        validation = load_jsonl(args.validation)
        real = load_grader(args.grader) if args.grader else None
        outputs = _outputs(args, train)
        train_split = probe_split(train)
        val_split = probe_split(validation)
        offline = None
        if real is not None and outputs is not None:
            if len(outputs) != len(train):
                raise ValueError(f"{len(train)} rows but {len(outputs)} outputs")
            report = grade_rows(real, train_split, probe_split(outputs))
            if report["errors"] or report["skipped"]:
                raise ValueError("offline grading failed; run finetune grade-local first")
            offline = report["mean"]
        estimate = estimate_training(
            PROBE_ROWS, n_epochs=1, seconds_per_step=args.seconds_per_step,
            hourly_usd=args.hourly_usd,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    graders = probe_graders()
    if real is not None:
        graders["real"] = real
    problems = []
    for name, grader in graders.items():
        problems.extend(
            f"{name}: {p}" for p in _validate_splits(
                "rft", train_split, val_split, grader=grader, require_rft_grader=True,
                model=args.model,
            )
        )
    for problem in problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if problems:
        return 1
    if real is not None and offline is None:
        print("warning       : no --outputs/--output-field, so the real grader's "
              "reward cannot be checked for divergence", file=sys.stderr)

    total = round(estimate["cost_usd"] * len(graders), 2)
    record = {
        "model": args.model,
        "rows": PROBE_ROWS,
        "offline_score": offline,
        "tolerance": DEFAULT_TOLERANCE if args.tolerance is None else args.tolerance,
        "estimate": {**estimate, "jobs": len(graders), "total_cost_usd": total},
        "jobs": {name: {"grader": g, "job_id": None, "reward": None}
                 for name, g in graders.items()},
        "verdict": {"status": "planned", "reasons": [], "pending": []},
    }
    print(f"probe jobs    : {', '.join(graders)}  ({PROBE_ROWS} rows, 1 epoch each)")
    print(f"estimate      : per job {spend_line(estimate)}")
    print(f"total spend   : <=${total:.2f} for {len(graders)} jobs (estimate, not a bill)")
    if offline is not None:
        print(f"offline score : {offline:g}")

    if args.approve_spend:
        try:
            _submit_probe(args, record, train_split, val_split)
        except (RuntimeError, OSError, ValueError) as exc:
            _write(args.out, record)
            print(f"error: {exc}", file=sys.stderr)
            return 2
        record["verdict"]["status"] = "submitted"
    _write(args.out, record)
    print(f"wrote         : {args.out}")
    if not args.approve_spend:
        print("result        : plan only -- rerun with --approve-spend to submit "
              f"{len(graders)} billable probe jobs")
        return 0
    print("result        : submitted; run finetune probe-check --record "
          f"{args.out} once the jobs report metrics")
    return 0


def _submit_probe(args, record: dict, train_split: list, val_split: list) -> None:
    import tempfile

    from castia.finetuning.cli import _write_jsonl
    from castia.finetuning.rft import build_rft_job, submit_rft_job, upload_file

    with tempfile.TemporaryDirectory(prefix="castia-probe-") as tmp:
        train_id = upload_file(_write_jsonl(Path(tmp) / "train.jsonl", train_split),
                               endpoint=args.project_endpoint)
        val_id = upload_file(_write_jsonl(Path(tmp) / "val.jsonl", val_split),
                             endpoint=args.project_endpoint)
    for name, job in record["jobs"].items():
        created = submit_rft_job(build_rft_job(
            model=args.model, training_file=train_id, validation_file=val_id,
            grader=job["grader"], hyperparameters={"n_epochs": 1},
            suffix=f"probe-{name.replace('_', '-')}",
        ), endpoint=args.project_endpoint)
        job["job_id"] = getattr(created, "id", created)
        print(f"submitted     : {name} -> {job['job_id']}")


def _write(path: str, record: dict) -> None:
    Path(path).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _cmd_probe_check(args: argparse.Namespace) -> int:
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    from castia.finetuning.jobs import FineTuningClient
    from castia.finetuning.probe import probe_verdict, reward_from_events

    try:
        record = json.loads(Path(args.record).read_text(encoding="utf-8"))
        jobs = record["jobs"]
        if any(not job.get("job_id") for job in jobs.values()):
            raise ValueError("record has unsubmitted jobs; run finetune probe --approve-spend")
        with FineTuningClient(args.project_endpoint,
                              request_timeout=args.request_timeout) as client:
            for job in jobs.values():
                events = [
                    e.model_dump(mode="json") if hasattr(e, "model_dump") else dict(e)
                    for e in client.events(job["job_id"], limit=100)
                ]
                job["reward"] = reward_from_events(events)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError, AzureError,
            OpenAIError) as exc:
        print(f"error: finetune probe-check: {exc}", file=sys.stderr)
        return 2
    verdict = probe_verdict(
        {name: job["reward"] for name, job in jobs.items()},
        offline_score=record.get("offline_score"),
        tolerance=record.get("tolerance", 0.15),
    )
    record["verdict"] = verdict
    _write(args.record, record)
    for name, job in jobs.items():
        print(f"{name:<14}: reward {job['reward']}  ({job['job_id']})")
    for reason in verdict["reasons"]:
        print(f"problem       : {reason}", file=sys.stderr)
    for item in verdict["pending"]:
        print(f"pending       : {item}")
    print(f"result        : {verdict['status']}")
    return {"pass": 0, "fail": 1}.get(verdict["status"], 3)


def probe_gate(path: str | None) -> list[str]:
    """Problems that block a full submit, from a probe record's verdict."""
    if not path:
        return []
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise TypeError("probe record must be a JSON object")
    verdict = record.get("verdict") or {}
    status = verdict.get("status")
    if status == "pass":
        return []
    if status == "fail":
        return [f"probe failed: {r}" for r in verdict.get("reasons") or ["see record"]]
    return [f"probe is {status or 'missing a verdict'}; run finetune probe-check first"]

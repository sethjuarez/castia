"""Command registration and handlers for finetuning."""

from __future__ import annotations

import argparse
import json
import sys


def register_commands(sub: argparse._SubParsersAction) -> None:
    ft = sub.add_parser(
        "finetune",
        help="fine-tuning -- prepare/validate/submit SFT, DPO, or RFT jobs",
    )
    ftsub = ft.add_subparsers(dest="finetune_command", required=True)

    ft_check = ftsub.add_parser(
        "check",
        help="validate an SFT, DPO, or RFT dataset offline, no Azure",
    )
    ft_check.add_argument("--type", choices=("sft", "dpo", "rft"), default="rft")
    ft_check.add_argument("--dataset", required=True,
                          help="training split JSONL")
    ft_check.add_argument("--validation", default=None,
                          help="validation split JSONL (required for RFT)")
    ft_check.add_argument("--grader", default=None,
                          help="RFT grader JSON to cross-check item.* references against")
    ft_check.add_argument("--model", default=None,
                          help="RFT base model; applies its role rules (MAI rejects 'system')")
    ft_check.set_defaults(func=_cmd_finetune_check)

    ft_grader = ftsub.add_parser(
        "grader",
        help="build a score_model grader from an eval rubric (offline bridge)",
    )
    ft_grader.add_argument("--rubric", required=True,
                           help="rubric dimensions file (id/description/weight)")
    ft_grader.add_argument("--model", required=True, help="judge model deployment")
    ft_grader.add_argument("--name", default="rubric_score", help="grader name")
    ft_grader.add_argument("--out", default=None,
                           help="write the grader JSON here (default: stdout)")
    ft_grader.set_defaults(func=_cmd_finetune_grader)

    ft_submit = ftsub.add_parser(
        "submit",
        help="fine_tuning.jobs.create -- start a billable fine-tuning job",
    )
    ft_submit.add_argument("--type", choices=("sft", "dpo", "rft"), default="rft")
    ft_submit.add_argument("--model", required=True, help="base model to fine-tune")
    ft_submit.add_argument("--dataset", required=True, help="training split JSONL")
    ft_submit.add_argument("--validation", default=None, help="validation split JSONL")
    ft_submit.add_argument("--grader", default=None, help="RFT grader JSON")
    ft_submit.add_argument("--project-endpoint")
    ft_submit.add_argument("--n-epochs", dest="n_epochs", type=int, default=None)
    ft_submit.add_argument(
        "--batch-size", dest="batch_size", default=None,
        help="1-256; -1 or auto is dropped from the payload so the service chooses",
    )
    ft_submit.add_argument(
        "--learning-rate-multiplier", dest="learning_rate_multiplier", type=float,
        default=None,
    )
    ft_submit.add_argument("--beta", type=float, default=None,
                           help="DPO beta hyperparameter")
    ft_submit.add_argument("--l2-multiplier", dest="l2_multiplier", type=float,
                           default=None, help="DPO l2_multiplier hyperparameter")
    ft_submit.add_argument("--reasoning-effort", dest="reasoning_effort", default=None,
                           choices=("low", "medium", "high"),
                           help="RFT reasoning_effort hyperparameter")
    ft_submit.add_argument("--eval-interval", dest="eval_interval", type=int, default=None,
                           help="RFT eval_interval hyperparameter")
    ft_submit.add_argument("--suffix", default=None, help="fine-tuned model name suffix")
    ft_submit.add_argument(
        "--rewrite-roles", dest="rewrite_roles", action="store_true",
        help="RFT only: upload copies with roles the base model rejects rewritten "
             "(MAI: system -> developer); source files are not changed",
    )
    ft_submit.add_argument(
        "--seconds-per-step", dest="seconds_per_step", type=float, default=None,
        help="measured wall-clock seconds per training step for the estimate",
    )
    ft_submit.add_argument(
        "--hourly-usd", dest="hourly_usd", type=float, default=None,
        help="training price per hour for the estimate (default: developer tier)",
    )
    ft_submit.add_argument(
        "--approve-train", dest="approve_train", action="store_true",
        help="approve the printed training spend; does not approve evaluation "
             "or promotion",
    )
    ft_submit.add_argument(
        "--probe-record", dest="probe_record", default=None,
        help="RFT: probe record from finetune probe/probe-check; a failed or "
             "unchecked probe blocks the submit",
    )
    ft_submit.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="print the resolved payload and submit nothing")
    ft_submit.set_defaults(func=_cmd_finetune_submit)
    from castia.finetuning.probe_cli import register_probe_commands

    register_probe_commands(ftsub)
    _register_management_commands(ftsub)


def _cmd_finetune_check(args: argparse.Namespace) -> int:
    from castia.finetuning.rft import load_grader, load_jsonl

    try:
        train = load_jsonl(args.dataset)
        validation = load_jsonl(args.validation) if args.validation else None
        grader = load_grader(args.grader) if args.grader else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"training      : {args.dataset}  ({len(train)} examples)")
    if validation is not None:
        print(f"validation    : {args.validation}  ({len(validation)} examples)")
    if args.grader:
        print(f"grader        : {args.grader}  (type: {grader.get('type')!r})")
    print(f"type          : {args.type}")
    if args.model:
        print(f"model         : {args.model}")

    problems = _validate_splits(
        args.type, train, validation, grader=grader, require_rft_grader=False,
        model=args.model,
    )

    for problem in problems:
        print(f"problem       : {problem}")
    print("result        : " + ("ok" if not problems else "PROBLEMS FOUND"))
    return 0 if not problems else 1

def _cmd_finetune_grader(args: argparse.Namespace) -> int:
    from castia.evaluation.suite import read_rubric
    from castia.finetuning.rft import rubric_to_score_model, validate_grader

    try:
        dims = read_rubric(args.rubric)
    except (OSError, ValueError) as exc:
        print(f"error: could not read rubric {args.rubric}: {exc}", file=sys.stderr)
        return 2
    if not dims:
        print(f"error: rubric {args.rubric} has no dimensions", file=sys.stderr)
        return 2

    try:
        grader = rubric_to_score_model(dims, model=args.model, name=args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    problems = validate_grader(grader)
    for problem in problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if problems:
        return 1

    import json as _json

    rendered = _json.dumps(grader, indent=2)
    if args.out:
        from pathlib import Path as _Path

        _Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        print(f"rubric        : {args.rubric}  ({len(dims)} dims)")
        print(f"wrote         : {args.out}  (score_model grader)")
    else:
        print(rendered)
    return 0

def _cmd_finetune_submit(args: argparse.Namespace) -> int:
    from castia.finetuning.rft import load_grader, load_jsonl

    try:
        grader = load_grader(args.grader) if args.grader else None
        train = load_jsonl(args.dataset)
        validation = load_jsonl(args.validation) if args.validation else None
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rewritten = 0
    if args.rewrite_roles:
        if args.type != "rft":
            print("error: --rewrite-roles is only valid with --type rft", file=sys.stderr)
            return 2
        from castia.finetuning.rft import rewrite_rft_roles

        train, train_count = rewrite_rft_roles(train, model=args.model)
        validation_count = 0
        if validation is not None:
            validation, validation_count = rewrite_rft_roles(validation, model=args.model)
        rewritten = train_count + validation_count

    problems = _validate_splits(
        args.type, train, validation, grader=grader, require_rft_grader=True,
        model=args.model,
    )
    for problem in problems:
        print(f"problem       : {problem}", file=sys.stderr)
    if problems:
        print("result        : PROBLEMS FOUND (nothing submitted)", file=sys.stderr)
        return 1

    try:
        hyperparameters = _hyperparameters(args)
        estimate = _estimate(args, len(train), hyperparameters)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if estimate is not None:
        from castia.finetuning.preflight import spend_line

        print(f"estimate      : {spend_line(estimate)}")

    if args.probe_record:
        from castia.finetuning.probe_cli import probe_gate

        try:
            gate = probe_gate(args.probe_record)
        except (OSError, TypeError, ValueError) as exc:
            print(f"error: --probe-record: {exc}", file=sys.stderr)
            return 2
        for problem in gate:
            print(f"problem       : {problem}", file=sys.stderr)
        if gate:
            print("result        : BLOCKED BY PROBE (nothing submitted)", file=sys.stderr)
            return 1
        print(f"probe         : pass ({args.probe_record})")
    elif args.type == "rft":
        print("warning       : no --probe-record; a tiny finetune probe proves the "
              "reward plumbing before a full run", file=sys.stderr)

    if args.dry_run:
        # Dry-run stays offline: build the payload from file *paths* (uploads and
        # the real file ids only happen on a live submit) so nothing is stored.
        import json as _json

        try:
            job = _build_job(
                args.type,
                model=args.model,
                training_file=args.dataset,
                validation_file=args.validation,
                grader=grader,
                hyperparameters=hyperparameters,
                suffix=args.suffix,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"type          : {args.type}")
        print(f"model         : {args.model}")
        print(f"training      : {args.dataset}  ({len(train)} examples)")
        if validation is not None:
            print(f"validation    : {args.validation}  ({len(validation)} examples)")
        if args.rewrite_roles:
            print(f"rewrote roles : {rewritten} message(s) in the upload copies")
        print("payload       :")
        print(_json.dumps(job, indent=2))
        print("result        : dry-run (nothing uploaded or submitted)")
        return 0

    if not args.approve_train:
        try:
            _build_job(
                args.type,
                model=args.model,
                training_file=args.dataset,
                validation_file=args.validation,
                grader=grader,
                hyperparameters=hyperparameters,
                suffix=args.suffix,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(
            "result        : not submitted -- review the plan, then rerun with "
            "--approve-train (training only; evaluation and promotion are "
            "separate approvals)",
            file=sys.stderr,
        )
        return 2

    import tempfile
    from pathlib import Path

    from castia.finetuning.rft import upload_file

    try:
        _build_job(
            args.type,
            model=args.model,
            training_file=args.dataset,
            validation_file=args.validation,
            grader=grader,
            hyperparameters=hyperparameters,
            suffix=args.suffix,
        )
        with tempfile.TemporaryDirectory(prefix="castia-rft-") as tmp:
            train_path, val_path = args.dataset, args.validation
            if rewritten:
                train_path = _write_jsonl(Path(tmp) / "training.jsonl", train)
                if validation is not None:
                    val_path = _write_jsonl(Path(tmp) / "validation.jsonl", validation)
                print(f"rewrote roles : {rewritten} message(s) in the upload copies")
            train_id = upload_file(train_path, endpoint=args.project_endpoint)
            val_id = (
                upload_file(val_path, endpoint=args.project_endpoint)
                if val_path else None
            )
        job = _build_job(
            args.type,
            model=args.model,
            training_file=train_id,
            validation_file=val_id,
            grader=grader,
            hyperparameters=hyperparameters,
            suffix=args.suffix,
        )
        created = _submit_job(args.type, job, endpoint=args.project_endpoint)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"job           : {getattr(created, 'id', created)}")
    print(f"result        : submitted (billable {args.type.upper()} job)")
    print("next          : evaluate the trained model, then approve promotion separately")
    return 0


def _estimate(args: argparse.Namespace, rows: int, hyperparameters: dict | None) -> dict | None:
    if args.type != "rft":
        return None
    from castia.finetuning.preflight import estimate_training

    hyper = hyperparameters or {}
    return estimate_training(
        rows,
        n_epochs=hyper.get("n_epochs"),
        batch_size=hyper.get("batch_size"),
        seconds_per_step=args.seconds_per_step,
        hourly_usd=args.hourly_usd,
    )


def _write_jsonl(path, rows: list) -> str:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return str(path)


def _validate_splits(
    training_type: str,
    train: list[dict],
    validation: list[dict] | None,
    *,
    grader: dict | None,
    require_rft_grader: bool,
    model: str | None = None,
) -> list[str]:
    if training_type == "sft":
        if grader is not None:
            return ["--grader is only valid with --type rft"]
        from castia.finetuning.sft import validate_sft_splits

        return validate_sft_splits(train, validation)
    if training_type == "dpo":
        if grader is not None:
            return ["--grader is only valid with --type rft"]
        from castia.finetuning.dpo import validate_dpo_splits

        return validate_dpo_splits(train, validation)

    from castia.finetuning.preflight import min_rows_problem

    short = min_rows_problem("rft", len(train))
    floor = [short] if short else []
    if validation is None:
        from castia.finetuning.rft import validate_rft_dataset

        problems = ["RFT requires --validation"] if require_rft_grader else []
        problems.extend(floor)
        if grader is not None:
            from castia.finetuning.rft import validate_grader

            problems.extend(f"grader: {p}" for p in validate_grader(grader))
        problems.extend(validate_rft_dataset(
            train, grader=grader, split="training", model=model,
        ))
        return problems
    if grader is None and require_rft_grader:
        return ["RFT requires --grader", *floor]
    from castia.finetuning.rft import validate_rft_splits

    return floor + validate_rft_splits(train, validation, grader=grader, model=model)


def _hyperparameters(args: argparse.Namespace) -> dict | None:
    hyperparameters = {}
    for name in ("n_epochs", "learning_rate_multiplier", "beta", "l2_multiplier"):
        value = getattr(args, name)
        if value is not None:
            hyperparameters[name] = value
    if args.batch_size is not None:
        from castia.finetuning.preflight import normalize_batch_size

        batch_size = normalize_batch_size(args.batch_size)
        if batch_size is not None:
            hyperparameters["batch_size"] = batch_size
    if args.reasoning_effort:
        hyperparameters["reasoning_effort"] = args.reasoning_effort
    if args.eval_interval is not None:
        hyperparameters["eval_interval"] = args.eval_interval
    return hyperparameters or None


def _build_job(
    training_type: str,
    *,
    model: str,
    training_file: str,
    validation_file: str | None,
    grader: dict | None,
    hyperparameters: dict | None,
    suffix: str | None,
) -> dict:
    if training_type == "sft":
        from castia.finetuning.sft import build_sft_job

        return build_sft_job(
            model=model,
            training_file=training_file,
            validation_file=validation_file,
            hyperparameters=hyperparameters,
            suffix=suffix,
        )
    if training_type == "dpo":
        from castia.finetuning.dpo import build_dpo_job

        return build_dpo_job(
            model=model,
            training_file=training_file,
            validation_file=validation_file,
            hyperparameters=hyperparameters,
            suffix=suffix,
        )
    from castia.finetuning.rft import build_rft_job

    if validation_file is None or grader is None:
        raise ValueError("RFT requires validation_file and grader")
    return build_rft_job(
        model=model,
        training_file=training_file,
        validation_file=validation_file,
        grader=grader,
        hyperparameters=hyperparameters,
        suffix=suffix,
    )


def _submit_job(training_type: str, job: dict, *, endpoint: str | None = None):
    if training_type == "sft":
        from castia.finetuning.sft import submit_sft_job

        return submit_sft_job(job, endpoint=endpoint)
    if training_type == "dpo":
        from castia.finetuning.dpo import submit_dpo_job

        return submit_dpo_job(job, endpoint=endpoint)
    from castia.finetuning.rft import submit_rft_job

    return submit_rft_job(job, endpoint=endpoint)


def _register_management_commands(subparsers: argparse._SubParsersAction) -> None:
    for name in (
        "list", "status", "events", "checkpoints", "results", "cancel", "delete", "handoff",
    ):
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
        if name == "delete":
            parser.add_argument(
                "--confirm-model", dest="confirm_model",
                help="required when the job has a trained model: deleting the job "
                     "deletes that model",
            )
            parser.add_argument("--yes", action="store_true",
                                help="confirm deleting a job that has no trained model")
        parser.set_defaults(func=run, management_command=name)


def run(args: argparse.Namespace) -> int:
    from azure.core.exceptions import AzureError
    from openai import OpenAIError

    from castia.finetuning.jobs import FineTuningClient, JobReference

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
            elif command == "delete":
                result = client.delete(
                    job_id, confirm_model=args.confirm_model, yes=args.yes,
                )
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
                if command == "status":
                    timing = client.timing(job)
                    if timing is not None:
                        result["timing"] = timing
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

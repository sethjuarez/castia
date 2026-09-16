"""CLI registrar for local lifecycle evidence and explicit callback handoffs.

The parent CLI calls ``register_commands(subparsers)`` and dispatches
``args.func(args)``. Artifact-producing commands print ``{"id": ..., "kind": ...}``.
Use ``lifecycle show ID`` to inspect the complete immutable record.

Snapshot ``--config`` is a JSON object containing ``source_files``,
``dependencies``, ``model``, ``instructions`` and optional ``tools``. Dataset
``--traces`` is JSONL of ReviewedTrace fields. Evaluator ``--evaluator`` is JSON
with ``name``, ``version`` and ``configuration``. Gate ``--gate`` is JSON of
AcceptanceGate keyword arguments.

Adapters are ordinary reviewed local Python modules, not a sandbox. Importing
them executes Python and may perform whatever operations their authors chose.
``--allow-code`` acknowledges this; ``--adapter-root`` confines adapter discovery
to an explicit local source tree. Deployment additionally requires ``--execute``.
Castia supplies no network client, shell command, or fine-tuning submission here.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.machinery
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from castia.lifecycle.operations import (
    AcceptanceGate,
    ReviewedTrace,
    compare_runs,
    create_agent_snapshot,
    curate_dataset,
    diff_candidates,
    evaluate,
    stage_candidate,
)
from castia.lifecycle.records import (
    AgentSnapshot,
    Candidate,
    DatasetSnapshot,
    Decision,
    Evaluator,
    Evidence,
    LifecycleValidationError,
    Run,
    canonical_json,
    plain,
)
from castia.lifecycle.storage import ArtifactStore, DeploymentJournal

_T = TypeVar("_T", AgentSnapshot, Candidate, DatasetSnapshot, Decision, Run)
_ADAPTER = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", re.ASCII)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys are not supported")
        result[key] = value
    return result


def _json_object(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise TypeError("configuration must be a JSON object")
    return value


def _adapter(reference: str, *, root: str, allow_code: bool) -> Callable[..., Any]:
    """Resolve local source without importing unreviewed parent packages first."""
    if not allow_code:
        raise ValueError("adapter execution requires --allow-code after local review")
    if not _ADAPTER.fullmatch(reference):
        raise ValueError("adapter must be module:callable")
    module_name, attribute = reference.split(":")
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError("adapter root must be a local directory")
    search = [str(base)]
    names = module_name.split(".")
    origin = None
    for index in range(len(names)):
        fullname = ".".join(names[:index + 1])
        spec = importlib.machinery.PathFinder.find_spec(fullname, search)
        if spec is None or spec.origin is None:
            raise ValueError("adapter requires explicit local Python source packages")
        origin = Path(spec.origin).resolve(strict=True)
        if not origin.is_relative_to(base) or origin.suffix != ".py":
            raise ValueError("adapter source must remain inside --adapter-root")
        current = Path(spec.origin)
        while current != base:
            if current.is_symlink():
                raise ValueError("symlinked adapters are not allowed")
            current = current.parent
        cached = sys.modules.get(fullname)
        if cached is not None and (
            not getattr(cached, "__file__", None)
            or Path(cached.__file__).resolve() != origin
        ):
            raise ValueError("adapter module name conflicts with an already imported module")
        search = list(spec.submodule_search_locations or ())
    root_string = str(base)
    sys.path.insert(0, root_string)
    try:
        module = importlib.import_module(module_name)
    finally:
        sys.path.remove(root_string)
    if Path(module.__file__).resolve() != origin:
        raise ValueError("adapter import origin changed")
    callback = getattr(module, attribute)
    if not callable(callback):
        raise TypeError("adapter attribute must be callable")
    return callback


def _record(store: ArtifactStore, identity: str, kind: type[_T]) -> _T:
    record = store.get(identity)
    if not isinstance(record, kind):
        raise TypeError(f"artifact must be {kind.__name__}")
    return record


def _persist(store: ArtifactStore, record: Evidence, *, status: str | None = None) -> None:
    result = {"id": store.put(record), "kind": record.kind}
    if status is not None:
        result["status"] = status
    print(canonical_json(result))


def _code_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter-root", default=".", help="reviewed local Python source root")
    parser.add_argument(
        "--allow-code", action="store_true",
        help="acknowledge that the reviewed adapter import and invocation execute Python",
    )


def register_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register ``lifecycle`` and its concrete command handlers on a parent parser."""
    lifecycle = subparsers.add_parser("lifecycle", help="local, immutable agent lifecycle evidence")
    lifecycle.add_argument("--store", default=".castia/evidence", help="artifact store directory")
    commands = lifecycle.add_subparsers(dest="lifecycle_command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=help_text)
        parser.add_argument("--store", default=argparse.SUPPRESS, help="artifact store directory")
        parser.set_defaults(func=run)
        return parser

    snapshot = command("snapshot", "hash explicit agent source and configuration")
    snapshot.add_argument("--root", default=".", help="root of explicitly named source files")
    snapshot.add_argument("--config", required=True, help="snapshot keyword arguments as JSON")

    dataset = command("dataset", "curate reviewed, redacted JSONL traces into disjoint splits")
    dataset.add_argument("--traces", required=True, help="JSONL of ReviewedTrace fields")
    dataset.add_argument("--redactor", required=True, help="reviewed module:callable redaction adapter")
    dataset.add_argument("--redaction-version", required=True)
    dataset.add_argument("--heldout-fraction", type=float, default=0.2)
    dataset.add_argument("--seed", default="castia")
    _code_options(dataset)

    evaluation = command("evaluate", "run bounded per-example evaluation through a reviewed adapter")
    evaluation.add_argument("--agent", required=True, help="AgentSnapshot artifact ID")
    evaluation.add_argument("--dataset", required=True, help="DatasetSnapshot artifact ID")
    evaluation.add_argument("--evaluator", required=True, help="versioned evaluator identity JSON")
    evaluation.add_argument("--callback", required=True, help="async module:callable evaluation adapter")
    evaluation.add_argument("--split", choices=("train", "heldout"), default="heldout")
    evaluation.add_argument("--repeats", type=int, default=1)
    evaluation.add_argument("--concurrency", type=int, default=4)
    evaluation.add_argument("--timeout", type=float, default=30)
    _code_options(evaluation)

    comparison = command("compare", "persist an acceptance decision; rejection exits with status 1")
    comparison.add_argument("baseline", help="baseline Run artifact ID")
    comparison.add_argument("candidate", help="candidate Run artifact ID")
    comparison.add_argument("--gate", help="AcceptanceGate keyword arguments as JSON")

    staging = command("stage", "capture explicit candidate config files without deploying")
    staging.add_argument("--root", default=".")
    staging.add_argument("--file", action="append", required=True, help="relative config file; repeatable")
    staging.add_argument("--baseline", required=True, help="baseline AgentSnapshot artifact ID")
    staging.add_argument("--agent", required=True, help="candidate AgentSnapshot artifact ID")

    difference = command("diff", "show changed configuration paths and their before/after hashes")
    difference.add_argument("baseline", help="baseline Candidate artifact ID")
    difference.add_argument("candidate", help="new Candidate artifact ID")

    for name in ("promote", "rollback"):
        handoff = command(name, "explicit deployment handoff with verified journal advancement")
        handoff.add_argument("--journal", required=True, help="deployment journal directory")
        handoff.add_argument("--expected-revision", type=int, required=True)
        handoff.add_argument("--deploy", required=True, help="async module:callable deploy adapter")
        handoff.add_argument("--verify", required=True, help="async module:callable verification adapter")
        handoff.add_argument(
            "--execute", action="store_true",
            help="explicitly authorize this deployment handoff (required to execute)",
        )
        if name == "promote":
            handoff.add_argument("--candidate", required=True, help="Candidate artifact ID")
            handoff.add_argument("--decision", required=True, help="accepted Decision artifact ID")
        _code_options(handoff)

    reconciliation = command("reconcile", "verify prior operations can no longer write before recovery")
    reconciliation.add_argument("--journal", required=True)
    reconciliation.add_argument("--expected-revision", type=int, required=True)
    reconciliation.add_argument(
        "--verify", required=True, help="async module:callable returning operation-quiescence evidence",
    )
    reconciliation.add_argument("--execute", action="store_true", help="authorize explicit reconciliation")
    _code_options(reconciliation)

    show = command("show", "validate and display an artifact or deployment journal")
    show.add_argument("id", nargs="?", help="artifact ID")
    show.add_argument("--journal", help="show verified journal state instead of an artifact")


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed lifecycle command; 0 success, 1 rejected evidence, 2 error."""
    command = args.lifecycle_command
    try:
        if command in ("dataset", "evaluate", "promote", "rollback", "reconcile") and not args.allow_code:
            raise ValueError("reviewed adapter execution requires --allow-code")
        if command in ("promote", "rollback", "reconcile") and not args.execute:
            raise ValueError("deployment requires --execute")
        if command == "show" and bool(args.id) == bool(args.journal):
            raise ValueError("show requires exactly one artifact ID or --journal")
        store = ArtifactStore(args.store)
        if command == "snapshot":
            _persist(store, create_agent_snapshot(args.root, **_json_object(args.config)))
        elif command == "dataset":
            rows = []
            for line in Path(args.traces).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(ReviewedTrace(**json.loads(line, object_pairs_hook=_pairs)))
            redactor = _adapter(args.redactor, root=args.adapter_root, allow_code=args.allow_code)
            _persist(store, curate_dataset(
                rows, redact=redactor, redaction_version=args.redaction_version,
                heldout_fraction=args.heldout_fraction, seed=args.seed,
            ))
        elif command == "evaluate":
            agent = _record(store, args.agent, AgentSnapshot)
            dataset = _record(store, args.dataset, DatasetSnapshot)
            evaluator = Evaluator(**_json_object(args.evaluator))
            callback = _adapter(args.callback, root=args.adapter_root, allow_code=args.allow_code)
            result = asyncio.run(evaluate(
                agent, dataset, evaluator, callback, split=args.split,
                repeats=args.repeats, concurrency=args.concurrency, timeout=args.timeout,
            ))
            complete = all(r.error is None and "quality" in r.metrics for r in result.results)
            _persist(store, result, status="completed" if complete else "inconclusive")
            if any(r.error == "timeout" for r in result.results):
                print(
                    "warning: callback timeout is not remote-job cleanup evidence; "
                    "reconcile any callback-owned jobs explicitly",
                    file=sys.stderr,
                )
            return 0 if complete else 1
        elif command == "compare":
            gate = AcceptanceGate(**_json_object(args.gate)) if args.gate else AcceptanceGate()
            decision = compare_runs(
                _record(store, args.baseline, Run), _record(store, args.candidate, Run), gate=gate,
            )
            _persist(store, decision, status="accepted" if decision.accepted else "rejected")
            return 0 if decision.accepted else 1
        elif command == "stage":
            _persist(store, stage_candidate(
                args.root, args.file, baseline=_record(store, args.baseline, AgentSnapshot),
                agent=_record(store, args.agent, AgentSnapshot),
            ))
        elif command == "diff":
            print(canonical_json(diff_candidates(
                _record(store, args.baseline, Candidate), _record(store, args.candidate, Candidate),
            )))
        elif command == "reconcile":
            journal = DeploymentJournal(args.journal)
            state = journal.read()
            if state.revision != args.expected_revision or not state.pending_cleanup:
                raise ValueError("journal is stale or has no pending operation to reconcile")
            verify = _adapter(args.verify, root=args.adapter_root, allow_code=args.allow_code)
            result = asyncio.run(journal.reconcile(verify=verify, expected_revision=args.expected_revision))
            print(canonical_json(plain(result)))
        elif command in ("promote", "rollback"):
            journal = DeploymentJournal(args.journal)
            # Validate stored evidence/revision before importing user code.
            state = journal.read()
            if state.revision != args.expected_revision or state.pending_cleanup:
                raise ValueError("journal is stale or has an unresolved handoff")
            candidate = decision = None
            if command == "promote":
                if state.remote_state == "unknown":
                    raise ValueError("remote state is unknown; explicit rollback/recovery is required")
                candidate = _record(store, args.candidate, Candidate)
                decision = _record(store, args.decision, Decision)
                if (
                    not decision.accepted or candidate.agent_id != decision.candidate_agent_id
                    or candidate.baseline_id != decision.baseline_agent_id
                ):
                    raise ValueError("candidate requires a matching accepted decision")
                if state.known_good:
                    previous = _record(journal.artifacts, state.known_good, Candidate)
                    if candidate.id == previous.id or candidate.baseline_id != previous.agent_id:
                        raise ValueError("candidate must extend the current known-good agent")
            elif not state.known_good or (
                state.remote_state != "unknown" and not state.previous_good
            ):
                raise ValueError("there is no verified rollback target")
            deploy = _adapter(args.deploy, root=args.adapter_root, allow_code=args.allow_code)
            verify = _adapter(args.verify, root=args.adapter_root, allow_code=args.allow_code)
            options = {
                "deploy": deploy, "verify": verify, "expected_revision": args.expected_revision,
            }
            result = asyncio.run(
                journal.promote(candidate, decision, **options) if command == "promote"
                else journal.rollback(**options)
            )
            print(canonical_json(plain(result)))
        elif command == "show":
            if args.journal:
                print(canonical_json(plain(DeploymentJournal(args.journal).read())))
            else:
                record = store.get(args.id)
                print(canonical_json({"id": record.id, "record": record.to_dict()}))
        else:
            raise ValueError("unknown lifecycle command")
        return 0
    except KeyboardInterrupt:
        print("error: lifecycle command cancelled", file=sys.stderr)
        return 130
    except LifecycleValidationError as exc:
        print(
            f"error: lifecycle {command}: {exc.code}: {LifecycleValidationError(exc.code)}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 -- adapter errors must not expose private exception text
        print(
            f"error: lifecycle {command} failed ({type(exc).__name__}); "
            "check input schemas, artifact integrity, and explicit adapter/execute flags",
            file=sys.stderr,
        )
        return 2

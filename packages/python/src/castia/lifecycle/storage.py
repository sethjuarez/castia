"""Local immutable artifacts and serialized, verified deployment handoffs.

The journal is append-only and hash-chained. A lock is held across injected
deployment/verification callbacks; optimistic revisions reject stale callers.
An interrupted process deliberately leaves an unresolved handoff/lock for manual
reconciliation rather than guessing whether a deployment reached production.
Hashes detect accidental/tampered content, not a malicious writer able to rewrite
the entire store. Use filesystem permissions or signed external attestations when
evidence crosses trust boundaries.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from castia.lifecycle.records import (
    Candidate,
    Decision,
    Evidence,
    canonical_json,
    check_public,
    content_hash,
    digest,
    integer,
    record_from_dict,
    text,
)


class IntegrityError(ValueError):
    """An artifact or deployment journal is malformed or fails its hash."""


class ConflictError(RuntimeError):
    """A concurrent/stale caller or unresolved handoff prevents mutation."""


class DeploymentError(RuntimeError):
    """The injected deployment or verification failed; known-good did not move."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrityError("duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise IntegrityError("symlinked evidence is not allowed")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise IntegrityError("expected evidence JSON object")
        return value
    except (ValueError, OSError, UnicodeError) as exc:
        raise IntegrityError("cannot read evidence JSON") from exc


def _immutable_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a fully flushed file with a no-replace atomic hard link."""
    raw = (canonical_json(payload) + "\n").encode("utf-8")
    staging = path.parent / f".write-{uuid.uuid4().hex}"
    try:
        with staging.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staging, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != raw:
                raise IntegrityError("immutable evidence already exists with different bytes")
    finally:
        staging.unlink(missing_ok=True)


class ArtifactStore:
    """Content-addressed JSON artifacts; writes are immutable and idempotent."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, record: Evidence) -> str:
        # Decode before persistence so only the public evidence schema is stored.
        validated = record_from_dict(record.to_dict())
        identity = validated.id
        path = self.root / f"{identity}.json"
        _immutable_write(path, {"id": identity, "record": validated.to_dict()})
        self.get(identity)
        return identity

    def get(self, identity: str) -> Evidence:
        digest(identity)
        payload = _read(self.root / f"{identity}.json")
        if set(payload) != {"id", "record"} or payload["id"] != identity:
            raise IntegrityError("artifact envelope identity mismatch")
        try:
            record = record_from_dict(payload["record"])
            if record.id != identity:
                raise IntegrityError("artifact content hash mismatch")
            return record
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise IntegrityError("invalid artifact content") from exc


@dataclass(frozen=True)
class JournalState:
    revision: int = 0
    known_good: str | None = None
    previous_good: tuple[str, ...] = ()
    last_status: str | None = None
    head: str | None = None
    remote_state: str = "uninitialized"
    pending_cleanup: bool = False


DeployCallback = Callable[[Candidate], Awaitable[object]]
VerifyCallback = Callable[[Candidate, object], Awaitable[bool]]
ReconcileCallback = Callable[[JournalState], Awaitable[Mapping[str, Any]]]


class DeploymentJournal:
    """Explicit local handoff: callbacks deploy and verify, Castia records proof.

    ``expected_revision`` is mandatory. A failed verification keeps the previous
    known-good pointer, but does NOT imply the remote environment was restored.
    Call rollback explicitly: after failure/cancellation it restores known-good;
    after success it deploys the preceding verified configuration.
    ``remote_state`` becomes ``unknown`` as soon as a handoff starts and stays
    unknown on failure or cancellation; only verification sets it to ``verified``.
    Every failed/cancelled handoff sets ``pending_cleanup`` and blocks ALL further
    handoffs, including rollback, until ``reconcile`` records explicit evidence
    that no older operation can still complete a write. Seeing an active version
    is not proof of operation quiescence. After reconciliation, remote state stays
    unknown until a verified rollback restores it.
    Promotion is blocked while remote state is unknown. With no verified
    rollback target, external recovery and manual journal reconciliation are
    required; deleting evidence or assuming an azd timeout cancelled Azure-side
    deployment is not recovery.

    An azd adapter can wrap an explicitly configured
    ``castia.delivery.AzdDeployment.deploy`` / ``verify`` with
    ``asyncio.to_thread``. Verify the returned deployment version, model, and
    optimizer candidate against the intended handoff before returning literal
    ``True``. The local evidence ``Candidate.id`` is a content hash, not an
    optimizer candidate name. The adapter must map those identities explicitly
    and ensure the staged config is materialized in the deployment root first;
    neither this journal nor ``AzdDeployment`` rewrites candidate configuration.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = ArtifactStore(self.root / "artifacts")
        self.events = self.root / "events"
        self.events.mkdir(exist_ok=True)
        if self.events.is_symlink():
            raise IntegrityError("symlinked journal is not allowed")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        path = self.root / ".handoff.lock"
        try:
            stream = path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise ConflictError("handoff locked; reconcile interrupted work before retry") from exc
        try:
            with stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
                yield
        finally:
            path.unlink(missing_ok=True)

    def read(self) -> JournalState:
        """Verify the complete journal chain and all candidate/decision references."""
        state = JournalState()
        pending: dict[str, Any] | None = None
        last_event: dict[str, Any] | None = None
        for index, path in enumerate(sorted(self.events.glob("*.json")), start=1):
            if path.name != f"{index:012d}.json":
                raise IntegrityError("journal sequence gap or unexpected path")
            envelope = _read(path)
            if set(envelope) != {"id", "event"}:
                raise IntegrityError("invalid journal envelope")
            event = envelope["event"]
            if not isinstance(event, dict) or set(event) != {
                "schema_version", "revision", "previous", "action", "candidate_id",
                "decision_id", "status", "reconciliation",
            }:
                raise IntegrityError("invalid journal event")
            if event["schema_version"] != 1 or type(event["schema_version"]) is not int:
                raise IntegrityError("unsupported journal schema")
            if event["revision"] != index or type(event["revision"]) is not int:
                raise IntegrityError("invalid journal revision")
            if event["previous"] != state.head or content_hash(event) != envelope["id"]:
                raise IntegrityError("journal hash chain mismatch")
            if event["action"] not in ("promote", "rollback", "reconcile") or event["status"] not in (
                "started", "verified", "failed", "cancelled", "reconciled",
            ):
                raise IntegrityError("unknown handoff transition")
            if event["action"] == "reconcile":
                if (
                    event["status"] != "reconciled" or not state.pending_cleanup
                    or last_event is None or any(
                        event[key] != last_event[key] for key in ("candidate_id", "decision_id")
                    )
                ):
                    raise IntegrityError("reconciliation has no matching uncertain operation")
                try:
                    self._reconciliation_evidence(event["reconciliation"])
                except (ValueError, TypeError) as exc:
                    raise IntegrityError("invalid reconciliation evidence") from exc
                state = JournalState(
                    index, state.known_good, state.previous_good, "reconciled", envelope["id"],
                    state.remote_state, False,
                )
                pending = None
                last_event = event
                continue
            if event["status"] == "reconciled" or event["reconciliation"] is not None:
                raise IntegrityError("reconciliation is a separate explicit operation")
            try:
                candidate = self.artifacts.get(event["candidate_id"])
                if not isinstance(candidate, Candidate):
                    raise IntegrityError("handoff must reference a candidate")
                if event["action"] == "promote":
                    decision = self.artifacts.get(event["decision_id"])
                    if not isinstance(decision, Decision):
                        raise IntegrityError("handoff must reference an acceptance decision")
                    self._accepted(candidate, decision)
                elif event["decision_id"] is not None:
                    raise IntegrityError("rollback cannot fabricate an acceptance decision")
            except (ValueError, TypeError) as exc:
                raise IntegrityError("invalid journal artifact reference") from exc
            history = state.previous_good
            known_good = state.known_good
            if event["status"] == "started":
                if pending is not None:
                    raise IntegrityError("overlapping handoff transitions")
                if state.pending_cleanup:
                    raise IntegrityError("handoff cannot bypass pending remote-write reconciliation")
                if event["action"] == "promote" and state.remote_state == "unknown":
                    raise IntegrityError("promotion cannot bypass unresolved remote state")
                if event["action"] == "promote" and known_good is not None:
                    previous = self.artifacts.get(known_good)
                    if not isinstance(previous, Candidate) or candidate.baseline_id != previous.agent_id:
                        raise IntegrityError("promotion does not extend known-good evidence")
                if event["action"] == "rollback":
                    target = (
                        known_good if state.remote_state == "unknown"
                        else history[-1] if history else None
                    )
                    if target is None or event["candidate_id"] != target:
                        raise IntegrityError("rollback is not a verified recovery candidate")
                pending = event
            else:
                if pending is None or any(
                    event[key] != pending[key] for key in ("action", "candidate_id", "decision_id")
                ):
                    raise IntegrityError("completion has no matching handoff")
                if event["status"] == "verified":
                    if event["action"] == "rollback":
                        if known_good != event["candidate_id"]:
                            history = history[:-1]
                    elif known_good is not None:
                        history = (*history, known_good)
                    known_good = event["candidate_id"]
                pending = None
            state = JournalState(
                index, known_good, history, event["status"], envelope["id"],
                "verified" if event["status"] == "verified" else "unknown",
                event["status"] != "verified",
            )
            last_event = event
        return state

    @staticmethod
    def _accepted(candidate: Candidate, decision: Decision) -> None:
        if (
            not decision.accepted or candidate.agent_id != decision.candidate_agent_id
            or candidate.baseline_id != decision.baseline_agent_id
        ):
            raise ValueError("candidate must match an accepted comparison decision")

    def _append(
        self, state: JournalState, *, action: str, candidate_id: str,
        decision_id: str | None, status: str, reconciliation: Mapping[str, Any] | None = None,
    ) -> JournalState:
        event = {
            "schema_version": 1, "revision": state.revision + 1, "previous": state.head,
            "action": action, "candidate_id": candidate_id,
            "decision_id": decision_id, "status": status, "reconciliation": reconciliation,
        }
        _immutable_write(
            self.events / f"{state.revision + 1:012d}.json",
            {"id": content_hash(event), "event": event},
        )
        return self.read()

    async def _handoff(
        self, state: JournalState, candidate: Candidate, *, action: str,
        decision_id: str | None, deploy: DeployCallback, verify: VerifyCallback,
    ) -> JournalState:
        state = self._append(
            state, action=action, candidate_id=candidate.id,
            decision_id=decision_id, status="started",
        )
        try:
            receipt = await deploy(candidate)
            if isinstance(receipt, Mapping):
                status = receipt.get("status")
                if isinstance(status, str) and status.strip().lower() in (
                    "failed", "failure", "error", "cancelled", "canceled",
                ):
                    raise DeploymentError("deployment callback reported explicit failure")
            verified = await verify(candidate, receipt)
            if verified is not True:
                raise DeploymentError("deployment verification did not explicitly succeed")
        except asyncio.CancelledError:
            self._append(
                state, action=action, candidate_id=candidate.id,
                decision_id=decision_id, status="cancelled",
            )
            raise
        except Exception as exc:
            self._append(
                state, action=action, candidate_id=candidate.id,
                decision_id=decision_id, status="failed",
            )
            raise DeploymentError("handoff failed; known-good is unchanged") from exc
        return self._append(
            state, action=action, candidate_id=candidate.id,
            decision_id=decision_id, status="verified",
        )

    def _check_revision(self, expected_revision: int) -> JournalState:
        integer(expected_revision, "expected_revision")
        state = self.read()
        if state.revision != expected_revision:
            raise ConflictError("stale journal revision")
        if state.last_status == "started":
            raise ConflictError("interrupted handoff requires manual reconciliation")
        if state.pending_cleanup:
            raise ConflictError("remote writes may still be outstanding; reconcile before any handoff")
        return state

    @staticmethod
    def _reconciliation_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("reconciliation must return structured evidence")
        if set(value) != {"no_outstanding_writes", "evidence"} or value["no_outstanding_writes"] is not True:
            raise ValueError("reconciliation must explicitly establish no outstanding writes")
        text(value["evidence"], "terminal operation evidence")
        check_public(value)
        return dict(value)

    async def reconcile(
        self, *, verify: ReconcileCallback, expected_revision: int,
    ) -> JournalState:
        """Record callback-verified operation quiescence; never deploy or guess.

        ``verify(state)`` must check every possibly outstanding prior operation,
        not merely the active deployment, and return exactly
        ``{"no_outstanding_writes": True, "evidence": "terminal operation IDs/status evidence"}``.
        Missing/false evidence leaves pending cleanup in place. A caller must
        reconcile crashed lock ownership separately before opening a stale lock.
        """
        with self._lock():
            integer(expected_revision, "expected_revision")
            state = self.read()
            if state.revision != expected_revision:
                raise ConflictError("stale journal revision")
            if not state.pending_cleanup:
                raise ValueError("there is no outstanding handoff to reconcile")
            prior = _read(self.events / f"{state.revision:012d}.json")["event"]
            evidence = self._reconciliation_evidence(await verify(state))
            return self._append(
                state, action="reconcile", candidate_id=prior["candidate_id"],
                decision_id=prior["decision_id"], status="reconciled", reconciliation=evidence,
            )

    async def promote(
        self, candidate: Candidate, decision: Decision, *,
        deploy: DeployCallback, verify: VerifyCallback, expected_revision: int,
    ) -> JournalState:
        self._accepted(candidate, decision)
        with self._lock():
            state = self._check_revision(expected_revision)
            if state.remote_state == "unknown":
                raise ConflictError(
                    "remote state is unknown; explicitly rollback or reconcile recovery before promotion"
                )
            if state.known_good is not None:
                if state.known_good == candidate.id:
                    raise ValueError("candidate is already the known-good deployment")
                previous = self.artifacts.get(state.known_good)
                if not isinstance(previous, Candidate) or previous.agent_id != candidate.baseline_id:
                    raise ValueError("candidate baseline must match known-good agent")
            self.artifacts.put(candidate)
            decision_id = self.artifacts.put(decision)
            return await self._handoff(
                state, candidate, action="promote", decision_id=decision_id,
                deploy=deploy, verify=verify,
            )

    async def rollback(
        self, *, deploy: DeployCallback, verify: VerifyCallback, expected_revision: int,
    ) -> JournalState:
        with self._lock():
            state = self._check_revision(expected_revision)
            target = (
                state.known_good if state.remote_state == "unknown"
                else state.previous_good[-1] if state.previous_good else None
            )
            if target is None:
                raise ValueError("no previous verified deployment is available")
            candidate = self.artifacts.get(target)
            if not isinstance(candidate, Candidate):
                raise IntegrityError("rollback reference must be a candidate")
            return await self._handoff(
                state, candidate, action="rollback", decision_id=None, deploy=deploy, verify=verify,
            )

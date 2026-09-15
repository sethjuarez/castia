"""Offline, reviewable evidence for the Castia agent lifecycle.

Typical flow::

    baseline = create_agent_snapshot(root, source_files=["agent.py"],
        dependencies={"castia": "0.5.0"}, model={"deployment": "baseline"},
        instructions="Help the user.")
    dataset = curate_dataset(reviewed_traces, redact=my_redactor,
        redaction_version="policy-v1")
    run = await evaluate(baseline, dataset,
        Evaluator("rubric", "v1", {"rubric_sha256": rubric_hash}), evaluate_one)
    ArtifactStore(".castia/evidence").put(run)

Source/configuration inputs are explicit, never ambient environment dumps.
Training/heldout rows carry independently reviewed references and redacted
provenance. Evaluator versions must cover judge model, rubric, and configuration.
Deployments happen only through explicitly supplied handoff callbacks. No import
or operation here submits an optimizer, evaluation-service, or fine-tuning job.
"""

from .operations import (
    AcceptanceGate,
    EvaluationCallback,
    ReviewedTrace,
    compare_runs,
    create_agent_snapshot,
    curate_dataset,
    dataset_jsonl,
    diff_candidates,
    evaluate,
    stage_candidate,
)
from .records import (
    REFERENCE_ORIGINS,
    SCHEMA_VERSION,
    AgentSnapshot,
    Candidate,
    ConfigFile,
    DatasetSnapshot,
    Decision,
    EvaluationResult,
    Evaluator,
    Evidence,
    Example,
    FileDigest,
    LifecycleValidationError,
    Run,
    canonical_json,
    content_hash,
    record_from_dict,
)
from .storage import (
    ArtifactStore,
    ConflictError,
    DeployCallback,
    DeploymentError,
    DeploymentJournal,
    IntegrityError,
    JournalState,
    ReconcileCallback,
    VerifyCallback,
)

__all__ = [
    "REFERENCE_ORIGINS", "SCHEMA_VERSION", "AcceptanceGate", "AgentSnapshot", "ArtifactStore",
    "Candidate", "ConfigFile", "ConflictError", "DatasetSnapshot", "Decision",
    "DeployCallback", "DeploymentError", "DeploymentJournal", "EvaluationCallback",
    "EvaluationResult", "Evaluator", "Evidence", "Example", "FileDigest",
    "IntegrityError", "JournalState", "LifecycleValidationError", "ReconcileCallback",
    "ReviewedTrace", "Run", "VerifyCallback",
    "canonical_json", "compare_runs", "content_hash", "create_agent_snapshot",
    "curate_dataset", "dataset_jsonl", "diff_candidates", "evaluate",
    "record_from_dict", "stage_candidate",
]

"""Pure pre-submit checks for fine-tuning jobs (offline, no Azure).

These guardrails come from hours lost in real RFT work: a ``batch_size`` of
``-1`` that the service rejects with a 400, jobs sized without knowing their
step count or cost, an ``estimated_finish`` that silently includes GPU queue
time, and a job delete that also deletes its trained model. Every function
here is pure and mirrored by
``spec/conformance/finetuning/preflight.json`` so other SDKs decide the same
way.

Cost figures are estimates for a spend gate, not a bill. The rate table needs
upkeep; pass explicit rates when you know better.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

# Published or observed prices used for the spend line. ``source`` says where
# each figure came from so an out-of-date entry is easy to spot.
RFT_COST_TABLE: dict[str, dict[str, float | str]] = {
    "developer": {
        "training_hourly_usd": 40.0,
        "deployment_daily_usd": 8.64,
        "source": "Foundry field notes (developer tier), 2026-11",
    },
}
DEFAULT_TIER = "developer"
# Wall-clock seconds per RFT step when nothing better is known. RFT steps
# sample and grade, so they are slow; replace this with a probe measurement.
DEFAULT_SECONDS_PER_STEP = 60.0
BATCH_SIZE_RANGE = (1, 256)
_AUTO_BATCH = {"-1", "auto", ""}

# The three human approvals for a trained model. Approving one never implies
# another: a succeeded job can still be worse on held-out data.
APPROVAL_STAGES = ("train", "evaluate", "promote")

# Python grader imports the RFT grader service rejects, with the evidence and a
# fix-it hint. Mirrors ``spec/conformance/finetuning/python_grader_rules.json``.
# An unlisted import is not proven allowed; add entries as the service reports
# them.
DENIED_GRADER_MODULES: dict[str, dict[str, str]] = {
    "urllib.parse": {
        "evidence": (
            "Waypoint MAI-2 RFT probe ftjob-82ff966f, 2026-11-25: graders "
            "validate/run HTTP 400 invalid_grader \"Invalid grader source: use of "
            "module 'urllib.parse' is not allowed in Python graders\""
        ),
        "fix": "parse the string with str methods or re instead of urllib.parse",
    },
}
# The service rejects RFT jobs with fewer training rows (HTTP 400 invalidPayload).
MIN_RFT_TRAINING_ROWS = 10


def python_grader_imports(source: str) -> list[str]:
    """Every module a Python grader imports, in source order (pure).

    ``from a import b`` reports ``a.b`` as well as ``a`` so a denied submodule
    imported by name is still found. Unparseable source returns ``[]``; the
    service reports syntax errors itself.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def python_grader_denied_imports(source: str) -> list[str]:
    """Denied modules (see :data:`DENIED_GRADER_MODULES`) a grader imports."""
    found: list[str] = []
    for module in python_grader_imports(source):
        for denied in DENIED_GRADER_MODULES:
            if (module == denied or module.startswith(denied + ".")) and denied not in found:
                found.append(denied)
    return found


def python_grader_problems(source: str) -> list[str]:
    """Fix-it problems for imports the RFT grader service rejects."""
    return [
        f"python grader imports {module!r}, which the RFT grader service rejects; "
        f"{DENIED_GRADER_MODULES[module]['fix']}"
        for module in python_grader_denied_imports(source)
    ]


def min_rows_problem(training_type: str, rows: int) -> str | None:
    """The service's minimum training rows, as a problem when ``rows`` is short."""
    if training_type == "rft" and rows < MIN_RFT_TRAINING_ROWS:
        return (
            f"training: {rows} example(s); RFT requires at least "
            f"{MIN_RFT_TRAINING_ROWS} (the service rejects the job with HTTP 400)"
        )
    return None


def normalize_batch_size(value: object) -> int | None:
    """Return an explicit batch size, or ``None`` to let the service choose.

    ``-1``, ``"auto"``, and empty values mean automatic and are dropped from
    the payload, because the service rejects ``-1`` with
    ``batchSize must be in [1,256]``.
    """
    if value is None or isinstance(value, bool):
        if isinstance(value, bool):
            raise ValueError(_batch_error(value))
        return None
    if isinstance(value, int):
        if value == -1:
            return None
        number = value
    else:
        text = str(value).strip().lower()
        if text in _AUTO_BATCH:
            return None
        try:
            number = int(text)
        except ValueError:
            raise ValueError(_batch_error(value)) from None
        if number == -1:
            return None
    low, high = BATCH_SIZE_RANGE
    if not low <= number <= high:
        raise ValueError(_batch_error(value))
    return number


def _batch_error(value: object) -> str:
    low, high = BATCH_SIZE_RANGE
    return (
        f"batch_size must be an integer in [{low}, {high}], or -1/auto to let "
        f"the service choose; got {value!r}"
    )


def cost_rates(tier: str = DEFAULT_TIER) -> dict[str, float | str]:
    """The cost table entry for ``tier``. Raises for an unknown tier."""
    try:
        return dict(RFT_COST_TABLE[tier])
    except KeyError:
        raise ValueError(
            f"unknown cost tier {tier!r} (known: {', '.join(sorted(RFT_COST_TABLE))})"
        ) from None


def estimate_training(
    rows: int,
    *,
    n_epochs: int | None = None,
    batch_size: int | None = None,
    seconds_per_step: float | None = None,
    hourly_usd: float | None = None,
    tier: str = DEFAULT_TIER,
) -> dict:
    """Estimate steps, wall-clock hours, and training cost (pure).

    There is no ``max_steps``: steps are about ``ceil(rows / batch) * epochs``.
    With an automatic batch size the step count is unknown, so the estimate is
    an upper bound at batch size 1. Every assumed input is listed under
    ``assumptions`` so the spend line can say what it guessed.
    """
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ValueError("rows must be a positive integer")
    assumptions: list[str] = []
    epochs = n_epochs
    if epochs is None:
        epochs = 1
        assumptions.append("n_epochs=1 (service default not known offline)")
    elif isinstance(epochs, bool) or not isinstance(epochs, int) or epochs < 1:
        raise ValueError("n_epochs must be a positive integer")
    bound = "estimate"
    batch = batch_size
    if batch is None:
        batch = 1
        bound = "upper"
        assumptions.append("batch_size=1 upper bound (batch size is automatic)")
    else:
        batch = normalize_batch_size(batch)
        if batch is None:
            batch = 1
            bound = "upper"
            assumptions.append("batch_size=1 upper bound (batch size is automatic)")
    pace = seconds_per_step
    if pace is None:
        pace = DEFAULT_SECONDS_PER_STEP
        assumptions.append(
            f"{DEFAULT_SECONDS_PER_STEP:g}s per step (measure with a probe)"
        )
    if isinstance(pace, bool) or not math.isfinite(pace) or pace <= 0:
        raise ValueError("seconds_per_step must be finite and positive")
    rate = hourly_usd
    if rate is None:
        rate = float(cost_rates(tier)["training_hourly_usd"])
        assumptions.append(f"${rate:g}/h {tier} tier")
    if isinstance(rate, bool) or not math.isfinite(rate) or rate < 0:
        raise ValueError("hourly_usd must be finite and non-negative")
    steps = math.ceil(rows / batch) * epochs
    hours = steps * pace / 3600
    return {
        "rows": rows,
        "n_epochs": epochs,
        "batch_size": batch,
        "steps": steps,
        "hours": round(hours, 3),
        "cost_usd": round(hours * rate, 2),
        "bound": bound,
        "assumptions": assumptions,
    }


def spend_line(estimate: Mapping) -> str:
    """One human line for the spend gate, e.g. ``~12 steps, ~0.2 h, ~$8.00``."""
    prefix = "<=" if estimate.get("bound") == "upper" else "~"
    line = (
        f"{prefix}{estimate['steps']} steps, {prefix}{estimate['hours']:g} h, "
        f"{prefix}${estimate['cost_usd']:.2f} (estimate, not a bill)"
    )
    if estimate.get("assumptions"):
        line += "; assumed " + ", ".join(estimate["assumptions"])
    return line


def training_started_at(events: Iterable[Mapping]) -> int | None:
    """Earliest event time that shows training running, from job events (pure).

    A ``metrics`` event, or a message that says training started or a step
    completed, marks the end of queueing. Returns epoch seconds or ``None``.
    """
    started: int | None = None
    for event in events:
        created = event.get("created_at")
        if not isinstance(created, int) or isinstance(created, bool):
            continue
        message = str(event.get("message") or "").lower()
        running = (
            event.get("type") == "metrics"
            or "training started" in message
            or "started training" in message
            or message.startswith("step ")
        )
        if running and (started is None or created < started):
            started = created
    return started


def split_job_timing(
    *,
    created_at: int,
    started_at: int | None,
    finished_at: int | None,
    now: int,
) -> dict:
    """Split elapsed time into queue and training seconds (pure).

    ``estimated_finish`` from the service includes GPU queue time, which once
    read as two days for a three-hour run. This reports the phases apart.
    """
    end = finished_at if finished_at is not None else now
    if started_at is None:
        return {
            "phase": "finished" if finished_at is not None else "queued",
            "queue_seconds": max(0, end - created_at),
            "training_seconds": 0,
        }
    return {
        "phase": "finished" if finished_at is not None else "training",
        "queue_seconds": max(0, started_at - created_at),
        "training_seconds": max(0, end - started_at),
    }


def delete_guard(
    *,
    fine_tuned_model: str | None,
    deployments: Sequence[Mapping],
    confirm_model: str | None,
    yes: bool,
) -> dict:
    """Decide whether a job delete may proceed (pure).

    Deleting a fine-tuning job deletes its trained model. A job without a model
    needs ``yes``. A job with a model needs ``confirm_model`` to equal the model
    name, and the decision lists the deployments that would stop serving.
    """
    serving = sorted(
        str(d.get("name")) for d in deployments
        if fine_tuned_model and d.get("model") == fine_tuned_model
    )
    if not fine_tuned_model:
        if yes:
            return {"allowed": True, "deployments": [], "reason": "job has no trained model"}
        return {
            "allowed": False, "deployments": [],
            "reason": "pass --yes to delete this job",
        }
    if confirm_model == fine_tuned_model:
        return {"allowed": True, "deployments": serving, "reason": "model delete confirmed"}
    reason = f"deleting this job deletes model {fine_tuned_model!r}"
    if serving:
        reason += f", which deployment(s) {', '.join(serving)} serve"
    reason += f"; pass --confirm-model {fine_tuned_model} to proceed"
    return {"allowed": False, "deployments": serving, "reason": reason}


def approvals(*, train: bool, evaluate: bool = False, promote: bool = False) -> dict:
    """The approval record for a trained model; each stage stands alone."""
    granted = {"train": train, "evaluate": evaluate, "promote": promote}
    return {stage: ("approved" if granted[stage] else "required") for stage in APPROVAL_STAGES}

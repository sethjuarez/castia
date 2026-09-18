"""Direct preference optimization (DPO) validation and payload builders."""

from __future__ import annotations

from collections.abc import Sequence

from castia.finetuning.sft import (
    SFT_HYPERPARAMETERS,
    _has_content_or_tool_calls,
    _validate_chat_messages,
)

DPO_HYPERPARAMETERS = (
    *SFT_HYPERPARAMETERS,
    "beta",
    "l2_multiplier",
)

DPO_METHOD_TYPE = "dpo"


def _validate_preference_messages(messages: object, *, split: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(messages, list) or not messages:
        return [f"{split}: no non-empty message list"]

    has_assistant = False
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            problems.append(f"{split}: message #{i + 1} is not an object")
            continue
        role = msg.get("role")
        if role not in {"assistant", "tool"}:
            problems.append(
                f"{split}: message #{i + 1} role must be 'assistant' or 'tool', "
                f"got {role!r}"
            )
        has_assistant = has_assistant or role == "assistant"
        if not _has_content_or_tool_calls(msg):
            problems.append(
                f"{split}: message #{i + 1} has no content or tool_calls"
            )
    if not has_assistant:
        problems.append(f"{split}: must include at least one assistant message")
    return problems


def validate_dpo_example(row: dict) -> list[str]:
    """Validate one DPO preference JSONL row."""
    problems: list[str] = []
    if not isinstance(row, dict):
        return ["example is not a JSON object"]

    input_block = row.get("input")
    if not isinstance(input_block, dict):
        problems.append("input must be an object")
    else:
        problems.extend(
            _validate_chat_messages(
                input_block.get("messages"),
                split="input",
                require_assistant=False,
            )
        )

    problems.extend(
        _validate_preference_messages(
            row.get("preferred_output"),
            split="preferred_output",
        )
    )
    problems.extend(
        _validate_preference_messages(
            row.get("non_preferred_output"),
            split="non_preferred_output",
        )
    )
    return problems


def validate_dpo_dataset(rows: Sequence[dict], *, split: str = "dataset") -> list[str]:
    """Validate every row of one DPO split."""
    if not rows:
        return [f"{split}: no examples"]
    problems: list[str] = []
    for i, row in enumerate(rows):
        for problem in validate_dpo_example(row):
            problems.append(f"{split}[{i}]: {problem}")
    return problems


def validate_dpo_splits(
    train: Sequence[dict],
    validation: Sequence[dict] | None = None,
) -> list[str]:
    """Validate DPO training and optional validation splits."""
    problems = validate_dpo_dataset(train, split="training")
    if validation is not None:
        problems.extend(validate_dpo_dataset(validation, split="validation"))
    return problems


def build_dpo_method(*, hyperparameters: dict | None = None) -> dict:
    """Build the ``method`` block for DPO fine-tuning."""
    method: dict = {"type": DPO_METHOD_TYPE}
    if hyperparameters:
        unknown = sorted(set(hyperparameters) - set(DPO_HYPERPARAMETERS))
        if unknown:
            raise ValueError(
                f"unknown DPO hyperparameter(s): {', '.join(unknown)} "
                f"(known: {', '.join(DPO_HYPERPARAMETERS)})"
            )
        method["dpo"] = {"hyperparameters": dict(hyperparameters)}
    return method


def build_dpo_job(
    *,
    model: str,
    training_file: str,
    validation_file: str | None = None,
    hyperparameters: dict | None = None,
    suffix: str | None = None,
    seed: int | None = None,
) -> dict:
    """Build ``fine_tuning.jobs.create`` kwargs for DPO."""
    job: dict = {
        "model": model,
        "training_file": training_file,
        "method": build_dpo_method(hyperparameters=hyperparameters),
    }
    if validation_file:
        job["validation_file"] = validation_file
    if suffix is not None:
        job["suffix"] = suffix
    if seed is not None:
        job["seed"] = seed
    return job


def submit_dpo_job(job: dict, *, endpoint: str | None = None):
    """Submit a DPO job through the shared fine-tuning seam."""
    from castia.finetuning.rft import _openai_client

    client = _openai_client(endpoint)
    return client.fine_tuning.jobs.create(**job)

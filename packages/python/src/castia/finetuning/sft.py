"""Supervised fine-tuning (SFT) dataset validation and payload builders."""

from __future__ import annotations

from collections.abc import Sequence

SFT_HYPERPARAMETERS = (
    "n_epochs",
    "batch_size",
    "learning_rate_multiplier",
)

SFT_METHOD_TYPE = "supervised"


def _has_content_or_tool_calls(msg: dict) -> bool:
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return True
    if isinstance(content, list) and content:
        return True
    tool_calls = msg.get("tool_calls")
    return isinstance(tool_calls, list) and bool(tool_calls)


def _validate_chat_messages(
    messages: object,
    *,
    split: str,
    require_assistant: bool = True,
    final_role: str | None = None,
) -> list[str]:
    problems: list[str] = []
    if not isinstance(messages, list) or not messages:
        return [f"{split}: no non-empty 'messages' list"]

    valid_roles = {"system", "developer", "user", "assistant", "tool"}
    has_user = False
    has_assistant = False
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            problems.append(f"{split}: message #{i + 1} is not an object")
            continue
        role = msg.get("role")
        if role not in valid_roles:
            problems.append(f"{split}: message #{i + 1} has invalid role {role!r}")
        has_user = has_user or role == "user"
        has_assistant = has_assistant or role == "assistant"
        if not _has_content_or_tool_calls(msg):
            problems.append(
                f"{split}: message #{i + 1} has no content or tool_calls"
            )

    if not has_user:
        problems.append(f"{split}: messages must include at least one user message")
    if require_assistant and not has_assistant:
        problems.append(f"{split}: messages must include at least one assistant message")
    if final_role is not None:
        last = messages[-1]
        role = last.get("role") if isinstance(last, dict) else None
        if role != final_role:
            problems.append(
                f"{split}: final message role must be {final_role!r}, got {role!r}"
            )
    return problems


def validate_sft_example(row: dict) -> list[str]:
    """Validate one supervised fine-tuning JSONL row."""
    if not isinstance(row, dict):
        return ["example is not a JSON object"]
    return _validate_chat_messages(
        row.get("messages"),
        split="example",
        require_assistant=True,
        final_role="assistant",
    )


def validate_sft_dataset(rows: Sequence[dict], *, split: str = "dataset") -> list[str]:
    """Validate every row of one SFT split."""
    if not rows:
        return [f"{split}: no examples"]
    problems: list[str] = []
    for i, row in enumerate(rows):
        for problem in validate_sft_example(row):
            problems.append(f"{split}[{i}]: {problem}")
    return problems


def validate_sft_splits(
    train: Sequence[dict],
    validation: Sequence[dict] | None = None,
) -> list[str]:
    """Validate SFT training and optional validation splits."""
    problems = validate_sft_dataset(train, split="training")
    if validation is not None:
        problems.extend(validate_sft_dataset(validation, split="validation"))
    return problems


def build_sft_method(*, hyperparameters: dict | None = None) -> dict:
    """Build the ``method`` block for supervised fine-tuning."""
    method: dict = {"type": SFT_METHOD_TYPE}
    if hyperparameters:
        unknown = sorted(set(hyperparameters) - set(SFT_HYPERPARAMETERS))
        if unknown:
            raise ValueError(
                f"unknown SFT hyperparameter(s): {', '.join(unknown)} "
                f"(known: {', '.join(SFT_HYPERPARAMETERS)})"
            )
        method["supervised"] = {"hyperparameters": dict(hyperparameters)}
    return method


def build_sft_job(
    *,
    model: str,
    training_file: str,
    validation_file: str | None = None,
    hyperparameters: dict | None = None,
    suffix: str | None = None,
    seed: int | None = None,
) -> dict:
    """Build ``fine_tuning.jobs.create`` kwargs for SFT."""
    job: dict = {
        "model": model,
        "training_file": training_file,
        "method": build_sft_method(hyperparameters=hyperparameters),
    }
    if validation_file:
        job["validation_file"] = validation_file
    if suffix is not None:
        job["suffix"] = suffix
    if seed is not None:
        job["seed"] = seed
    return job


def submit_sft_job(job: dict, *, endpoint: str | None = None):
    """Submit an SFT job through the shared fine-tuning seam."""
    from castia.finetuning.rft import _openai_client

    client = _openai_client(endpoint)
    return client.fine_tuning.jobs.create(**job)

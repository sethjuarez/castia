"""Unit tests for the RFT tooling (``castia.finetune``).

Hermetic: exercises the *pure* grader builders, offline validators, and the pure
payload builder against in-memory fixtures. Nothing here uploads a file or
submits a job -- :func:`submit_rft_job` / :func:`upload_file` are the impure
seams and are never called (a real RFT job is billable and gated).
"""

from __future__ import annotations

import json

import pytest

from castia.evalsuite import RubricDimension
from castia.finetune import (
    GRADER_TYPES,
    build_rft_job,
    build_rft_method,
    grader_item_fields,
    load_jsonl,
    multi_grader,
    python_grader,
    rubric_to_score_model,
    score_model_grader,
    string_check_grader,
    text_similarity_grader,
    validate_grader,
    validate_rft_dataset,
    validate_rft_example,
    validate_rft_splits,
)

# --------------------------------------------------------------------------- #
# Grader builders                                                             #
# --------------------------------------------------------------------------- #


def test_string_check_grader_shape_and_bad_operation():
    g = string_check_grader(
        "exact", input="{{ sample.output_text }}", reference="{{ item.answer }}"
    )
    assert g["type"] == "string_check"
    assert g["operation"] == "eq"
    assert validate_grader(g) == []

    with pytest.raises(ValueError):
        string_check_grader("x", input="a", reference="b", operation="matches")


def test_text_similarity_grader_metric_validation():
    g = text_similarity_grader(
        "sim",
        input="{{ sample.output_text }}",
        reference="{{ item.reference }}",
        evaluation_metric="rouge_l",
        pass_threshold=0.5,
    )
    assert g["evaluation_metric"] == "rouge_l"
    assert g["pass_threshold"] == 0.5
    assert validate_grader(g) == []

    with pytest.raises(ValueError):
        text_similarity_grader("x", input="a", reference="b", evaluation_metric="nope")


def test_score_model_grader_defaults_range():
    g = score_model_grader(
        "judge",
        model="gpt-4o",
        input=[{"role": "user", "content": "grade {{ sample.output_text }}"}],
    )
    assert g["range"] == [0.0, 1.0]
    assert validate_grader(g) == []


def test_multi_grader_validates_subgraders():
    good = multi_grader(
        "combo",
        graders={
            "acc": string_check_grader("acc", input="{{ sample.output_text }}", reference="{{ item.a }}"),
            "sim": text_similarity_grader("sim", input="{{ sample.output_text }}", reference="{{ item.a }}"),
        },
        calculate_output="0.7 * acc + 0.3 * sim",
    )
    assert validate_grader(good) == []

    bad = multi_grader(
        "combo",
        graders={"acc": {"type": "string_check", "name": "acc"}},  # missing fields
        calculate_output="acc",
    )
    problems = validate_grader(bad)
    assert any("sub-grader 'acc'" in p for p in problems)


# --------------------------------------------------------------------------- #
# Grader validation                                                          #
# --------------------------------------------------------------------------- #


def test_validate_grader_rejects_unknown_type_and_namespace():
    problems = validate_grader(
        {"type": "mystery", "name": "n", "input": "{{ foo.bar }}"}
    )
    assert any("not one of" in p for p in problems)
    assert any("unknown namespace" in p for p in problems)


def test_python_grader_ok():
    g = python_grader("py", source="def grade(sample, item):\n    return 1.0\n")
    assert validate_grader(g) == []
    assert "python" in GRADER_TYPES


def test_grader_item_fields_extracts_ground_truth_columns():
    g = score_model_grader(
        "judge",
        model="gpt-4o",
        input=[
            {"role": "system", "content": "ref {{ item.answer }} / {{ item.category }}"},
            {"role": "user", "content": "{{ sample.output_text }}"},
        ],
    )
    assert grader_item_fields(g) == {"answer", "category"}


# --------------------------------------------------------------------------- #
# rubric -> score_model bridge                                               #
# --------------------------------------------------------------------------- #


def test_rubric_to_score_model_bridge():
    dims = [
        RubricDimension(id="correct", description="Right outcome.", weight=0.7),
        RubricDimension(
            id="general", description="Overall quality.", always_applicable=True
        ),
    ]
    g = rubric_to_score_model(dims, model="gpt-4o", name="rubric_score")
    assert g["type"] == "score_model"
    assert g["name"] == "rubric_score"
    system = g["input"][0]["content"]
    assert "correct [weight 0.7]" in system
    assert "general [weight 1]" in system and "always applicable" in system
    assert validate_grader(g) == []

    with pytest.raises(ValueError):
        rubric_to_score_model([], model="gpt-4o")


# --------------------------------------------------------------------------- #
# Dataset validation                                                         #
# --------------------------------------------------------------------------- #


def _example(final_role="user", extra=None):
    row = {
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": final_role, "content": "the prompt"},
        ]
    }
    if extra:
        row.update(extra)
    return row


def test_validate_rft_example_requires_final_user_role():
    assert validate_rft_example(_example()) == []
    problems = validate_rft_example(_example(final_role="assistant"))
    assert any("final message role must be 'user'" in p for p in problems)


def test_validate_rft_example_flags_missing_messages_and_bad_role():
    assert any("no non-empty 'messages'" in p for p in validate_rft_example({}))
    bad = {"messages": [{"role": "wizard", "content": "x"}]}
    assert any("invalid role" in p for p in validate_rft_example(bad))


def test_validate_rft_dataset_cross_checks_grader_item_fields():
    grader = string_check_grader(
        "acc", input="{{ sample.output_text }}", reference="{{ item.answer }}"
    )
    rows = [_example(extra={"answer": "42"}), _example()]  # 2nd row missing `answer`
    problems = validate_rft_dataset(rows, grader=grader, split="training")
    assert any("training[1]" in p and "answer" in p for p in problems)
    # First row carries the field, so it is clean.
    assert not any("training[0]" in p for p in problems)


def test_validate_rft_splits_requires_both_and_ok_path():
    grader = string_check_grader(
        "acc", input="{{ sample.output_text }}", reference="{{ item.answer }}"
    )
    train = [_example(extra={"answer": "42"})]
    val = [_example(extra={"answer": "7"})]
    assert validate_rft_splits(train, val, grader=grader) == []
    # Empty validation split is rejected.
    problems = validate_rft_splits(train, [], grader=grader)
    assert any("validation: no examples" in p for p in problems)


# --------------------------------------------------------------------------- #
# Payload builder                                                            #
# --------------------------------------------------------------------------- #


def test_build_rft_method_rejects_unknown_hyperparameter():
    grader = string_check_grader("acc", input="{{ sample.output_text }}", reference="{{ item.a }}")
    method = build_rft_method(grader, hyperparameters={"reasoning_effort": "high"})
    assert method["type"] == "reinforcement"
    assert method["reinforcement"]["hyperparameters"]["reasoning_effort"] == "high"

    with pytest.raises(ValueError):
        build_rft_method(grader, hyperparameters={"learningrate": 0.1})


def test_build_rft_job_requires_validation_file():
    grader = string_check_grader("acc", input="{{ sample.output_text }}", reference="{{ item.a }}")
    job = build_rft_job(
        model="o4-mini",
        training_file="file-train",
        validation_file="file-val",
        grader=grader,
        hyperparameters={"reasoning_effort": "medium"},
        suffix="rft1",
    )
    assert job["model"] == "o4-mini"
    assert job["training_file"] == "file-train"
    assert job["validation_file"] == "file-val"
    assert job["method"]["reinforcement"]["grader"]["type"] == "string_check"
    assert job["suffix"] == "rft1"

    with pytest.raises(ValueError):
        build_rft_job(
            model="o4-mini", training_file="file-train", validation_file="", grader=grader
        )


def test_load_jsonl_roundtrip(tmp_path):
    p = tmp_path / "train.jsonl"
    p.write_text(
        "\n".join(json.dumps(_example(extra={"answer": str(i)})) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    rows = load_jsonl(p)
    assert len(rows) == 3
    assert rows[0]["answer"] == "0"

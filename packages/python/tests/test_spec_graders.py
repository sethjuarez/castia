"""Conformance test: the RFT grader-schema fixture.

`spec/conformance/graders/grader_schema.json` is the language-neutral golden for
the reinforcement fine-tuning reward-function schema -- the grader types,
template namespaces, hyperparameter names, and dataset rules every castia SDK
must reproduce. This test locks `castia.finetune`'s constants and validators to
that fixture so the SDK and the documented contract cannot silently drift.

The fixture is stamped PROVISIONAL/doc-derived: it encodes the documented shape,
not a live-validated wire shape. This test asserts *consistency with the doc*,
not that a live RFT job accepts the payload.
"""

from __future__ import annotations

import json
from pathlib import Path

from castia.finetune import (
    _TEMPLATE_NS,
    GRADER_TYPES,
    RFT_HYPERPARAMETERS,
    STRING_CHECK_OPS,
    TEXT_SIMILARITY_METRICS,
    string_check_grader,
    validate_grader,
    validate_rft_example,
    validate_rft_splits,
)

# The language-neutral golden lives in the monorepo's spec/ tree; the SDK docs,
# the finetune module, and this test read the same fixture so the contract
# cannot drift.
SPEC_GRADERS = (
    Path(__file__).resolve().parents[3]
    / "spec"
    / "conformance"
    / "graders"
    / "grader_schema.json"
)


def _load() -> dict:
    return json.loads(SPEC_GRADERS.read_text(encoding="utf-8"))


def test_fixture_exists_and_is_stamped_provisional():
    data = _load()
    assert data["contract"] == "foundry-rft-grader-schema"
    # The wire shape is doc-derived until a live RFT job confirms it -- the
    # fixture must say so out loud.
    assert data["status"] == "provisional"
    assert "doc-derived" in data["source"]


def test_grader_types_match_the_sdk():
    ids = [g["id"] for g in _load()["grader_types"]]
    assert ids == list(GRADER_TYPES)


def test_string_check_operations_match_the_sdk():
    entry = next(g for g in _load()["grader_types"] if g["id"] == "string_check")
    assert entry["operations"] == list(STRING_CHECK_OPS)


def test_text_similarity_metrics_match_the_sdk():
    entry = next(g for g in _load()["grader_types"] if g["id"] == "text_similarity")
    assert entry["metrics"] == list(TEXT_SIMILARITY_METRICS)


def test_template_namespaces_match_the_sdk():
    assert _load()["template_namespaces"] == list(_TEMPLATE_NS)


def test_hyperparameters_match_the_sdk():
    hp = _load()["hyperparameters"]
    # The SDK tuple is the rft-specific knobs followed by the sft-shared ones.
    assert hp["rft_specific"] + hp["sft_shared"] == list(RFT_HYPERPARAMETERS)
    assert hp["reasoning_effort_levels"] == ["low", "medium", "high"]


def test_per_type_required_fields_are_enforced_by_the_validator():
    # For every grader type, a bare {type, name} dict must be flagged as missing
    # exactly the required fields the fixture declares.
    for entry in _load()["grader_types"]:
        gtype, required = entry["id"], entry["required"]
        problems = validate_grader({"type": gtype, "name": "n"})
        for field in required:
            assert any(
                field in p and "missing required field" in p for p in problems
            ), f"{gtype}: expected a missing-field problem for {field!r}"


def test_dataset_final_user_role_rule_is_enforced():
    rules = " ".join(_load()["dataset"]["rules"]).lower()
    assert "final message role must be user" in rules

    good = {"messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "q"}]}
    assert validate_rft_example(good) == []
    bad = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}
    assert any("final message role must be 'user'" in p for p in validate_rft_example(bad))


def test_dataset_requires_both_splits():
    rules = " ".join(_load()["dataset"]["rules"]).lower()
    assert "both a training split and a validation split are required" in rules

    grader = string_check_grader(
        "acc", input="{{ sample.output_text }}", reference="{{ item.a }}"
    )
    train = [{"messages": [{"role": "user", "content": "q"}], "a": "1"}]
    assert any(
        "validation: no examples" in p
        for p in validate_rft_splits(train, [], grader=grader)
    )

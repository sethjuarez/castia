"""Pure, offline tests for the eval-suite tooling (:mod:`castia.evalsuite`).

No Azure, no credentials, no ``azd``: the argv builders are pure, ``read_rubric``
/ ``validate_suite`` are filesystem-only, and the one impure seam (``run_azd``)
is exercised only through its ``shutil.which`` guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castia import evalsuite
from castia.evalsuite import (
    build_generate_argv,
    build_run_argv,
    build_update_argv,
    load_suite,
    read_rubric,
    run_azd,
    validate_suite,
)

# The language-neutral golden lives in the monorepo's spec/ tree; the SDK reads
# the same fixture so the Python reader and the cross-SDK spec cannot drift.
SPEC_RUBRIC = (
    Path(__file__).resolve().parents[3]
    / "spec"
    / "conformance"
    / "rubric"
    / "rubric_dimensions.json"
)


# --------------------------------------------------------------------------- #
# argv builders (pure)                                                        #
# --------------------------------------------------------------------------- #


def test_generate_argv_minimal_is_just_the_verb():
    assert build_generate_argv(out_file="") == ["azd", "ai", "agent", "eval", "generate"]


def test_generate_argv_emits_only_set_flags_and_repeats_evaluators():
    argv = build_generate_argv(
        agent="hal",
        gen_instruction_file=".agent_configs/baseline/instructions.md",
        eval_model="gpt-4o",
        max_samples=15,
        evaluators=["builtin.task_adherence", "smoke-core"],
        out_file="eval.generated.yaml",
        reset_defaults=True,
        no_wait=True,
        no_prompt=True,
    )
    assert argv[:5] == ["azd", "ai", "agent", "eval", "generate"]
    assert argv.count("--evaluator") == 2
    assert argv[argv.index("--agent") + 1] == "hal"
    assert argv[argv.index("--max-samples") + 1] == "15"
    assert argv[argv.index("--out-file") + 1] == "eval.generated.yaml"
    assert "--reset-defaults" in argv and "--no-wait" in argv and "--no-prompt" in argv
    # Unset flags are omitted entirely.
    assert "--dataset" not in argv and "--trace-days" not in argv


def test_update_argv_flags():
    argv = build_update_argv(config="eval.generated.yaml", evaluator_only=True, no_prompt=True)
    assert argv[:5] == ["azd", "ai", "agent", "eval", "update"]
    assert argv[argv.index("--config") + 1] == "eval.generated.yaml"
    assert "--evaluator-only" in argv and "--no-prompt" in argv
    assert "--dataset-only" not in argv


def test_run_argv_flags():
    argv = build_run_argv(config="eval.yaml", name="nightly", no_wait=True)
    assert argv[:5] == ["azd", "ai", "agent", "eval", "run"]
    assert argv[argv.index("--name") + 1] == "nightly"
    assert "--no-wait" in argv


# --------------------------------------------------------------------------- #
# read_rubric -- the id-keyed bare-list shape                                 #
# --------------------------------------------------------------------------- #


def test_read_rubric_bare_list_keyed_by_id():
    dims = read_rubric(SPEC_RUBRIC)
    assert [d.id for d in dims][:2] == ["correct_outcome", "safety_compliance"]
    assert dims[0].weight == 10
    # The trailing catch-all carries always_applicable.
    catchall = dims[-1]
    assert catchall.id == "general_quality"
    assert catchall.always_applicable is True
    assert all(d.always_applicable is False for d in dims[:-1])


def test_read_rubric_accepts_name_as_legacy_alias_but_prefers_id(tmp_path: Path):
    p = tmp_path / "legacy.json"
    p.write_text(
        json.dumps(
            [
                {"name": "legacy_slug", "description": "d", "weight": 1},
                {"id": "wins", "name": "loses", "description": "d"},
            ]
        ),
        encoding="utf-8",
    )
    dims = read_rubric(p)
    assert dims[0].id == "legacy_slug"  # name used when id absent
    assert dims[1].id == "wins"  # id preferred over name
    assert dims[1].weight is None  # weight optional


def test_read_rubric_accepts_dimensions_wrapper(tmp_path: Path):
    p = tmp_path / "wrapped.json"
    p.write_text(
        json.dumps({"dimensions": [{"id": "only", "description": "d", "weight": 2}]}),
        encoding="utf-8",
    )
    dims = read_rubric(p)
    assert [d.id for d in dims] == ["only"]


# --------------------------------------------------------------------------- #
# validate_suite -- offline referential-integrity gate                        #
# --------------------------------------------------------------------------- #


def _write_suite(tmp_path: Path, rubric_rel: str = "rubric.json") -> Path:
    (tmp_path / rubric_rel).write_text(SPEC_RUBRIC.read_text(encoding="utf-8"), encoding="utf-8")
    cfg = tmp_path / "eval.yaml"
    cfg.write_text(
        "agent:\n"
        "  name: hal\n"
        "  model: gpt-4o\n"
        "evaluators:\n"
        "  - name: smoke-core\n"
        "    version: '1'\n"
        f"    local_uri: {rubric_rel}\n"
        "options:\n"
        "  eval_model: gpt-4o\n",
        encoding="utf-8",
    )
    return cfg


def test_validate_suite_ok_when_rubric_resolves(tmp_path: Path):
    cfg = _write_suite(tmp_path)
    plan = validate_suite(load_suite(cfg))
    assert plan.ok
    assert plan.problems == []
    assert "smoke-core" in plan.rubrics
    assert len(plan.rubrics["smoke-core"]) == 7


def test_validate_suite_flags_missing_rubric_file(tmp_path: Path):
    cfg = _write_suite(tmp_path, rubric_rel="rubric.json")
    (tmp_path / "rubric.json").unlink()
    plan = validate_suite(load_suite(cfg))
    assert not plan.ok
    assert any("does not exist" in p for p in plan.problems)


def test_validate_suite_notes_when_no_evaluators(tmp_path: Path):
    cfg = tmp_path / "eval.yaml"
    cfg.write_text("agent:\n  name: hal\n", encoding="utf-8")
    plan = validate_suite(load_suite(cfg))
    assert plan.ok  # a note, not a problem
    assert any("no evaluators declared" in n for n in plan.notes)


# --------------------------------------------------------------------------- #
# run_azd -- the impure seam's PATH guard                                     #
# --------------------------------------------------------------------------- #


def test_run_azd_raises_clean_error_when_azd_missing(monkeypatch):
    monkeypatch.setattr(evalsuite.shutil, "which", lambda _exe: None)
    with pytest.raises(FileNotFoundError, match="not on PATH"):
        run_azd(["azd", "ai", "agent", "eval", "run"])

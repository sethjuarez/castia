"""Pure, offline tests for the eval-suite tooling (:mod:`castia.evaluation.suite`).

No Azure, no credentials, no ``azd``: the argv builders are pure, ``read_rubric``
/ ``validate_suite`` are filesystem-only, and the one impure seam (``run_azd``)
is exercised only through monkeypatched ``shutil.which``/``subprocess.run``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from castia.evaluation import suite as evalsuite
from castia.evaluation.suite import (
    RubricDimension,
    build_generate_argv,
    build_run_argv,
    build_update_argv,
    compile_gates,
    load_suite,
    read_rubric,
    rubric_to_wire,
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


# --------------------------------------------------------------------------- #
# gate=True rubric dimensions + gold-aware template                           #
# --------------------------------------------------------------------------- #


def _rubric_score(dims, scores):
    total = sum(d.weight for d in dims)
    return sum(d.weight * scores[d.id] for d in dims) / total


def test_read_rubric_parses_gate_flag(tmp_path: Path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps([
        {"id": "gold", "description": "d", "gate": True},
        {"id": "other", "description": "d", "weight": 1},
    ]), encoding="utf-8")
    dims = read_rubric(p)
    assert [d.gate for d in dims] == [True, False]


@pytest.mark.parametrize("threshold,gates", [(0.5, 1), (0.7, 2), (0.8, 3)])
def test_compile_gates_fails_rubric_when_any_gate_fails(threshold, gates):
    dims = [RubricDimension(id=f"g{i}", description="d", gate=True) for i in range(gates)]
    dims += [RubricDimension(id=f"o{i}", description="d", weight=w) for i, w in enumerate([1, 3, 2])]
    compiled = compile_gates(dims, pass_threshold=threshold)
    assert not any(d.gate for d in compiled)
    perfect = {d.id: 1.0 for d in compiled}
    assert _rubric_score(compiled, perfect) >= threshold
    for i in range(gates):
        assert _rubric_score(compiled, {**perfect, f"g{i}": 0.0}) < threshold


def test_compile_gates_keeps_larger_declared_weight_and_defaults_others():
    dims = [
        RubricDimension(id="g", description="d", weight=50, gate=True),
        RubricDimension(id="o", description="d"),
    ]
    compiled = compile_gates(dims)
    assert [d.weight for d in compiled] == [50.0, 1.0]


def test_compile_gates_shares_the_heaviest_declared_weight_across_gates():
    dims = [
        RubricDimension(id="heavy", description="d", weight=100, gate=True),
        RubricDimension(id="light", description="d", gate=True),
        RubricDimension(id="o", description="d"),
    ]
    compiled = compile_gates(dims, pass_threshold=0.75)
    assert [d.weight for d in compiled] == [100.0, 100.0, 1.0]
    perfect = {d.id: 1.0 for d in compiled}
    assert _rubric_score(compiled, {**perfect, "light": 0.0}) < 0.75


def test_compile_gates_rejects_unenforceable_threshold():
    dims = [RubricDimension(id=f"g{i}", description="d", gate=True) for i in range(2)]
    with pytest.raises(ValueError, match="raise pass_threshold above 0.500"):
        compile_gates(dims, pass_threshold=0.5)


def test_rubric_to_wire_refuses_uncompiled_gate():
    with pytest.raises(ValueError, match="compile_gates"):
        rubric_to_wire([RubricDimension(id="g", description="d", gate=True)])


def test_validate_suite_flags_uncompiled_gate(tmp_path: Path):
    (tmp_path / "eval.yaml").write_text(
        "evaluators:\n  - name: gold\n    version: '1'\n    local_uri: src.json\n",
        encoding="utf-8",
    )
    (tmp_path / "src.json").write_text(
        json.dumps([{"id": "gold", "description": "d", "gate": True}]), encoding="utf-8"
    )
    plan = validate_suite(load_suite(tmp_path / "eval.yaml"))
    assert not plan.ok
    assert "uncompiled gate" in plan.problems[0]


def test_validate_suite_notes_rubric_ground_truth_for_optimizer(tmp_path: Path):
    (tmp_path / "eval.yaml").write_text(
        "evaluators:\n  - name: gold\n    version: '1'\n    local_uri: gold.json\n"
        "  - name: plain\n    version: '1'\n    local_uri: plain.json\n",
        encoding="utf-8",
    )
    (tmp_path / "gold.json").write_text(
        json.dumps([{"id": "g", "description": "Agrees with {{ item.ground_truth }}"}]),
        encoding="utf-8",
    )
    (tmp_path / "plain.json").write_text(
        json.dumps([{"id": "c", "description": "Clear"}]), encoding="utf-8"
    )
    plan = validate_suite(load_suite(tmp_path / "eval.yaml"))
    assert plan.ok
    assert len(plan.notes) == 1
    assert plan.notes[0].startswith("evaluator 'gold': rubric references {{item.ground_truth}}")
    assert "register the gold check as a prompt evaluator" in plan.notes[0]


def test_gold_aware_template_round_trips_through_check(tmp_path: Path):
    from castia.evaluation.templates import write_gold_aware_template

    paths = write_gold_aware_template(tmp_path / "evaluators")
    names = {p.name for p in paths}
    assert names == {
        "gold_rubric.source.json", "gold_rubric.json",
        "gold_string_check.json", "gold_sample.jsonl",
    }
    check = json.loads((tmp_path / "evaluators" / "gold_string_check.json").read_text())
    assert check["reference"] == "{{item.ground_truth}}"
    rows = [json.loads(line) for line in
            (tmp_path / "evaluators" / "gold_sample.jsonl").read_text().splitlines()]
    assert all(set(r) == {"query", "ground_truth"} for r in rows)

    (tmp_path / "eval.yaml").write_text(
        "evaluators:\n  - name: gold\n    version: '1'\n"
        "    local_uri: evaluators/gold_rubric.json\n",
        encoding="utf-8",
    )
    plan = validate_suite(load_suite(tmp_path / "eval.yaml"))
    assert plan.ok, plan.problems
    compiled = plan.rubrics["gold"]
    perfect = {d.id: 1.0 for d in compiled}
    assert _rubric_score(compiled, {**perfect, "matches_ground_truth": 0.0}) < 0.5

    with pytest.raises(FileExistsError):
        write_gold_aware_template(tmp_path / "evaluators")


def test_cli_rubric_compile_writes_wire_file(tmp_path: Path, capsys):
    from castia.__main__ import main

    src = tmp_path / "src.json"
    src.write_text(json.dumps([
        {"id": "gold", "description": "d", "gate": True},
        {"id": "o", "description": "d", "weight": 4},
    ]), encoding="utf-8")
    out = tmp_path / "out.json"
    assert main(["eval", "rubric", "compile", str(src), "--out", str(out)]) == 0
    wire = json.loads(out.read_text())
    assert wire == [
        {"id": "gold", "description": "d", "weight": 5},
        {"id": "o", "description": "d", "weight": 4},
    ]
    assert "(gate)" in capsys.readouterr().out


def test_run_azd_sets_agents_opt_ins_without_overriding_user_values(monkeypatch):
    seen = {}

    def fake_run(argv, *, cwd, env, check, stdout=None):
        seen.update(env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(evalsuite.shutil, "which", lambda _exe: "azd")
    monkeypatch.setattr(evalsuite.subprocess, "run", fake_run)
    monkeypatch.setenv("AZD_ALPHA_ENABLE_AGENTS_VOICE", "false")
    monkeypatch.delenv("AZD_ALPHA_ENABLE_AGENTS_LONG_RUNNING", raising=False)
    monkeypatch.delenv("AZD_ALPHA_ENABLE_AGENTS_RBAC_WARNINGS", raising=False)

    assert run_azd(["azd", "ai", "agent", "eval", "run"]) == 0
    assert seen["AZD_ALPHA_ENABLE_AGENTS_LONG_RUNNING"] == "true"
    assert seen["AZD_ALPHA_ENABLE_AGENTS_RBAC_WARNINGS"] == "true"
    assert seen["AZD_ALPHA_ENABLE_AGENTS_VOICE"] == "false"

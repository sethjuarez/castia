from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from castia.__main__ import main
from castia.evaluation import azd_eval
from castia.evaluation.azd_eval import (
    EvalPortError,
    build_dataset_update_plan,
    build_generate_argv,
    build_init_argv,
    build_run_start_argv,
    check_drift,
    discover_extensions,
    load_eval_definitions,
    resolve_backend,
    select_backend,
    select_eval,
)
from castia.evaluation.suite import load_suite

EVAL_YAML = """\
name: demo
agent: {name: my-agent, model: gpt-4o}
options: {eval_model: gpt-4o-mini}
evaluators: [builtin.task_adherence, builtin.intent_resolution]
dataset: {local_uri: data/cases.jsonl}
"""

AZURE_EVAL_YAML = """\
datasets:
  - {name: data, file: ../data/cases.jsonl}
evals:
  - name: my-agent-eval
    dataset: data
    evaluationLevel: turn
    evaluators:
      - {evaluator: builtin.task_adherence, initializationParameters: {model: gpt-4o-mini}}
      - {evaluator: builtin.intent_resolution, initializationParameters: {model: gpt-4o-mini}}
    target: {type: agent, name: my-agent}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cases.jsonl").write_text('{"query": "hi"}\n', encoding="utf-8")
    (tmp_path / "eval.yaml").write_text(EVAL_YAML, encoding="utf-8")
    (tmp_path / "azure.yaml").write_text("name: demo\n", encoding="utf-8")
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "azure.eval.yaml").write_text(AZURE_EVAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(azd_eval.BACKEND_ENV, raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("extensions", "expected"),
    [
        ([{"id": "azure.ai.evaluations", "version": "1.0.0-beta.2"}], "evaluations"),
        (
            [
                {"id": "azure.ai.agents", "version": "1.0.0-beta.23"},
                {"id": "azure.ai.evaluations", "version": "1.0.0-beta.2"},
            ],
            "evaluations",
        ),
        ([{"id": "azure.ai.agents", "version": "1.0.0-beta.21"}], "agent"),
        ([{"id": "azure.ai.agents", "version": "0.9.0"}], "agent"),
    ],
)
def test_select_backend(extensions, expected):
    assert select_backend(extensions) == expected


@pytest.mark.parametrize(
    "extensions",
    [[], [{"id": "azure.ai.agents", "version": "1.0.0-beta.22"}],
     [{"id": "azure.ai.agents", "version": "1.2.0"}]],
)
def test_select_backend_without_a_usable_extension_explains_install(extensions):
    with pytest.raises(EvalPortError, match="azure.ai.evaluations"):
        select_backend(extensions)


def test_discover_extensions_raises_on_failure_or_bad_json():
    def failing(argv, **_):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    def garbage(argv, **_):
        return subprocess.CompletedProcess(argv, 0, "not json", "")

    with pytest.raises(EvalPortError, match="boom"):
        discover_extensions(failing)
    with pytest.raises(EvalPortError):
        discover_extensions(garbage)


def test_explicit_backend_skips_discovery(monkeypatch):
    def explode():
        raise AssertionError("discovery should not run")

    monkeypatch.delenv(azd_eval.BACKEND_ENV, raising=False)
    assert resolve_backend("agent", discover=explode) == "agent"
    monkeypatch.setenv(azd_eval.BACKEND_ENV, "evaluations")
    assert resolve_backend(None, discover=explode) == "evaluations"
    with pytest.raises(EvalPortError, match="unknown eval backend"):
        resolve_backend("legacy", discover=explode)


def test_init_argv_derives_flags_from_eval_yaml(project: Path):
    argv = build_init_argv(load_suite("eval.yaml"), name="smoke")
    assert argv == [
        "azd", "ai", "eval", "init", "--source", "dataset",
        "--dataset", str(Path("data/cases.jsonl")),
        "--target", "my-agent",
        "--judge-model", "gpt-4o-mini",
        "--evaluator", "builtin.task_adherence,builtin.intent_resolution",
        "--name", "smoke",
        "--path", "evals",
        "--no-prompt",
    ]


def test_init_rejects_custom_evaluators(project: Path):
    (project / "eval.yaml").write_text(
        EVAL_YAML.replace(
            "evaluators: [builtin.task_adherence, builtin.intent_resolution]",
            "evaluators: [{name: rubric, local_uri: rubric.yaml}]",
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvalPortError, match="custom evaluators are not converted: rubric"):
        build_init_argv(load_suite("eval.yaml"))


def test_select_eval_requires_a_choice_when_several_are_declared():
    defs = {"evals": [{"name": "a"}, {"name": "b"}]}
    assert select_eval(defs, "b") == "b"
    with pytest.raises(EvalPortError, match="--eval"):
        select_eval(defs)
    with pytest.raises(EvalPortError, match="not declared"):
        select_eval(defs, "c")
    assert select_eval({"evals": [{"name": "only"}]}) == "only"


def test_run_start_argv_validates_fail_on():
    argv = build_run_start_argv(eval_name="e", fail_on="pass-rate=0.8", max_samples=5)
    assert argv[:4] == ["azd", "ai", "eval", "run"]
    assert ["--fail-on", "pass-rate=0.8"] == argv[argv.index("--fail-on"):][:2]
    for bad in ("pass-rate=1.5", "sometimes", "pass-rate="):
        with pytest.raises(EvalPortError):
            build_run_start_argv(eval_name="e", fail_on=bad)


def test_generate_argv_maps_artifact_and_names():
    argv = build_generate_argv(target="a", generation_model="m", artifact="evaluator",
                               evaluator_name="rubric", force=True)
    assert argv == ["azd", "ai", "eval", "generate", "--target", "a", "--generation-model", "m",
                    "--evaluator", "--evaluator-name", "rubric", "--path", "evals", "--force"]


def test_dataset_update_plan_preflights_every_file(project: Path):
    defs = load_eval_definitions("evals")
    plan = build_dataset_update_plan(defs)
    assert plan[0][:6] == ["azd", "ai", "eval", "dataset", "update", "data"]
    defs["datasets"].append({"name": "gone", "file": "missing.jsonl"})
    with pytest.raises(EvalPortError, match="gone"):
        build_dataset_update_plan(defs)


def test_check_drift_reports_target_evaluator_and_dataset_mismatch(project: Path):
    suite = load_suite("eval.yaml")
    defs = load_eval_definitions("evals")
    assert check_drift(suite, defs) == []

    defs["evals"][0]["target"]["name"] = "other-agent"
    defs["evals"][0]["evaluators"].pop()
    defs["datasets"][0]["file"] = "elsewhere.jsonl"
    problems = check_drift(suite, defs)
    assert len(problems) == 3
    assert "other-agent" in problems[0]


def test_cli_check_fails_on_drift(project: Path, capsys):
    assert main(["eval", "check"]) == 0
    (project / "evals" / "azure.eval.yaml").write_text(
        AZURE_EVAL_YAML.replace("name: my-agent", "name: renamed"), encoding="utf-8"
    )
    assert main(["eval", "check"]) == 1
    assert "renamed" in capsys.readouterr().out


def test_cli_run_dry_run_deploys_then_starts(project: Path, capsys):
    rc = main(["eval", "run", "--backend", "evaluations", "--deploy",
               "--fail-on", "any-failure", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    commands = [line for line in out.splitlines() if line.startswith("command")]
    assert "azd ai eval create my-agent-eval --path evals" in commands[0]
    assert "azd ai eval run start --path evals --eval my-agent-eval --fail-on any-failure" in commands[1]



def test_cli_run_json_reports_the_plan_as_one_envelope(project: Path, capsys):
    import json

    rc = main(["eval", "run", "--backend", "evaluations", "--rung", "smoke", "--deploy",
               "--dry-run", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    env = json.loads(captured.out)
    assert env["command"] == "eval run" and env["status"] == "dry_run"
    create, start = env["result"]["argv"]
    assert create[:4] == ["azd", "ai", "eval", "create"]
    assert start[:5] == ["azd", "ai", "eval", "run", "start"]
    assert "smoke · n1" in start
    assert "backend       : evaluations" in captured.err

def test_cli_rejects_flags_for_the_other_backend(project: Path, capsys):
    assert main(["eval", "run", "--backend", "agent", "--deploy", "--dry-run"]) == 2
    assert main(["eval", "generate", "--backend", "evaluations", "--dataset", "x.jsonl",
                 "--dry-run"]) == 2
    assert main(["eval", "update", "--backend", "evaluations", "--evaluator-only",
                 "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "--deploy" in err and "--dataset" in err and "evaluator update" in err


def test_cli_legacy_run_keeps_old_argv(project: Path, capsys):
    assert main(["eval", "run", "--backend", "agent", "--dry-run"]) == 0
    assert "azd ai agent eval run --config eval.yaml" in capsys.readouterr().out

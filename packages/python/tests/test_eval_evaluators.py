"""Offline tests for evaluator export/import, content hashing and pinning."""

from __future__ import annotations

import json

import pytest

from castia.evaluation import foundry
from castia.evaluation.evaluators import (
    HASH_TAG,
    EvaluatorBundle,
    content_hash,
    export_evaluator,
    find_unpinned_evaluators,
    import_evaluator,
    pin_evaluator_version,
)
from castia.evaluation.suite import load_suite

ENDPOINT = "https://example.ai.azure.com/api/projects/p"

RUBRIC = {
    "evaluator_type": "custom",
    "categories": ["quality"],
    "definition": {"type": "rubric", "dimensions": [{"id": "a", "description": "d"}]},
}


def service_version(version, **extra):
    return {"id": f"x/{version}", "name": "gold", "version": str(version), **RUBRIC, **extra}


class FakeClient:
    def __init__(self, versions=None, echo_definition=True):
        self.versions = list(versions or [])
        self.created: list[dict] = []
        self.echo_definition = echo_definition

    def list_evaluator_versions(self, name):
        return list(self.versions)

    def get_evaluator_version(self, name, version):
        return next(v for v in self.versions if v["version"] == str(version))

    def create_evaluator_version(self, name, body):
        self.created.append(body)
        out = {"name": name, "version": str(len(self.versions) + 1), "tags": body["tags"]}
        if self.echo_definition:
            out.update({k: v for k, v in body.items() if k != "tags"})
        self.versions.append(out)
        return out


def test_content_hash_ignores_cosmetic_fields_and_key_order():
    a = content_hash(RUBRIC)
    b = content_hash(
        {
            "definition": {"dimensions": [{"description": "d", "id": "a"}], "type": "rubric"},
            "categories": ["quality"],
            "evaluator_type": "custom",
            "display_name": "Renamed",
            "tags": {"x": "y"},
            "version": "9",
        }
    )
    assert a == b and a.startswith("sha256:")
    changed = {**RUBRIC, "definition": {**RUBRIC["definition"], "pass_threshold": 0.7}}
    assert content_hash(changed) != a


def test_export_latest_writes_hash_named_bundle(tmp_path):
    client = FakeClient([service_version(2, display_name="Gold"), service_version(10)])
    bundle = export_evaluator(client, "gold")
    assert bundle.version == "10"
    path = bundle.write(tmp_path)
    assert path.name == f"gold@{bundle.content_hash[7:19]}.json"
    loaded = EvaluatorBundle.load(path)
    assert loaded == bundle
    assert "id" not in loaded.body


def test_load_rejects_edited_bundle(tmp_path):
    path = EvaluatorBundle.from_service("gold", service_version(1)).write(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["body"]["definition"]["type"] = "prompt"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="edited after export"):
        EvaluatorBundle.load(path)


def test_import_reuses_matching_version():
    bundle = EvaluatorBundle.from_service("gold", service_version(1))
    client = FakeClient([service_version(3, display_name="other name")])
    result = import_evaluator(client, bundle)
    assert (result.version, result.created) == ("3", False)
    assert client.created == []


def test_import_trusts_content_over_a_stale_tag():
    bundle = EvaluatorBundle.from_service("gold", service_version(1))
    edited = service_version(2, tags={HASH_TAG: bundle.content_hash})
    edited["definition"] = {"type": "rubric", "dimensions": []}
    client = FakeClient([edited])
    result = import_evaluator(client, bundle)
    assert (result.version, result.created) == ("2", True)


def test_import_creates_tagged_version_and_verifies_hash():
    bundle = EvaluatorBundle.from_service("gold", service_version(1))
    client = FakeClient([])
    result = import_evaluator(client, bundle, name="gold-copy")
    assert (result.name, result.version, result.created) == ("gold-copy", "1", True)
    assert client.created[0]["tags"][HASH_TAG] == bundle.content_hash
    # Second import is idempotent via the tag when listings omit definitions.
    client.versions = [{"version": "1", "tags": {HASH_TAG: bundle.content_hash}}]
    assert import_evaluator(client, bundle).created is False


def test_import_raises_when_service_rewrites_definition():
    bundle = EvaluatorBundle.from_service("gold", service_version(1))

    class Rewriting(FakeClient):
        def create_evaluator_version(self, name, body):
            return {**body, "version": "1", "definition": {"type": "rubric"}}

    with pytest.raises(RuntimeError, match="rewrote the definition"):
        import_evaluator(Rewriting([]), bundle)


EVAL_YAML = """\
name: suite  # keep me
evaluators:
  - builtin.task_adherence
  - gold
  - name: style
    local_uri: style.json
  - name: tone
    version: 2
"""


def test_find_unpinned_and_pin(tmp_path):
    cfg = tmp_path / "eval.yaml"
    cfg.write_text(EVAL_YAML, encoding="utf-8")
    assert find_unpinned_evaluators(load_suite(cfg)) == ["gold", "style"]
    assert pin_evaluator_version(cfg, "gold", "4")
    assert pin_evaluator_version(cfg, "style", "7")
    assert not pin_evaluator_version(cfg, "missing", "1")
    text = cfg.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert find_unpinned_evaluators(load_suite(cfg)) == []
    versions = {e.name: e.version for e in load_suite(cfg).evaluators}
    assert versions == {"builtin.task_adherence": None, "gold": "4", "style": "7", "tone": "2"}


def test_check_require_pinned_and_run_refuse_unpinned(tmp_path, capsys):
    from castia.__main__ import main

    cfg = tmp_path / "eval.yaml"
    cfg.write_text("name: s\nevaluators:\n  - gold\n", encoding="utf-8")
    assert main(["eval", "check", "--config", str(cfg), "--require-pinned"]) == 1
    assert "evaluator 'gold' has no version" in capsys.readouterr().out

    run = ["eval", "run", "--config", str(cfg), "--rung", "b", "--rows", "5", "--dry-run"]
    assert main(run) == 2
    assert "unpinned evaluators: gold" in capsys.readouterr().err
    assert main([*run, "--allow-unpinned"]) == 0
    assert "not reproducible" in capsys.readouterr().err


def test_import_cli_pins_version(tmp_path, monkeypatch, capsys):
    from castia.__main__ import main

    bundle_path = EvaluatorBundle.from_service("gold", service_version(1)).write(tmp_path)
    cfg = tmp_path / "eval.yaml"
    cfg.write_text("name: s\nevaluators:\n  - gold\n", encoding="utf-8")
    fake = FakeClient([service_version(5)])
    monkeypatch.setattr(foundry, "FoundryEvalClient", lambda endpoint: fake)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", ENDPOINT)
    rc = main(["eval", "evaluator", "import", str(bundle_path), "--pin-version",
               "--config", str(cfg)])
    assert rc == 0
    assert "gold v5 (reused)" in capsys.readouterr().out
    assert load_suite(cfg).evaluators[0].version == "5"


def test_client_evaluator_paths(monkeypatch):
    calls = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body
            self.headers = {}

        def json(self):
            return self.body

    replies = [
        Resp({"value": [{"version": "1"}], "nextLink": f"{ENDPOINT}/evaluators/g/versions?api-version=v1&skip=1"}),
        Resp({"value": [{"version": "2"}]}),
        Resp({"version": "3"}),
    ]

    class FakePipelineClient:
        def __init__(self, *, base_url, policies):
            pass

        def send_request(self, request):
            calls.append((request.method, request.url))
            return replies.pop(0)

    monkeypatch.setattr("azure.core.PipelineClient", FakePipelineClient)
    client = foundry.FoundryEvalClient(ENDPOINT, credential=object())
    assert [v["version"] for v in client.list_evaluator_versions("g")] == ["1", "2"]
    assert client.create_evaluator_version("g", {"definition": {}})["version"] == "3"
    assert calls == [
        ("GET", f"{ENDPOINT}/evaluators/g/versions?api-version=v1"),
        ("GET", f"{ENDPOINT}/evaluators/g/versions?api-version=v1&skip=1"),
        ("POST", f"{ENDPOINT}/evaluators/g/versions?api-version=v1"),
    ]

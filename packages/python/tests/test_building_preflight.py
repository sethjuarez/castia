"""Readiness is a local, redacted, read-only gate, never a cloud health claim."""

import json
import sys

import pytest

from castia.building import preflight, scaffold_project


def statuses(report):
    return {item.check: item.status for item in report.diagnostics}


def test_missing_configuration_is_explicit_and_values_never_serialized(tmp_path):
    scaffold_project(tmp_path)
    report = preflight(tmp_path, environment={"FOUNDRY_PROJECT_ENDPOINT": "SECRET-TOKEN"})
    assert not report.ok
    assert statuses(report)["configuration.AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "fail"
    assert statuses(report)["configuration.FOUNDRY_PROJECT_ENDPOINT"] == "pass"
    assert statuses(report)["cloud"] == "skipped"
    assert "SECRET-TOKEN" not in json.dumps(report.to_dict())


@pytest.mark.parametrize("source", [
    "app = 1",
    "from castia import Agent\napp = Agent()",
    "raise RuntimeError('TOP-SECRET-CREDENTIAL')",
    "import socket\nsocket.create_connection(('example.com', 443))",
])
def test_bad_registration_or_remote_import_side_effects_fail_locally(tmp_path, source):
    scaffold_project(tmp_path)
    (tmp_path / "main.py").write_text(source)
    before_path = list(sys.path)
    before_modules = {key for key in sys.modules if key.startswith("_castia_preflight_")}
    report = preflight(tmp_path, environment={}, required_env=())
    assert not report.ok
    assert statuses(report)["registration"] == "fail"
    assert "TOP-SECRET-CREDENTIAL" not in json.dumps(report.to_dict())
    assert sys.path == before_path
    assert before_modules == {key for key in sys.modules if key.startswith("_castia_preflight_")}


@pytest.mark.parametrize("target", ["../main:app", "main", "main:missing", "main:app.foo"])
def test_bad_app_target(tmp_path, target):
    scaffold_project(tmp_path)
    assert statuses(preflight(tmp_path, app_target=target))["registration"] == "fail"


@pytest.mark.parametrize("relative", [
    "azure.yaml",
    ".agent_configs/baseline/instructions.md",
    ".agent_configs/baseline/metadata.yaml",
    "eval-seed.jsonl",
])
def test_missing_artifacts_fail(tmp_path, relative):
    scaffold_project(tmp_path)
    (tmp_path / relative).unlink()
    assert not preflight(tmp_path, required_env=()).ok


def test_manifest_drift_and_version_errors(tmp_path):
    scaffold_project(tmp_path)
    path = tmp_path / "azure.yaml"
    path.write_text(path.read_text().replace("version: 2.0.0", "version: 1.0.0"))
    report = preflight(tmp_path, required_env=())
    assert statuses(report)["manifest"] == "fail"
    path.write_text(path.read_text().replace("protocol: responses", "protocol: chat"))
    assert statuses(preflight(tmp_path, required_env=()))["manifest"] == "fail"


def test_external_artifact_pointers_rejected_without_reading(tmp_path):
    scaffold_project(tmp_path)
    path = tmp_path / ".agent_configs" / "baseline" / "metadata.yaml"
    path.write_text(path.read_text().replace("instructions.md", "../../../secret.txt"))
    report = preflight(tmp_path, required_env=())
    assert statuses(report)["baseline"] == "fail"


def test_providers_not_called_and_tool_drift_explicitly_skipped(tmp_path):
    scaffold_project(tmp_path)
    with (tmp_path / "main.py").open("a") as out:
        out.write("\ndef tools():\n    raise RuntimeError('must not execute')\napp.tools(tools)\n")
    report = preflight(tmp_path, required_env=())
    assert report.ok, report.to_dict()
    assert statuses(report)["baseline.tools"] == "skipped"


def test_nonexistent_project_does_not_create_it(tmp_path):
    root = tmp_path / "missing"
    report = preflight(root, required_env=())
    assert not report.ok
    assert statuses(report)["project"] == "fail"
    assert not root.exists()


def test_code_mode_does_not_require_optional_dockerfile(tmp_path):
    scaffold_project(tmp_path)
    (tmp_path / "Dockerfile").unlink()
    report = preflight(tmp_path, required_env=())
    assert report.ok, report.to_dict()
    assert statuses(report)["build.Dockerfile"] == "skipped"
    warning = next(item.message for item in report.diagnostics if item.check == "optimizer")
    assert "Consider a Responses-only sibling" in warning
    assert "requires" not in warning


def test_container_mode_still_requires_dockerfile(tmp_path):
    scaffold_project(tmp_path)
    manifest = tmp_path / "azure.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "    codeConfiguration:\n      runtime: python_3_13\n"
            "      entryPoint: main.py\n      dependencyResolution: remote_build\n",
            "",
        ).replace("language: python", "language: docker")
    )
    (tmp_path / "Dockerfile").unlink()
    report = preflight(tmp_path, required_env=())
    assert not report.ok
    assert statuses(report)["build.Dockerfile"] == "fail"


def test_code_entrypoint_must_be_a_local_existing_file(tmp_path):
    scaffold_project(tmp_path)
    manifest = tmp_path / "azure.yaml"
    manifest.write_text(manifest.read_text().replace("entryPoint: main.py", "entryPoint: ../outside.py"))
    assert statuses(preflight(tmp_path, required_env=()))["manifest"] == "fail"


@pytest.mark.parametrize("missing", ["    kind: hosted\n", "    name: my-agent\n"])
def test_missing_inline_definition_reproduces_packaging_fallback(tmp_path, missing):
    scaffold_project(tmp_path)
    manifest = tmp_path / "azure.yaml"
    manifest.write_text(manifest.read_text().replace(missing, ""))
    report = preflight(tmp_path, required_env=())
    assert statuses(report)["manifest"] == "fail"
    diagnostic = next(item for item in report.diagnostics if item.check == "manifest")
    assert "otherwise azd expects a separate agent definition" in diagnostic.message


def test_inline_definition_name_matches_runtime_agent(tmp_path):
    scaffold_project(tmp_path)
    manifest = tmp_path / "azure.yaml"
    manifest.write_text(manifest.read_text().replace("    name: my-agent", "    name: different"))
    report = preflight(tmp_path, required_env=())
    assert statuses(report)["manifest"] == "fail"


def test_deployment_context_is_advisory_unless_requested(tmp_path):
    scaffold_project(tmp_path)
    local = preflight(tmp_path, required_env=(), environment={})
    assert local.ok
    assert statuses(local)["azd.AZURE_LOCATION"] == "warning"
    required = preflight(tmp_path, required_env=(), environment={}, deployment=True)
    assert not required.ok
    for key in ("AZURE_LOCATION", "AZURE_AI_PROJECT_ID", "AZURE_SUBSCRIPTION_ID"):
        assert statuses(required)[f"azd.{key}"] == "fail"
    assert statuses(required)["azd.tenant"] == "skipped"
    assert statuses(required)["azd.environment"] == "skipped"


def test_existing_deployment_context_passes_without_echoing_values(tmp_path):
    scaffold_project(tmp_path)
    environment = {
        "AZURE_LOCATION": "private-region-value",
        "AZURE_SUBSCRIPTION_ID": "private-subscription",
        "AZURE_AI_PROJECT_ID": (
            "/subscriptions/private-subscription/resourceGroups/private-group/"
            "providers/Microsoft.CognitiveServices/accounts/private-account/projects/private-project"
        ),
    }
    report = preflight(tmp_path, required_env=(), environment=environment, deployment=True)
    assert report.ok, report.to_dict()
    assert "private-" not in json.dumps(report.to_dict())
    environment["AZURE_AI_PROJECT_ID"] = "https://project.invalid"
    bad = preflight(tmp_path, required_env=(), environment=environment, deployment=True)
    assert not bad.ok
    assert statuses(bad)["azd.project_arm_id"] == "fail"


def test_preflight_rejects_reserved_hosted_env(tmp_path):
    scaffold_project(tmp_path)
    manifest = tmp_path / "azure.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "      AZURE_AI_MODEL_DEPLOYMENT_NAME: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}\n",
            "      AZURE_AI_MODEL_DEPLOYMENT_NAME: ${AZURE_AI_MODEL_DEPLOYMENT_NAME}\n"
            "      FOUNDRY_PROJECT_ENDPOINT: ${FOUNDRY_PROJECT_ENDPOINT}\n",
        )
    )
    report = preflight(tmp_path, required_env=())
    assert not report.ok
    assert statuses(report)["manifest.env"] == "fail"
    diagnostic = next(item for item in report.diagnostics if item.check == "manifest.env")
    assert "FOUNDRY_PROJECT_ENDPOINT" in diagnostic.message
    assert "Foundry manages FOUNDRY_* and AGENT_*" in diagnostic.message

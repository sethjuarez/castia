"""Generated projects are executable artifacts, not placeholder templates."""

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from castia.building import AgentTestHarness, preflight, scaffold_project
from castia.evaluation.suite import load_suite, validate_suite
from castia.optimizing.baseline import generate_optimizer_config


def load_main(root):
    spec = importlib.util.spec_from_file_location("generated_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_compiles_imports_checks_and_runs_offline(tmp_path):
    root = tmp_path / "agent"
    report = scaffold_project(root, name="test-agent", model="gpt-4o")
    assert report.app_target == "main:app"
    assert report.to_dict()["provisioned"] is False
    assert len(report.files) == 13
    for filename in root.rglob("*.py"):
        compile(filename.read_text(encoding="utf-8"), str(filename), "exec")
    module = load_main(root)
    assert module.app.registered_protocols() == ["activity", "responses", "invocations"]
    assert validate_suite(load_suite(root / "eval.yaml")).ok
    assert not generate_optimizer_config(
        module.app, root / ".agent_configs", check=True
    ).changed
    assert json.loads((root / "eval-seed.jsonl").read_text())["ground_truth"] == "4"
    pyproject = (root / "pyproject.toml").read_text()
    assert 'name = "test-agent"' in pyproject
    assert "python-dotenv>=1.0.1" in pyproject
    assert 'requires = ["setuptools>=68"]' in pyproject
    assert 'py-modules = ["main"]' in pyproject
    assert "package = false" in pyproject
    assert not (root / "requirements.txt").exists()
    assert not (root / "requirements-dev.txt").exists()
    assert (root / ".env.example").read_text() == (
        "FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>\n"
        "AZURE_AI_MODEL_DEPLOYMENT_NAME=<deployment-name>\n"
    )
    assert "EXPOSE 8088" in (root / "Dockerfile").read_text()
    assert '"main.py"' in (root / "Dockerfile").read_text()
    assert "COPY pyproject.toml main.py ./" in (root / "Dockerfile").read_text()
    assert "pip install --no-cache-dir ." in (root / "Dockerfile").read_text()
    assert "FROM python:3.13-slim" in (root / "Dockerfile").read_text()
    main_py = (root / "main.py").read_text()
    assert "app.startup_check(validate_startup)" in main_py
    assert "app.run()" in main_py
    assert '.strip().rstrip("/")' in main_py
    manifest = YAML(typ="safe").load((root / "azure.yaml").read_text())
    service = manifest["services"]["test-agent"]
    assert service["language"] == "python"
    assert service["host"] == "azure.ai.agent"
    assert service["kind"] == "hosted"
    assert service["name"] == module.app.name == "test-agent"
    assert service["description"]
    assert service["project"] == "."
    assert service["codeConfiguration"] == {
        "runtime": "python_3_13",
        "entryPoint": "main.py",
        "dependencyResolution": "remote_build",
    }
    assert "docker" not in service
    assert service["env"] == {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "${AZURE_AI_MODEL_DEPLOYMENT_NAME}",
    }
    assert service["container"] == {"resources": {"cpu": "0.5", "memory": "1Gi"}}
    assert [item["protocol"] for item in service["protocols"]] == module.app.registered_protocols()
    assert all(item["version"] == "2.0.0" for item in service["protocols"])
    assert not (root / "agent.yaml").exists()
    guide = (root / "DEPLOYMENT.md").read_text()
    assert "AZURE_AI_PROJECT_ID" in guide
    assert "azd resolves the tenant from the subscription" in guide
    assert "does not create a project" in guide
    assert "uv run --directory" in guide
    assert "do not declare it under `services.<agent>.env`" in guide
    assert "do not add a runtime `requirements.txt` containing `-e .`" in guide
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-W", "error", str(root / "tests")],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", ["../outside", "inside/../../outside"])
def test_scaffold_refuses_traversal(tmp_path, target):
    with pytest.raises(ValueError, match="traversal"):
        scaffold_project(tmp_path / target)


@pytest.mark.parametrize("kwargs", [
    {"name": "../oops"}, {"name": "x\nservices: bad"}, {"name": "UPPER"},
    {"model": "../model"}, {"model": "x\nsecret"}, {"model": "';bad"},
])
def test_scaffold_refuses_injected_identifiers(tmp_path, kwargs):
    with pytest.raises(ValueError):
        scaffold_project(tmp_path / "agent", **kwargs)
    assert not (tmp_path / "agent").exists()


def test_conflicts_leave_existing_content_unchanged(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    marker = root / ".hidden"
    marker.write_text("do not touch")
    with pytest.raises(FileExistsError):
        scaffold_project(root)
    assert list(root.iterdir()) == [marker]
    assert marker.read_text() == "do not touch"
    with pytest.raises(FileExistsError):
        scaffold_project(marker)


def test_scaffold_refuses_symlink_target(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows host")
    with pytest.raises(ValueError, match="symlinks"):
        scaffold_project(link / "child")
    assert not list(actual.iterdir())


def test_failed_write_rolls_back_only_its_created_content(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    root.mkdir()
    original = Path.open

    def fail(self, mode="r", *args, **kwargs):
        if self.name == "Dockerfile" and mode == "x":
            raise OSError("write failure")
        return original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="write failure"):
        scaffold_project(root)
    assert root.is_dir()
    assert not list(root.iterdir())


def test_importing_scaffold_never_constructs_model_or_loads_optimizer(tmp_path, monkeypatch):
    import castia

    scaffold_project(tmp_path)

    def fail(*args, **kwargs):
        pytest.fail("module registration invoked config/model")

    monkeypatch.setattr(castia, "load_agent_config", fail)
    monkeypatch.setattr(castia, "configured_model", fail)
    module = load_main(tmp_path)

    class Echo:
        async def respond(self, text):
            return text

    async def run():
        async with AgentTestHarness(
            module.app, dependency_overrides={module.model_provider: Echo}
        ) as test:
            r = await test.client.post("/responses", json={"input": "real handler"})
            assert r.json()["output_text"] == "real handler"

    asyncio.run(run())


def test_building_import_stays_cheap():
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; import castia.building; "
            "assert 'fastapi' not in sys.modules; "
            "assert 'httpx' not in sys.modules; "
            "assert 'azure.identity' not in sys.modules"
        )],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_generated_project_passes_readiness_without_writes(tmp_path):
    scaffold_project(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = preflight(tmp_path, environment={
        "FOUNDRY_PROJECT_ENDPOINT": "https://example.invalid/api/projects/test",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4o",
    })
    assert report.ok, report.to_dict()
    assert report.protocols == ("activity", "responses", "invocations")
    diagnostics = {item.check: item.message for item in report.diagnostics}
    assert diagnostics["build.pyproject.toml"] == "Local build artifact is present."
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after

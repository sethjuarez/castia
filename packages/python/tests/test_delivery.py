import json
import subprocess
from types import SimpleNamespace

import pytest

from castia.delivery import AzdDeployment

ENDPOINT = "https://test.services.ai.azure.com/api/projects/project"
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
RESOURCE = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/group"
    "/providers/Microsoft.CognitiveServices/accounts/test/projects/project"
)


def environment():
    return {
        "AZURE_ENV_NAME": "dev",
        "FOUNDRY_PROJECT_ENDPOINT": ENDPOINT,
        "AZURE_AI_PROJECT_ID": RESOURCE,
        "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION,
    }


def deployment(tmp_path):
    (tmp_path / "azure.yaml").write_text(
        "services:\n  smoke:\n    host: azure.ai.agent\n    project: .\n"
    )
    return AzdDeployment(
        tmp_path, service="smoke", project_endpoint=ENDPOINT, project_resource_id=RESOURCE,
    )


def payload():
    return {
        "name": "smoke", "version": "3", "status": "active",
        "definition": {
            "kind": "hosted",
            "environment_variables": {
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "OPTIMIZATION_CANDIDATE_ID": "candidate",
            },
            "code_configuration": {"content_hash": "hash"},
        },
    }


def test_azd_pins_project_and_deploys_only_named_service(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    calls = []
    monkeypatch.setattr("castia.delivery.azd.shutil.which", lambda _: "azd.exe")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://unrelated.invalid")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        data = environment() if argv[1] == "env" else payload()
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    monkeypatch.setattr("castia.delivery.azd.subprocess.run", run)
    receipt = client.deploy(expected_model="model", expected_candidate="candidate")
    assert receipt.agent_version == "3"
    assert calls[1][0] == [
        "azd.exe", "deploy", "smoke", "--no-prompt", "--environment", "dev",
    ]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 900
    assert calls[0][1]["env"]["FOUNDRY_PROJECT_ENDPOINT"] == ENDPOINT
    assert calls[0][1]["env"]["AZURE_AI_PROJECT_ENDPOINT"] == ENDPOINT
    assert calls[0][1]["env"]["AZURE_AI_PROJECT_ID"] == RESOURCE
    assert calls[0][1]["env"]["AZURE_SUBSCRIPTION_ID"] == SUBSCRIPTION
    assert receipt.project_resource_id == RESOURCE
    assert all("provision" not in argv for argv, _ in calls)


@pytest.mark.parametrize("change", [
    {"name": "other"}, {"status": "failed"}, {"version": None}, {"definition": {}},
])
def test_verification_rejects_mismatched_or_missing_state(tmp_path, monkeypatch, change):
    client = deployment(tmp_path)
    data = payload() | change
    monkeypatch.setattr(client, "_run", lambda args: json.dumps(
        environment() if args[0] == "env" else data
    ))
    with pytest.raises(ValueError):
        client.verify(expected_model="model")


def test_candidate_mismatch_is_not_success(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    monkeypatch.setattr(client, "_run", lambda args: json.dumps(
        environment() if args[0] == "env" else payload()
    ))
    with pytest.raises(ValueError, match="candidate"):
        client.verify(expected_candidate="different")


def test_manifest_cannot_escape_root(tmp_path):
    (tmp_path / "azure.yaml").write_text(
        "services:\n  smoke:\n    host: azure.ai.agent\n    project: ..\n"
    )
    with pytest.raises(ValueError, match="inside"):
        AzdDeployment(
            tmp_path, service="smoke", project_endpoint=ENDPOINT, project_resource_id=RESOURCE,
        )


@pytest.mark.parametrize("reserved", ["FOUNDRY_PROJECT_ENDPOINT", "AGENT_SECRET"])
def test_reserved_hosted_env_blocks_deployment_handoff(tmp_path, reserved):
    (tmp_path / "azure.yaml").write_text(
        "services:\n"
        "  smoke:\n"
        "    host: azure.ai.agent\n"
        "    kind: hosted\n"
        "    name: smoke\n"
        "    project: .\n"
        "    env:\n"
        f"      {reserved}: ${{{reserved}}}\n"
    )
    with pytest.raises(ValueError, match=reserved):
        AzdDeployment(
            tmp_path, service="smoke", project_endpoint=ENDPOINT, project_resource_id=RESOURCE,
        )


def test_external_failure_does_not_echo_hook_secrets(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    monkeypatch.setattr("castia.delivery.azd.shutil.which", lambda _: "azd.exe")
    monkeypatch.setattr(
        "castia.delivery.azd.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="secret-token"),
    )
    with pytest.raises(RuntimeError, match="exited 1") as exc:
        client.show()
    assert "secret-token" not in str(exc.value)
    assert exc.value.stderr == "secret-token"


def test_timeout_reports_remote_state_uncertain(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    monkeypatch.setattr("castia.delivery.azd.shutil.which", lambda _: "azd.exe")

    def timeout(argv, **kwargs):
        if argv[1] == "env":
            return SimpleNamespace(returncode=0, stdout=json.dumps(environment()), stderr="")
        raise subprocess.TimeoutExpired(["azd"], 1)

    monkeypatch.setattr("castia.delivery.azd.subprocess.run", timeout)
    with pytest.raises(TimeoutError, match="still be running"):
        client.deploy()


@pytest.mark.parametrize("change", [
    {"FOUNDRY_PROJECT_ENDPOINT": "https://other.services.ai.azure.com/api/projects/project"},
    {"FOUNDRY_PROJECT_ENDPOINT": None},
    {"AZURE_AI_PROJECT_ENDPOINT": "https://other.invalid"},
    {"AZURE_AI_PROJECT_ID": RESOURCE.replace("/accounts/test/", "/accounts/other/")},
    {"FOUNDRY_PROJECT_RESOURCE_ID": "/subscriptions/other"},
    {"AZURE_SUBSCRIPTION_ID": "22222222-2222-2222-2222-222222222222"},
    {"AZURE_ENV_NAME": None},
])
@pytest.mark.parametrize("operation", ["deploy", "verify"])
def test_prepared_context_mismatch_blocks_resource_commands(
    tmp_path, monkeypatch, change, operation,
):
    client = deployment(tmp_path)
    calls = []

    def run(args):
        calls.append(args)
        assert args[0] == "env", "An unapproved context reached a resource command"
        return json.dumps(environment() | change)

    monkeypatch.setattr(client, "_run", run)
    with pytest.raises(ValueError):
        getattr(client, operation)()
    assert len(calls) == 1


def test_changed_environment_cannot_produce_a_mislabeled_receipt(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    values = iter([
        environment(),
        environment() | {"FOUNDRY_PROJECT_ENDPOINT": "https://other.invalid"},
    ])

    def run(args):
        return json.dumps(next(values) if args[0] == "env" else payload())

    monkeypatch.setattr(client, "_run", run)
    with pytest.raises(ValueError, match="endpoint"):
        client.verify()


def test_explicit_environment_must_match_resolved_name(tmp_path, monkeypatch):
    client = deployment(tmp_path)
    client.environment = "approved"
    monkeypatch.setattr(client, "_run", lambda _: json.dumps(environment()))
    with pytest.raises(ValueError, match="different environment"):
        client.show()


@pytest.mark.parametrize("resource", [
    (
        "/subscriptions/not-a-uuid/resourceGroups/group/providers/"
        "Microsoft.CognitiveServices/accounts/test/projects/project"
    ),
    RESOURCE.replace("/projects/project", "/projects/other"),
    RESOURCE.replace("/projects/project", ""),
])
def test_invalid_approved_project_resource_is_rejected(tmp_path, resource):
    with pytest.raises(ValueError):
        AzdDeployment(
            tmp_path, service="smoke", project_endpoint=ENDPOINT, project_resource_id=resource,
        )

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from castia.__main__ import main
from castia.building.doctor import identity_checks, singular_toolbox_paths
from castia.building.quota import (
    ARM,
    QuotaError,
    classify_meter,
    list_quota,
    parse_usages,
)

PROJECT_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg/providers/"
    "Microsoft.CognitiveServices/accounts/acct/projects/proj"
)
SUB = "00000000-0000-0000-0000-000000000001"


def _statuses(report):
    return {item.check: item.status for item in report.diagnostics}


def _write_service(tmp_path, env_block: str, extra: str = "") -> None:
    (tmp_path / "azure.yaml").write_text(
        "name: demo\nservices:\n  agent:\n    host: azure.ai.agent\n"
        f"    env:\n{env_block}{extra}",
        encoding="utf-8",
    )


def test_identity_checks_warn_on_missing_and_mismatched_values(tmp_path):
    _write_service(tmp_path, "      OTHER: x\n")
    (tmp_path / "main.py").write_text('from castia import Model\nm = Model("gpt-5")\n', encoding="utf-8")

    report = identity_checks(tmp_path, environment={"AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1"})
    statuses = _statuses(report)

    assert statuses["identity.project_resource_id"] == "warning"
    assert statuses["identity.manifest.agent.project_resource_id"] == "warning"
    assert statuses["identity.manifest.agent.model"] == "warning"
    assert statuses["identity.model_deployment"] == "warning"
    message = next(i.message for i in report.diagnostics if i.check == "identity.model_deployment")
    assert "main.py:2 uses 'gpt-5'" in message
    assert report.ok


def test_identity_checks_reject_endpoint_as_project_id(tmp_path):
    report = identity_checks(
        tmp_path,
        environment={"AZURE_AI_PROJECT_ID": "https://acct.services.ai.azure.com/api/projects/proj"},
    )
    assert _statuses(report)["identity.project_resource_id"] == "warning"


def test_identity_checks_pass_when_aligned(tmp_path):
    _write_service(
        tmp_path,
        "      AZURE_AI_PROJECT_RESOURCE_ID: ${AZURE_AI_PROJECT_ID}\n"
        "      AZURE_AI_MODEL_DEPLOYMENT_NAME: gpt-4.1\n",
    )
    (tmp_path / "main.py").write_text('Model(deployment="gpt-4.1")\n', encoding="utf-8")

    report = identity_checks(
        tmp_path,
        environment={"AZURE_AI_PROJECT_ID": PROJECT_ID, "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1"},
    )

    assert report.warnings == 0
    assert "manifest.toolboxes" not in _statuses(report)


def test_singular_toolbox_key_warns(tmp_path):
    _write_service(
        tmp_path,
        "      AZURE_AI_MODEL_DEPLOYMENT_NAME: gpt-4.1\n",
        "    toolbox:\n      name: tools\n",
    )

    assert singular_toolbox_paths(tmp_path) == ["services.agent.toolbox"]
    report = identity_checks(tmp_path, environment={})
    assert _statuses(report)["manifest.toolboxes"] == "warning"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("OpenAI.GlobalStandard.o4-mini", ("serving", "GlobalStandard", "o4-mini")),
        ("OpenAI.Standard.o4-mini-finetune", ("finetuned-serving", "Standard", "o4-mini")),
        ("OpenAI.DeveloperTier.gpt4.1-finetune", ("developer-tier", "DeveloperTier", "gpt4.1")),
        ("OpenAI.GlobalBatch.gpt-4.1", ("batch", "GlobalBatch", "gpt-4.1")),
        ("OpenAI.FineTuned.Deployments", ("finetuned-deployments", "FineTuned", None)),
        (
            "AIServices.GlobalStandard.GPT-OSS-20B-Finetune",
            ("finetuned-serving", "GlobalStandard", "GPT-OSS-20B"),
        ),
    ],
)
def test_classify_meter(name, expected):
    assert classify_meter(name) == expected


def _row(name: str, current: float, limit: float) -> dict:
    return {"name": {"value": name}, "currentValue": current, "limit": limit}


def test_parse_usages_matches_model_spellings_and_labels_full_serving():
    rows = [
        _row("OpenAI.Standard.gpt4.1", 1000, 1000),
        _row("OpenAI.DeveloperTier.gpt4.1-finetune", 0, 250),
        _row("OpenAI.GlobalBatch.gpt-4.1", 0, 200000),
        _row("OpenAI.Standard.gpt4.1-mini", 0, 10),
        _row("OpenAI.FineTuned.Deployments", 1, 10),
    ]

    meters = parse_usages(rows, model="gpt-4.1")

    by_kind = {meter.kind: meter for meter in meters}
    assert set(by_kind) == {"serving", "developer-tier", "batch", "finetuned-deployments"}
    assert by_kind["serving"].state == "full"
    assert by_kind["developer-tier"].state == "available"


class _Credential:
    def get_token(self, scope):
        assert scope.startswith(ARM)
        return SimpleNamespace(token="t")


def test_list_quota_follows_arm_pages_and_reports_missing_kinds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer t"
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "value": [_row("OpenAI.GlobalStandard.o4-mini", 0, 2000)],
                    "nextLink": f"{ARM}/next?page=2",
                },
            )
        return httpx.Response(200, json={"value": [_row("OpenAI.DeveloperTier.o4-mini-finetune", 250, 250)]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = list_quota(SUB, "SwedenCentral", model="o4-mini", credential=_Credential(), client=client)

    assert len(calls) == 2
    assert "/locations/swedencentral/usages" in calls[0]
    assert report.missing_kinds() == ["finetuned-serving"]
    assert report.to_dict()["meters"][1]["state"] == "full"


def test_list_quota_rejects_foreign_next_link_and_http_errors():
    def foreign(request):
        return httpx.Response(200, json={"value": [], "nextLink": "https://evil.example/x"})

    with (
        httpx.Client(transport=httpx.MockTransport(foreign)) as client,
        pytest.raises(QuotaError, match="nextLink"),
    ):
        list_quota(SUB, "eastus", credential=_Credential(), client=client)

    with (
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client,
        pytest.raises(QuotaError, match="HTTP 403"),
    ):
        list_quota(SUB, "eastus", credential=_Credential(), client=client)

    with pytest.raises(ValueError):
        list_quota("not-a-uuid", "eastus", credential=_Credential())


def test_doctor_cli_json_and_quota_requires_subscription(tmp_path, monkeypatch, capsys):
    for key in ("AZURE_SUBSCRIPTION_ID", "AZURE_AI_PROJECT_ID", "AZURE_AI_PROJECT_RESOURCE_ID",
                "FOUNDRY_PROJECT_RESOURCE_ID", "AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        monkeypatch.delenv(key, raising=False)

    assert main(["doctor", "--project", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "offline"

    assert main(["doctor", "--project", str(tmp_path), "quota", "--region", "eastus"]) == 2
    assert "--subscription" in capsys.readouterr().err

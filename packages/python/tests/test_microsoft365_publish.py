import json
import struct

import pytest

from castia import Agent
from castia.delivery.microsoft365 import (
    AUTOPILOT_API_VERSION,
    TEAMS_API_VERSION,
    build_publish_plan,
    load_png_icon,
    write_redacted_artifact,
)
from castia.messaging.surfaces import Teams

ENDPOINT = "https://test.services.ai.azure.com/api/projects/contracts"


def _write_png(path, width, height):
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    ihdr += b"\x08\x06\x00\x00\x00" + b"\x00\x00\x00\x00"
    path.write_bytes(header + ihdr + b"\x00\x00\x00\x00IEND\x00\x00\x00\x00")
    return path


def activity_app():
    app = Agent(name="contracts")

    @app.activity(Teams.direct)
    async def answer(text: str) -> str:
        return text

    return app


def publish_args(tmp_path):
    return {
        "app": activity_app(),
        "project_endpoint": ENDPOINT,
        "agent_name": "contracts",
        "agent_display_name": "Contracts",
        "app_version": "1.0.0",
        "color_icon": _write_png(tmp_path / "color.png", 192, 192),
        "outline_icon": _write_png(tmp_path / "outline.png", 32, 32),
        "short_description": "Reviews contracts.",
        "full_description": "Reviews contracts and prepares evidence-backed reports.",
        "developer_name": "Caldova",
        "developer_website_url": "https://caldova.com",
        "privacy_url": "https://caldova.com/privacy",
        "terms_of_use_url": "https://caldova.com/terms",
    }


def test_autopilot_payload_forces_agentic_user_template(tmp_path):
    plan = build_publish_plan(
        **publish_args(tmp_path),
        mode="autopilot",
        publish_scope="Shared",
        blueprint_client_id="client-id",
    )
    payload = plan.payload
    assert plan.api_version == AUTOPILOT_API_VERSION
    assert plan.url == (
        ENDPOINT
        + "/agents/contracts/microsoft365/publish?api-version="
        + AUTOPILOT_API_VERSION
    )
    assert payload["publishAsAutopilot"] is True
    assert payload["publishScope"] == "Tenant"
    assert payload["useAgenticUserTemplate"] is True
    assert payload["agenticUserTemplate"] == {
        "Id": "digitalWorkerTemplate",
        "File": "agenticUserTemplateManifest.json",
        "SchemaVersion": "0.1.0-preview",
        "AgentIdentityBlueprintId": "client-id",
        "CommunicationProtocol": "activityProtocol",
    }
    assert isinstance(payload["colorIconBase64"], str)
    assert plan.redacted_payload["colorIconBase64"]["redacted"] is True
    assert "base64" not in plan.redacted_payload["colorIconBase64"]


def test_teams_payload_uses_stable_api_and_bot_service_arm_id(tmp_path):
    plan = build_publish_plan(
        **publish_args(tmp_path),
        mode="teams",
        publish_scope="Tenant",
        bot_service_arm_id="/subscriptions/000/resourceGroups/rg/providers/Microsoft.BotService/botServices/bot",
    )
    assert plan.api_version == TEAMS_API_VERSION
    assert plan.payload["publishAsAutopilot"] is False
    assert plan.payload["publishScope"] == "Tenant"
    assert plan.payload["botServiceArmId"].endswith("/botServices/bot")
    assert "useAgenticUserTemplate" not in plan.payload


def test_autopilot_requires_privacy_url_even_when_other_metadata_is_present(tmp_path):
    args = publish_args(tmp_path)
    args["privacy_url"] = None
    with pytest.raises(ValueError, match="privacyUrl"):
        build_publish_plan(
            **args,
            mode="autopilot",
            blueprint_client_id="client-id",
        )


def test_https_metadata_url_validation_names_field(tmp_path):
    args = publish_args(tmp_path)
    args["developer_website_url"] = "http://caldova.com"
    with pytest.raises(ValueError, match="developerWebsiteUrl"):
        build_publish_plan(
            **args,
            mode="teams",
            bot_service_arm_id="/subscriptions/000/resourceGroups/rg/providers/Microsoft.BotService/botServices/bot",
        )


def test_mode_specific_required_fields(tmp_path):
    with pytest.raises(ValueError, match="blueprintClientId"):
        build_publish_plan(**publish_args(tmp_path), mode="autopilot")

    with pytest.raises(ValueError, match="botServiceArmId"):
        build_publish_plan(**publish_args(tmp_path), mode="teams")


def test_icon_validation_checks_png_type_and_dimensions(tmp_path):
    with pytest.raises(ValueError, match="192x192"):
        load_png_icon(
            _write_png(tmp_path / "wrong.png", 191, 192),
            field="colorIconBase64",
            expected_size=(192, 192),
        )

    not_png = tmp_path / "icon.txt"
    not_png.write_text("not png", encoding="utf-8")
    with pytest.raises(ValueError, match="PNG"):
        load_png_icon(not_png, field="colorIconBase64", expected_size=(192, 192))


def test_activity_protocol_is_required_for_microsoft365_publish(tmp_path):
    app = Agent(name="responses-only")

    @app.responses()
    async def answer(text: str) -> str:
        return text

    with pytest.raises(ValueError, match="Activity protocol"):
        build_publish_plan(
            **(publish_args(tmp_path) | {"app": app}),
            mode="autopilot",
            blueprint_client_id="client-id",
        )


def test_redacted_artifact_omits_full_icon_base64(tmp_path):
    plan = build_publish_plan(
        **publish_args(tmp_path),
        mode="autopilot",
        blueprint_client_id="client-id",
    )
    artifact = write_redacted_artifact(plan, tmp_path / "dry-run.json")
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["payload"]["colorIconBase64"]["redacted"] is True
    assert data["icons"]["colorIcon"]["width"] == 192
    assert plan.payload["colorIconBase64"] not in artifact.read_text(encoding="utf-8")

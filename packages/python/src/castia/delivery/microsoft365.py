"""Offline Microsoft 365 publish payload planning for Foundry hosted agents."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from castia.runtime.application import Agent

Mode = Literal["teams", "autopilot"]
PublishScope = Literal["Shared", "Tenant"]

TEAMS_API_VERSION = "v1"
AUTOPILOT_API_VERSION = "2025-11-15-preview"


@dataclass(frozen=True)
class Icon:
    path: Path
    width: int
    height: int
    sha256: str
    size_bytes: int
    base64: str

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class Microsoft365PublishPlan:
    mode: Mode
    method: str
    url: str
    api_version: str
    payload: dict[str, Any]
    redacted_payload: dict[str, Any]
    icon_summaries: dict[str, dict[str, Any]]

    def artifact(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "method": self.method,
            "url": self.url,
            "apiVersion": self.api_version,
            "payload": self.redacted_payload,
            "icons": self.icon_summaries,
        }


def require_activity_protocol(app: Agent) -> None:
    """Microsoft 365 publishing requires the Activity protocol."""
    if "activity" not in app.registered_protocols():
        raise ValueError(
            "Microsoft 365 publishing requires an @app.activity(...) handler "
            "so the hosted agent can serve the Activity protocol."
        )


def validate_https_url(value: str | None, *, field: str, required: bool = False) -> str | None:
    """Return a normalized HTTPS URL or fail with the field name."""
    if value is None or not str(value).strip():
        if required:
            raise ValueError(f"{field} is required")
        return None
    text = str(value).strip()
    url = urlsplit(text)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.fragment
    ):
        raise ValueError(f"{field} must be a valid HTTPS URL")
    return text


def validate_project_endpoint(value: str) -> str:
    endpoint = validate_https_url(value, field="projectEndpoint", required=True)
    assert endpoint is not None
    url = urlsplit(endpoint)
    parts = [part for part in url.path.split("/") if part]
    if len(parts) < 3 or parts[-3:-1] != ["api", "projects"] or not parts[-1]:
        raise ValueError(
            "projectEndpoint must be a Foundry project URL containing /api/projects/<project>"
        )
    if url.query:
        raise ValueError("projectEndpoint must not include a query string")
    return endpoint.rstrip("/")


def _required_text(value: str | None, field: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field} is required")
    return str(value).strip()


def _png_dimensions(data: bytes) -> tuple[int, int]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ValueError("icon must be a PNG file")
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError("icon PNG is missing an IHDR header")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def load_png_icon(path: str | Path, *, field: str, expected_size: tuple[int, int]) -> Icon:
    resolved = Path(path)
    data = resolved.read_bytes()
    width, height = _png_dimensions(data)
    if (width, height) != expected_size:
        expected = f"{expected_size[0]}x{expected_size[1]}"
        actual = f"{width}x{height}"
        raise ValueError(f"{field} must be a PNG exactly {expected}; got {actual}")
    digest = hashlib.sha256(data).hexdigest()
    return Icon(
        path=resolved,
        width=width,
        height=height,
        sha256=digest,
        size_bytes=len(data),
        base64=base64.b64encode(data).decode("ascii"),
    )


def _redact_icons(payload: dict[str, Any], *, color: Icon, outline: Icon) -> dict[str, Any]:
    redacted = dict(payload)
    redacted["colorIconBase64"] = {
        "redacted": True,
        **color.summary(),
    }
    redacted["outlineIconBase64"] = {
        "redacted": True,
        **outline.summary(),
    }
    return redacted


def build_publish_plan(
    *,
    app: Agent,
    mode: Mode,
    project_endpoint: str,
    agent_name: str,
    agent_display_name: str,
    app_version: str,
    color_icon: str | Path,
    outline_icon: str | Path,
    short_description: str,
    full_description: str,
    developer_name: str,
    developer_website_url: str,
    privacy_url: str | None,
    terms_of_use_url: str | None = None,
    publish_scope: PublishScope = "Shared",
    can_respond_without_mention: bool = False,
    blueprint_client_id: str | None = None,
    bot_service_arm_id: str | None = None,
) -> Microsoft365PublishPlan:
    require_activity_protocol(app)
    if mode not in ("teams", "autopilot"):
        raise ValueError("mode must be 'teams' or 'autopilot'")

    endpoint = validate_project_endpoint(project_endpoint)
    name = _required_text(agent_name, "agentName")
    color = load_png_icon(color_icon, field="colorIconBase64", expected_size=(192, 192))
    outline = load_png_icon(outline_icon, field="outlineIconBase64", expected_size=(32, 32))
    developer_url = validate_https_url(
        developer_website_url, field="developerWebsiteUrl", required=True,
    )
    privacy = validate_https_url(
        privacy_url, field="privacyUrl", required=(mode == "autopilot"),
    )
    terms = validate_https_url(terms_of_use_url, field="termsOfUseUrl")

    payload: dict[str, Any] = {
        "agentDisplayName": _required_text(agent_display_name, "agentDisplayName"),
        "publishAsAutopilot": mode == "autopilot",
        "publishScope": "Tenant" if mode == "autopilot" else publish_scope,
        "appVersion": _required_text(app_version, "appVersion"),
        "canRespondWithoutMention": bool(can_respond_without_mention),
        "shortDescription": _required_text(short_description, "shortDescription"),
        "fullDescription": _required_text(full_description, "fullDescription"),
        "developerName": _required_text(developer_name, "developerName"),
        "developerWebsiteUrl": developer_url,
        "colorIconBase64": color.base64,
        "outlineIconBase64": outline.base64,
    }
    if privacy is not None:
        payload["privacyUrl"] = privacy
    if terms is not None:
        payload["termsOfUseUrl"] = terms

    if mode == "autopilot":
        client_id = _required_text(blueprint_client_id, "blueprintClientId")
        payload["useAgenticUserTemplate"] = True
        payload["agenticUserTemplate"] = {
            "Id": "digitalWorkerTemplate",
            "File": "agenticUserTemplateManifest.json",
            "SchemaVersion": "0.1.0-preview",
            "AgentIdentityBlueprintId": client_id,
            "CommunicationProtocol": "activityProtocol",
        }
        api_version = AUTOPILOT_API_VERSION
    else:
        if publish_scope not in ("Shared", "Tenant"):
            raise ValueError("publishScope must be Shared or Tenant for teams mode")
        payload["botServiceArmId"] = _required_text(bot_service_arm_id, "botServiceArmId")
        api_version = TEAMS_API_VERSION

    url = f"{endpoint}/agents/{name}/microsoft365/publish?api-version={api_version}"
    return Microsoft365PublishPlan(
        mode=mode,
        method="POST",
        url=url,
        api_version=api_version,
        payload=payload,
        redacted_payload=_redact_icons(payload, color=color, outline=outline),
        icon_summaries={
            "colorIcon": color.summary(),
            "outlineIcon": outline.summary(),
        },
    )


def write_redacted_artifact(plan: Microsoft365PublishPlan, path: str | Path) -> Path:
    target = Path(path)
    target.write_text(
        json.dumps(plan.artifact(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target

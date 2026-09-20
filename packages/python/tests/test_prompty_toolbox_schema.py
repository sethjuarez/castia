from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

import castia.prompty as castia_prompty
from castia.prompty import (
    toolbox_preflight,
    toolbox_prompty_tools_from_mcp,
    toolbox_prompty_tools_from_schema,
)


@dataclass
class FakeProperty:
    name: str
    kind: str
    description: str | None = None
    required: bool = False
    items: object | None = None
    properties: list[object] | None = None
    additionalProperties: object | None = None


@dataclass
class FakeFunctionTool:
    name: str
    description: str | None = None
    parameters: list[object] | None = None
    kind: str = "function"


class FakePrompty:
    Property = FakeProperty
    FunctionTool = FakeFunctionTool


class FakeMcpClient:
    async def list_tools(self) -> list[dict[str, Any]]:
        return [_knowledge_base_tool()]


def test_toolbox_prompty_tools_from_schema_preserves_required_array(monkeypatch):
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    tools = toolbox_prompty_tools_from_schema(
        [_knowledge_base_tool()],
        allowed_tools=("contracts-kb-mcp___knowledge_base_retrieve",),
    )

    tool = tools[0]
    query_variants = tool.parameters[0]
    assert tool.name == "contracts-kb-mcp___knowledge_base_retrieve"
    assert tool.description == "Retrieve cited grounding passages."
    assert query_variants.name == "query_variants"
    assert query_variants.kind == "array"
    assert query_variants.required is True
    assert query_variants.description == "Alternative search queries."
    assert query_variants.items.kind == "string"


def test_toolbox_prompty_tools_from_schema_preserves_objects_and_additional_properties(monkeypatch):
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    tools = toolbox_prompty_tools_from_schema(
        [
            {
                "name": "structured_lookup",
                "inputSchema": {
                    "type": "object",
                    "required": ["filter"],
                    "properties": {
                        "filter": {
                            "type": "object",
                            "description": "Structured metadata filter.",
                            "additionalProperties": False,
                            "required": ["jurisdiction"],
                            "properties": {
                                "jurisdiction": {
                                    "type": "string",
                                    "description": "Policy jurisdiction.",
                                }
                            },
                        }
                    },
                },
            }
        ],
        allowed_tools=("structured_lookup",),
    )

    filter_param = tools[0].parameters[0]
    assert filter_param.name == "filter"
    assert filter_param.kind == "object"
    assert filter_param.required is True
    assert filter_param.additionalProperties is False
    assert filter_param.properties[0].name == "jurisdiction"
    assert filter_param.properties[0].required is True


def test_toolbox_prompty_tools_from_mcp_uses_tools_list_schema(monkeypatch):
    monkeypatch.setattr(castia_prompty, "_prompty", lambda: FakePrompty)

    tools = asyncio.run(
        toolbox_prompty_tools_from_mcp(
            ("contracts-kb-mcp___knowledge_base_retrieve",),
            client=FakeMcpClient(),
        )
    )

    assert tools[0].parameters[0].name == "query_variants"
    assert tools[0].parameters[0].kind == "array"
    assert tools[0].parameters[0].required is True


def test_toolbox_preflight_lists_tools_with_ai_foundry_bearer(monkeypatch):
    requests = []

    async def fake_token() -> str:
        return "TOKEN"

    monkeypatch.setattr(castia_prompty, "_default_toolbox_token", fake_token)

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request, payload))
        assert request.headers["Authorization"] == "Bearer TOKEN"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [_knowledge_base_tool()]},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            return await toolbox_preflight(
                ("contracts-kb-mcp___knowledge_base_retrieve",),
                endpoint="https://example.test/toolboxes/contracts/mcp",
                http_client=http,
            )

    result = asyncio.run(run())

    assert result.ok is True
    assert result.missing_tools == ()
    assert result.tool_names == ("contracts-kb-mcp___knowledge_base_retrieve",)
    assert [payload["method"] for _, payload in requests] == ["tools/list"]


def test_toolbox_preflight_reports_missing_allowed_tools():
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [_knowledge_base_tool()]},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            return await toolbox_preflight(
                ("knowledge_base_retrieve",),
                endpoint="https://example.test/toolboxes/contracts/mcp",
                token_provider=lambda: "TOKEN",
                http_client=http,
            )

    result = asyncio.run(run())

    assert result.ok is False
    assert result.missing_tools == ("knowledge_base_retrieve",)
    assert any("fully qualified toolbox matches" in diagnostic for diagnostic in result.diagnostics)


def _knowledge_base_tool() -> dict[str, Any]:
    return {
        "name": "contracts-kb-mcp___knowledge_base_retrieve",
        "description": "Retrieve cited grounding passages.",
        "inputSchema": {
            "type": "object",
            "required": ["query_variants"],
            "additionalProperties": False,
            "properties": {
                "query_variants": {
                    "type": "array",
                    "description": "Alternative search queries.",
                    "items": {"type": "string"},
                }
            },
        },
    }

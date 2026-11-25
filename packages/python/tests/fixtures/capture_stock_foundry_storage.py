"""Capture the Foundry storage writes made by the stock agentserver Responses SDK.

Regenerates ``stock_foundry_storage_2_4_0.json``, which
``tests/test_foundry_storage.py`` uses to pin Castia's create and update bodies
to the stock ``FoundryStorageProvider`` contract. The script drives the stock
host in-process with a recording provider, then serializes each call with the
stock ``_foundry_serializer``, exactly as ``FoundryStorageProvider`` does
before it POSTs. It makes no network calls.

Run from ``packages/python``::

    uv run --no-project --with azure-ai-agentserver-responses==2.4.0 \
        --with httpx python tests/fixtures/capture_stock_foundry_storage.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.pop("FOUNDRY_HOSTING_ENVIRONMENT", None)
os.environ.pop("FOUNDRY_AGENT_SESSION_ID", None)
os.environ["AGENTSERVER_STATE_ROOT"] = tempfile.mkdtemp()

from importlib.metadata import version

import httpx
from azure.ai.agentserver.responses import (
    InMemoryResponseProvider,
    ResponsesAgentServerHost,
    TextResponse,
)
from azure.ai.agentserver.responses.store._foundry_serializer import (
    serialize_create_request,
    serialize_response,
)

REFERENCE = {"type": "agent_reference", "name": "contract-policy-analyst", "version": "11"}
CASES = {
    "sync": {"model": "gpt-test", "input": "hello", "agent_reference": REFERENCE},
    "stream": {
        "model": "gpt-test",
        "input": "hello",
        "stream": True,
        "agent_reference": REFERENCE,
    },
}


class RecordingProvider(InMemoryResponseProvider):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[dict] = []

    async def create_response(self, response, input_items, history_item_ids, *, context=None):
        items = list(input_items or [])
        history = list(history_item_ids or [])
        body = serialize_create_request(response, items, history)
        self.writes.append({"op": "create", "body": json.loads(body)})
        await super().create_response(response, items, history, context=context)

    async def update_response(self, response, *, context=None):
        self.writes.append({"op": "update", "body": json.loads(serialize_response(response))})
        await super().update_response(response, context=context)


async def capture(payload: dict) -> list[dict]:
    provider = RecordingProvider()
    host = ResponsesAgentServerHost(store=provider)

    @host.response_handler
    async def handler(request, context, cancellation_signal):
        async for event in TextResponse(context, request, text="Hi there"):
            yield event

    transport = httpx.ASGITransport(app=host)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reply = await client.post("/responses", json=payload)
        reply.raise_for_status()
    return provider.writes


async def main() -> None:
    result = {
        "generator": "tests/fixtures/capture_stock_foundry_storage.py",
        "sdk": f"azure-ai-agentserver-responses=={version('azure-ai-agentserver-responses')}",
        "cases": {},
    }
    for name, payload in CASES.items():
        result["cases"][name] = {"request": payload, "writes": await capture(payload)}
    out = Path(__file__).with_name("stock_foundry_storage_2_4_0.json")
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

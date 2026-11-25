"""Foundry response storage: where the platform reads hosted Responses.

A hosted Foundry agent's responses are read back by the platform
(``GET {project}/openai/v1/responses/{id}``, the Agent Optimizer, the portal)
from Foundry storage, not from the agent container. The agentserver Responses
SDK writes every hosted response there through ``FoundryStorageProvider``;
:class:`FoundryResponseStore` is Castia's equivalent over the same wire
contract::

    POST   {project}/storage/responses?api-version=v1           create
    GET    {project}/storage/responses/{id}?api-version=v1      get
    POST   {project}/storage/responses/{id}?api-version=v1      update
    DELETE {project}/storage/responses/{id}?api-version=v1      delete
    GET    .../responses/{id}/input_items?...                   input items
    POST   .../items/batch/retrieve?api-version=v1              item lookup
    GET    .../history/item_ids?...                             history ids

Requests carry a ``https://ai.azure.com/.default`` bearer token and forward the
trusted per-request ``x-agent-foundry-call-id`` so storage scopes every
operation to the calling end user. ``x-agent-user-id`` is never forwarded.

Castia selects this store automatically when it runs hosted
(``FOUNDRY_HOSTING_ENVIRONMENT`` set) and the agent passes no
``response_store``. Foundry storage has no conditional write, so this store
does not enable ``background=true``; pass your own atomic store for that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from castia.hosting.agent_identity import is_complete_agent_reference
from castia.hosting.response_ids import item_id
from castia.hosting.responses_store import StoredResponse
from castia.runtime.request_context import (
    FOUNDRY_CALL_ID_HEADER,
    current_request_context,
)

__all__ = [
    "FOUNDRY_STORAGE_SCOPE",
    "FoundryResponseAlreadyExistsError",
    "FoundryResponseStore",
    "FoundryStorageError",
    "foundry_storage_enabled",
]

logger = logging.getLogger(__name__)

FOUNDRY_STORAGE_SCOPE = "https://ai.azure.com/.default"
_API_VERSION = "v1"
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_HOSTING_ENV = "FOUNDRY_HOSTING_ENVIRONMENT"
_ENDPOINT_ENV = "FOUNDRY_PROJECT_ENDPOINT"
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_ATTEMPTS = 3
_INPUT_ITEMS_PAGE = 100
_HISTORY_LIMIT = 1000
_CONFLICT_CODES = frozenset({"conflict", "already_exists", "duplicate"})
# Types whose status becomes ``completed`` / is preserved when an input item is
# stored, as in the agentserver ``to_output_item``.
_COMPLETED_STATUS_TYPES = frozenset(
    {
        "message",
        "function_call",
        "function_call_output",
        "computer_call",
        "computer_call_output",
        "file_search_call",
        "web_search_call",
        "image_generation_call",
        "code_interpreter_call",
        "local_shell_call",
        "local_shell_call_output",
        "shell_call",
        "shell_call_output",
        "mcp_call",
        "memory_search_call",
        "reasoning",
    }
)
_PRESERVE_STATUS_TYPES = frozenset(
    {
        "output_message",
        "apply_patch_call",
        "apply_patch_call_output",
        "tool_search_call",
        "tool_search_output",
    }
)


class FoundryStorageError(RuntimeError):
    """Foundry storage answered with an error, or a stored payload was unusable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        body: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.body = body


class FoundryResponseAlreadyExistsError(FoundryStorageError):
    """``create`` found a response with the same id already stored."""


def foundry_storage_enabled() -> bool:
    """True when running as a Foundry hosted agent (``FOUNDRY_HOSTING_ENVIRONMENT``)."""
    return bool(os.getenv(_HOSTING_ENV, "").strip())


def _storage_base_url(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ValueError(
            f"{_ENDPOINT_ENV} is required to store hosted Responses in Foundry storage."
        )
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(
            f"Foundry project endpoint must be an absolute http(s) URL, got {endpoint!r}."
        )
    return endpoint.rstrip("/") + "/storage/"


def _encode(value: str) -> str:
    return quote(value, safe="")


def _error_from_response(response) -> FoundryStorageError:
    status = response.status_code
    message = f"Foundry storage request failed with HTTP {status}."
    body = None
    code = None
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body keeps the default message
        data = None
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        body = data
        error = data["error"]
        if error.get("message"):
            message = str(error["message"])
        if error.get("code"):
            code = str(error["code"])
    error_type = FoundryStorageError
    if status in (400, 409) and (
        "already exists" in message.lower() or (code or "").lower() in _CONFLICT_CODES
    ):
        error_type = FoundryResponseAlreadyExistsError
    return error_type(message, status_code=status, code=code, body=body)


def _stored_input_items(items: Iterable, response_id: str) -> tuple[list, list[str]]:
    """Normalize request input items the way agentserver persists them.

    Returns ``(items, reference_ids)`` where ``items`` holds ``None`` at each
    ``item_reference`` position, to be filled from storage. Items get a typed
    id co-located with the response; unrecognized item types are dropped.
    """
    stored: list = []
    references: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        data = dict(item)
        if "type" not in data:
            if "role" in data or "content" in data:
                data["type"] = "message"
            elif isinstance(data.get("id"), str):
                data["type"] = "item_reference"
        item_type = data.get("type")
        if item_type == "item_reference":
            reference = data.get("id")
            if not isinstance(reference, str) or not reference:
                raise FoundryStorageError("item_reference input is missing its id.")
            references.append(reference)
            stored.append(None)
            continue
        new_id = item_id(data, response_id)
        if new_id is None:
            continue
        data["id"] = new_id
        if item_type == "message" and isinstance(data.get("content"), str):
            data["content"] = [{"type": "input_text", "text": data["content"]}]
        if item_type in _COMPLETED_STATUS_TYPES:
            data["status"] = "completed"
        elif item_type in _PRESERVE_STATUS_TYPES:
            data.setdefault("status", "completed")
        else:
            data.pop("status", None)
        stored.append(data)
    return stored, references


def _strip_nulls(value: dict) -> dict:
    """agentserver ``strip_nulls``: drop ``None`` values, recursing into dicts only."""
    return {
        key: _strip_nulls(item) if isinstance(item, dict) else item
        for key, item in value.items()
        if item is not None
    }


def storage_snapshot(response: Mapping) -> dict:
    """The response snapshot agentserver writes to Foundry storage.

    Mirrors ``_extract_response_snapshot_from_events`` and the output-item
    defaults in agentserver 2.4.0: ``None`` fields dropped, ``response_id``
    stamped on the response and each output item, ``agent_reference`` copied
    onto each output item, ``background`` and ``completed_at`` filled in.
    ``output_text`` is a client convenience, not part of the stored object.

    :raises FoundryStorageError: the response has no complete
        ``agent_reference``; Foundry storage would reject it with HTTP 400.
    """
    snapshot = _strip_nulls(deepcopy(dict(response)))
    snapshot.pop("output_text", None)
    reference = snapshot.get("agent_reference")
    if not is_complete_agent_reference(reference):
        raise FoundryStorageError(
            "agent_reference with name and version is required to store a "
            "response in Foundry storage. Send agent_reference in the request "
            "or set FOUNDRY_AGENT_NAME and FOUNDRY_AGENT_VERSION.",
            code="invalid_request",
        )
    response_id = snapshot["id"]
    snapshot.setdefault("response_id", response_id)
    snapshot.setdefault("object", "response")
    snapshot.setdefault("output", [])
    snapshot.setdefault("background", False)
    if snapshot.get("status") == "completed":
        snapshot.setdefault("completed_at", int(time.time()))
    for item in snapshot["output"]:
        if isinstance(item, dict):
            if item.get("response_id") is None:
                item["response_id"] = response_id
            if item.get("agent_reference") is None:
                item["agent_reference"] = deepcopy(reference)
    return snapshot


class FoundryResponseStore:
    """A :class:`~castia.ResponseStore` backed by Foundry response storage.

    ``endpoint`` is the Foundry project endpoint (``FOUNDRY_PROJECT_ENDPOINT``).
    ``credential`` is an async Azure credential; it defaults to
    :class:`~azure.identity.aio.DefaultAzureCredential`. ``transport`` is an
    optional ``httpx`` async transport (tests use ``httpx.MockTransport``).

    Every failure raises :class:`FoundryStorageError` (or the underlying
    transport error) after bounded retries of transient failures; missing
    records are ``None``/``False`` as the protocol requires.

    Foundry storage scopes records to the caller identified by the forwarded
    call id, so records come back without Castia ``user_id`` metadata and
    :attr:`isolates_callers` tells Castia the backend enforces ownership.
    """

    isolates_callers = True
    """Foundry storage, not Castia, decides which caller may read a record."""

    supports_background = False
    """``update`` is not an atomic compare-and-set, so background stays off."""

    response_id_prefix = "caresp"
    """Prefix for ids Castia mints for this store, as the agentserver SDK does."""

    def __init__(
        self,
        endpoint: str,
        *,
        credential: Any = None,
        transport: Any = None,
        timeout: float = 30.0,
        retry_backoff: float = 0.5,
    ) -> None:
        self._base_url = _storage_base_url(endpoint)
        self._credential = credential
        self._owns_credential = credential is None
        self._transport = transport
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._client = None
        self._token: str | None = None
        self._token_expires_on = 0.0
        self._token_lock = asyncio.Lock()

    @classmethod
    def from_env(cls, **kwargs: Any) -> FoundryResponseStore:
        """Build the store from ``FOUNDRY_PROJECT_ENDPOINT``; raise when it is unusable."""
        return cls(os.getenv(_ENDPOINT_ENV, ""), **kwargs)

    @property
    def storage_base_url(self) -> str:
        return self._base_url

    def url(self, path: str, **params: str) -> str:
        """``{endpoint}/storage/{path}?api-version=v1&...`` with encoded values."""
        query = "&".join(
            [f"api-version={_API_VERSION}"]
            + [f"{_encode(key)}={_encode(value)}" for key, value in params.items()]
        )
        return f"{self._base_url}{path}?{query}"

    async def aclose(self) -> None:
        """Close the HTTP client and the default credential this store created."""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
        if self._owns_credential and self._credential is not None:
            close = getattr(self._credential, "close", None)
            if close is not None:
                await close()
            self._credential = None

    async def _bearer(self) -> str:
        async with self._token_lock:
            if self._token is None or time.time() > self._token_expires_on - 300:
                if self._credential is None:
                    from azure.identity.aio import DefaultAzureCredential

                    self._credential = DefaultAzureCredential()
                token = await self._credential.get_token(FOUNDRY_STORAGE_SCOPE)
                self._token = token.token
                self._token_expires_on = float(token.expires_on)
            return self._token

    def _http(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: object = None,
        not_found_ok: bool = False,
    ):
        import httpx

        url = self.url(path, **dict(params or {}))
        headers = {
            "Authorization": f"Bearer {await self._bearer()}",
            "User-Agent": "castia-python",
            "x-ms-client-request-id": str(uuid4()),
        }
        call_id = current_request_context().call_id
        if call_id:
            headers[FOUNDRY_CALL_ID_HEADER] = call_id
        content = None
        if body is not None:
            headers["Content-Type"] = _JSON_CONTENT_TYPE
            content = json.dumps(body).encode("utf-8")
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await self._http().request(
                    method, url, headers=headers, content=content
                )
            except httpx.TransportError:
                if attempt == _ATTEMPTS:
                    raise
            else:
                if response.status_code < 300:
                    return response
                if response.status_code == 404 and not_found_ok:
                    return None
                if response.status_code not in _RETRY_STATUSES or attempt == _ATTEMPTS:
                    raise _error_from_response(response)
            await asyncio.sleep(self._retry_backoff * attempt)
        raise AssertionError("unreachable")

    @staticmethod
    def _json(response, path: str):
        try:
            return response.json()
        except ValueError as exc:
            raise FoundryStorageError(
                f"Foundry storage returned a non-JSON body for {path}.",
                status_code=response.status_code,
            ) from exc

    async def get_items(self, item_ids: list[str]) -> list[dict | None]:
        """Batch-read stored items; ``None`` where an id is unknown."""
        if not item_ids:
            return []
        path = "items/batch/retrieve"
        response = await self._request("POST", path, body={"item_ids": list(item_ids)})
        items = self._json(response, path)
        if not isinstance(items, list):
            raise FoundryStorageError(f"Foundry storage returned an unexpected {path} body.")
        return [item if isinstance(item, dict) else None for item in items]

    async def get_history_item_ids(
        self,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        limit: int = _HISTORY_LIMIT,
    ) -> list[str]:
        params = {"limit": str(limit)}
        if previous_response_id is not None:
            params["previous_response_id"] = previous_response_id
        if conversation_id is not None:
            params["conversation_id"] = conversation_id
        path = "history/item_ids"
        response = await self._request("GET", path, params=params)
        ids = self._json(response, path)
        if not isinstance(ids, list):
            raise FoundryStorageError(f"Foundry storage returned an unexpected {path} body.")
        return [str(value) for value in ids]

    async def create_envelope(self, record: StoredResponse) -> dict:
        """The ``POST responses`` body agentserver sends for ``record``."""
        response = storage_snapshot(record["response"])
        response_id = response["id"]
        items, references = _stored_input_items(record.get("input_items") or [], response_id)
        if references:
            resolved = await self.get_items(references)
            missing = [ref for ref, item in zip(references, resolved) if item is None]
            if missing or len(resolved) != len(references):
                raise FoundryStorageError(
                    "Referenced input items were not found in Foundry storage: "
                    + ", ".join(missing or references)
                )
            filled = iter(resolved)
            items = [item if item is not None else next(filled) for item in items]
        previous = response.get("previous_response_id")
        history = (
            await self.get_history_item_ids(
                previous_response_id=previous, conversation_id=None
            )
            if previous
            else []
        )
        return {"response": response, "input_items": items, "history_item_ids": history}

    async def create(self, record: StoredResponse) -> None:
        await self._request("POST", "responses", body=await self.create_envelope(record))

    async def replace(self, response: dict) -> None:
        """Overwrite the stored response snapshot (agentserver ``update_response``)."""
        snapshot = storage_snapshot(response)
        await self._request("POST", f"responses/{_encode(snapshot['id'])}", body=snapshot)

    async def get(self, response_id: str) -> StoredResponse | None:
        path = f"responses/{_encode(response_id)}"
        response = await self._request("GET", path, not_found_ok=True)
        if response is None:
            return None
        body = self._json(response, path)
        if not isinstance(body, dict):
            raise FoundryStorageError(f"Foundry storage returned an unexpected {path} body.")
        return {
            "response": body,
            "input_items": await self._input_items(response_id),
            "background": body.get("background") is True,
            "user_id": None,
        }

    async def _input_items(self, response_id: str) -> list[dict]:
        path = f"responses/{_encode(response_id)}/input_items"
        items: list[dict] = []
        after: str | None = None
        while True:
            params = {"limit": str(_INPUT_ITEMS_PAGE), "order": "asc"}
            if after is not None:
                params["after"] = after
            response = await self._request("GET", path, params=params, not_found_ok=True)
            if response is None:
                break
            body = self._json(response, path)
            page = body.get("data") if isinstance(body, dict) else None
            if not isinstance(page, list):
                raise FoundryStorageError(f"Foundry storage returned an unexpected {path} body.")
            items.extend(item for item in page if isinstance(item, dict))
            last = page[-1].get("id") if page and isinstance(page[-1], dict) else None
            if len(page) < _INPUT_ITEMS_PAGE or not last or body.get("has_more") is False:
                break
            if last == after:
                raise FoundryStorageError(
                    f"Foundry storage {path} pagination did not advance past {last!r}."
                )
            after = last
        return items

    async def update(self, record: StoredResponse, *, expected_status: str) -> bool:
        """Replace the response when its stored status matches.

        Foundry storage has no conditional write, so this reads then writes and
        is **not** atomic across replicas; Castia never relies on it for
        ``background=true`` with this store.
        """
        response = record["response"]
        current = await self._request(
            "GET", f"responses/{_encode(response['id'])}", not_found_ok=True
        )
        if current is None:
            return False
        stored = self._json(current, "responses")
        if not isinstance(stored, dict) or stored.get("status") != expected_status:
            return False
        await self.replace(response)
        return True

    async def delete(self, response_id: str) -> bool:
        response = await self._request(
            "DELETE", f"responses/{_encode(response_id)}", not_found_ok=True
        )
        return response is not None

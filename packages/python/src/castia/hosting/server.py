"""The hidden wiring: build the FastAPI app and serve every protocol.

Everything a customer should never read lives here -- the route table, the
surface-to-predicate mapping, the Activity endpoint that parses an inbound turn
and posts its reply back through the connector, and the native wire endpoints.
``Agent.run`` imports this module only *after* telemetry is configured (FastAPI
and uvicorn are cheap, uninstrumented imports, so pulling them here is safe).

This replaces the old ``aiohttp`` + ``CloudAdapter`` + ``AgentApplication`` stack
with plain FastAPI. The Activity protocol is handled directly:

* parse the inbound Bot Framework Activity (:class:`castia.protocols.activity.Activity`),
* route it to the first handler whose Teams-surface predicate matches,
* run the handler and POST its reply out-of-band via
  :func:`castia.messaging.connector.send_reply` (the connector chooses the reply
  identity by turn type),
* return ``200`` -- always, so a handler error or an unsupported activity never
  becomes a platform-visible failure.

The wire protocols (responses / chat / invocations) are request/response: the
handler's string result is wrapped in the protocol's envelope and returned as the
HTTP body.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from ipaddress import ip_address
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from castia.hosting.responses_store import (
    NON_TERMINAL_STATUSES,
    ResponseHistoryStore,
    ResponseStore,
    StoredResponse,
    _ProcessLocalResponseCache,
)
from castia.hosting.snapshot import register_snapshot_routes
from castia.messaging.connector import send_reply
from castia.messaging.routing import (
    teams_direct_message,
    teams_group_chat_message,
    teams_tagged_channel_message,
)
from castia.messaging.surfaces import Teams
from castia.observe import dev_diagnostics
from castia.protocols.activity import Activity
from castia.runtime.context import turn_scope
from castia.runtime.dispatch import (
    make_dispatch,
    make_invoke_dispatch,
    make_return_dispatch,
    make_stream_dispatch,
)
from castia.runtime.history import bind_response_history, model_input_items
from castia.runtime.request_context import (
    FOUNDRY_CALL_ID_HEADER,
    FOUNDRY_SESSION_ID_HEADER,
    FOUNDRY_USER_ID_HEADER,
    RequestContext,
    current_request_context,
    reset_request_context,
    set_request_context,
)
from castia.runtime.usage import TurnUsage, usage_scope

logger = logging.getLogger(__name__)
# The root logger sits at WARNING, so INFO records (including the unknown-invoke
# ack below) are dropped before the OTel handler can export them to App Insights.
# Pin this module to INFO so invoke routing is observable in the traces table.
logger.setLevel(logging.INFO)

_PREDICATES: dict[Teams, Callable[[Activity], bool]] = {
    Teams.direct: teams_direct_message,
    Teams.group: teams_group_chat_message,
    Teams.channel_mention: teams_tagged_channel_message,
}
_ROUTE_PATHS = {
    "activity": "/activity/messages",
    "responses": "/responses",
    "invocations": "/invocations",
    "chat": "/chat/completions",
}
_ACTIVITY_ID_HEADER = "x-agent-activity-id"
_ACTIVITY_SESSION_ID_QUERY_PARAM = "agent_session_id"
_MAX_ACTIVITY_ID_LENGTH = 256
_VALID_ACTIVITY_ID = re.compile(r"^[a-zA-Z0-9\-_.:]+\Z")
_COMMON_ENV_CHECKS = (
    "FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "TOOLBOX_ENDPOINT",
    "OPTIMIZATION_CANDIDATE_ID",
)
_MAX_STORED_RESPONSES = 512


def _any_of(surfaces: tuple[Teams, ...]) -> Callable[[Activity], bool]:
    """Combine the selected surface predicates with OR."""
    predicates = [_PREDICATES[s] for s in surfaces]
    return lambda activity: any(predicate(activity) for predicate in predicates)


def _header_value(request: Request, name: str) -> str | None:
    """Return a non-empty platform header value, or ``None``."""
    if name not in request.headers:
        return None
    value = request.headers[name].strip()
    return value or None


def _valid_activity_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if not value or len(value) > _MAX_ACTIVITY_ID_LENGTH:
        return None
    if not _VALID_ACTIVITY_ID.fullmatch(value):
        return None
    return value


def _activity_response_headers(
    request: Request,
    payload: dict | None,
    *,
    session_id: str | None = None,
) -> dict[str, str]:
    activity_id = _valid_activity_id((payload or {}).get("id")) or str(uuid4())
    return {
        _ACTIVITY_ID_HEADER: activity_id,
        FOUNDRY_SESSION_ID_HEADER: session_id or _activity_session_id(request),
    }


def _activity_session_id(request: Request) -> str:
    return (
        _valid_activity_id(request.query_params.get(_ACTIVITY_SESSION_ID_QUERY_PARAM))
        or _valid_activity_id(_header_value(request, FOUNDRY_SESSION_ID_HEADER))
        or str(uuid4())
    )


@contextmanager
def _request_scope(
    request: Request,
    *,
    session_id: str | None = None,
) -> Iterator[RequestContext]:
    context = RequestContext(
        call_id=_header_value(request, FOUNDRY_CALL_ID_HEADER),
        user_id=_header_value(request, FOUNDRY_USER_ID_HEADER),
        session_id=session_id or _header_value(request, FOUNDRY_SESSION_ID_HEADER),
    )
    token = set_request_context(context)
    try:
        yield context
    finally:
        reset_request_context(token)


def _responses_body(
    text: str,
    *,
    response_id: str | None = None,
    output_item_id: str | None = None,
    status: str = "completed",
    created_at: int | None = None,
    error: dict | None = None,
    model: str | None = None,
    instructions: object = None,
    metadata: dict | None = None,
    tools: list | None = None,
    tool_choice: object = None,
    parallel_tool_calls: bool | None = None,
    usage: TurnUsage | None = None,
    previous_response_id: str | None = None,
    conversation: dict | None = None,
) -> dict:
    """An OpenAI Responses-shaped payload carrying ``text``.

    Minimal but real: ``output_text`` for simple readers and a structured
    ``output[]`` for clients that walk the content array. ``id`` lets clients
    retrieve this completed response from the process-local lifecycle store.
    """
    content_part = {"type": "output_text", "text": text}
    message = {
        "type": "message",
        "role": "assistant",
        "content": [content_part],
    }
    if output_item_id is not None:
        content_part["annotations"] = []
        content_part["logprobs"] = []
        message["id"] = output_item_id
        message["status"] = "incomplete" if status == "failed" else status

    return {
        "id": response_id or f"resp_{uuid4().hex}",
        "object": "response",
        "created_at": created_at or int(time.time()),
        "status": status,
        "error": error,
        "incomplete_details": None,
        "instructions": instructions,
        "metadata": metadata or {},
        "model": model or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "castia-agent",
        "output_text": text,
        "output": [message],
        "parallel_tool_calls": True
        if parallel_tool_calls is None
        else parallel_tool_calls,
        "previous_response_id": previous_response_id,
        "conversation": deepcopy(conversation),
        "tool_choice": "auto" if tool_choice is None else tool_choice,
        "tools": tools or [],
        "usage": None
        if status != "completed"
        else (usage or TurnUsage()).to_responses_usage(),
    }


def _responses_request_options(body: dict) -> dict:
    return {
        "model": body.get("model") if isinstance(body.get("model"), str) else None,
        "instructions": body.get("instructions")
        if isinstance(body.get("instructions"), str | list)
        else None,
        "metadata": deepcopy(body.get("metadata"))
        if isinstance(body.get("metadata"), dict)
        else {},
        "tools": deepcopy(body.get("tools")) if isinstance(body.get("tools"), list) else [],
        "tool_choice": _responses_tool_choice(body.get("tool_choice")),
        "parallel_tool_calls": body.get("parallel_tool_calls")
        if isinstance(body.get("parallel_tool_calls"), bool)
        else None,
    }


def _responses_tool_choice(value: object) -> object:
    if isinstance(value, str) and value in {"auto", "none", "required"}:
        return value
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return deepcopy(value)
    return None


def _responses_input_items(value, *, response_id: str) -> list[dict]:
    """Return a minimal input-items page payload source for a Responses request."""
    if isinstance(value, str):
        return [
            {
                "id": f"{response_id}_input_0",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": value}],
            }
        ]
    if not isinstance(value, list):
        return []

    items: list[dict] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append(
                {
                    "id": f"{response_id}_input_{index}",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": item}],
                }
            )
        elif isinstance(item, dict):
            normalized = dict(item)
            normalized["id"] = f"{response_id}_input_{index}"
            normalized.setdefault("type", "message")
            items.append(normalized)
    return items


def _responses_list(
    items: list[dict],
    *,
    limit: int = 20,
    order: str = "desc",
    after: str | None = None,
    before: str | None = None,
) -> dict:
    ordered = items if order == "asc" else list(reversed(items))
    if after is not None:
        ordered = _after_item(ordered, after)
    if before is not None:
        ordered = _before_item(ordered, before)
    page = ordered[:limit]
    return {
        "object": "list",
        "data": page,
        "first_id": page[0].get("id") if page else None,
        "last_id": page[-1].get("id") if page else None,
        "has_more": len(ordered) > limit,
    }


def _after_item(items: list[dict], item_id: str) -> list[dict]:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            return items[index + 1 :]
    return items


def _before_item(items: list[dict], item_id: str) -> list[dict]:
    for index, item in enumerate(items):
        if item.get("id") == item_id:
            return items[:index]
    return items


_BACKGROUND_STORE_REQUIRED = (
    "background=true requires a response store shared by every replica. "
    "Pass one to Agent(response_store=...); see the castia README section "
    "'Background responses'."
)
_STORE_UNAVAILABLE = "The response store is unavailable. Retry the request."
_STORE_WRITE_ATTEMPTS = 3


async def _save_terminal_response(
    responses_store: ResponseStore,
    response: dict,
    input_items: list[dict],
    *,
    user_id: str | None,
) -> None:
    """Persist a finished foreground response; storage failure never fails the turn."""
    record: StoredResponse = {
        "response": deepcopy(response),
        "input_items": input_items,
        "background": False,
        "user_id": user_id,
    }
    try:
        await responses_store.create(record)
    except Exception:
        logger.exception("Could not store response %s.", response.get("id"))


class _StoreWriteError(Exception):
    """A store write kept failing after retries."""


async def _transition(
    responses_store: ResponseStore,
    record: StoredResponse,
    *,
    expected_status: str,
    raise_errors: bool = False,
) -> bool:
    """Compare-and-set a record's status, retrying transient store failures.

    Returns ``False`` only when the compare-and-set loses. When the store keeps
    failing, raises :class:`_StoreWriteError` if ``raise_errors`` is set and
    otherwise logs and returns ``False``.
    """
    response_id = record["response"]["id"]
    for attempt in range(1, _STORE_WRITE_ATTEMPTS + 1):
        try:
            return await responses_store.update(record, expected_status=expected_status)
        except Exception as exc:
            if attempt == _STORE_WRITE_ATTEMPTS:
                if raise_errors:
                    raise _StoreWriteError(response_id) from exc
                logger.exception(
                    "Could not move background response %s from %s to %s.",
                    response_id,
                    expected_status,
                    record["response"].get("status"),
                )
                return False
            await asyncio.sleep(0.05 * attempt)
    return False


def _store_unavailable() -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": _STORE_UNAVAILABLE,
                "type": "server_error",
                "param": None,
                "code": "store_unavailable",
            }
        },
        status_code=503,
    )


def _background_snapshot(
    *,
    response_id: str,
    created_at: int,
    status: str,
    response_options: dict,
    error: dict | None = None,
) -> dict:
    """A non-completed background Responses object: empty output, no usage."""
    body = _responses_body(
        "",
        response_id=response_id,
        status=status,
        created_at=created_at,
        error=error,
        **response_options,
    )
    body["output"] = []
    body["usage"] = None
    body["background"] = True
    return body


_HISTORY_STORE_REQUIRED = (
    "previous_response_id requires a response store that keeps prior responses. "
    "Pass one to Agent(response_store=...); see the castia README section "
    "'Conversation history'."
)
_HISTORY_MAX_HOPS = 100
_HISTORY_MAX_ITEMS = 1000


class _HistoryError(Exception):
    """A history lookup that must end the request with ``response``."""

    def __init__(self, response: JSONResponse) -> None:
        super().__init__(response.status_code)
        self.response = response


def _history_request(body: dict) -> tuple[str | None, str | None] | JSONResponse:
    """Validate ``previous_response_id`` / ``conversation``; return their ids."""
    previous = body.get("previous_response_id")
    if previous is not None and (not isinstance(previous, str) or not previous):
        return _api_error(
            "previous_response_id must be a non-empty string.",
            param="previous_response_id",
        )
    conversation = body.get("conversation")
    conversation_id = None
    if conversation is not None:
        if isinstance(conversation, str):
            conversation_id = conversation
        elif isinstance(conversation, dict) and isinstance(conversation.get("id"), str):
            conversation_id = conversation["id"]
        if not conversation_id:
            return _api_error(
                "conversation must be a conversation id or an object with an id.",
                param="conversation",
            )
    if previous is not None and conversation_id is not None:
        return _api_error(
            "previous_response_id and conversation cannot be used together.",
            param="conversation",
        )
    return previous, conversation_id


def _inline_history(value) -> list:
    """Items a client sent in ``input`` before the latest user message."""
    index, _ = _current_user_item(value)
    return value[:index] if index is not None else []


def _response_items(record: StoredResponse) -> list:
    return list(record.get("input_items") or []) + list(
        record["response"].get("output") or []
    )


async def _stored_history(
    store: ResponseStore | None,
    *,
    previous_response_id: str | None,
    conversation_id: str | None,
    owner: str | None,
) -> tuple[list[list], bool]:
    """Prior responses' items, grouped per response and oldest first.

    Returns ``(groups, truncated)``. Raises :class:`_HistoryError` for a
    missing, foreign, or unfinished ``previous_response_id``.
    """
    if conversation_id is not None:
        if not isinstance(store, ResponseHistoryStore):
            logger.info(
                "Ignoring Responses conversation %r: the response store does not "
                "implement castia.ResponseHistoryStore.",
                conversation_id,
            )
            _set_span_attribute("castia.responses.conversation_ignored", True)
            return [], False
        groups = await store.get_history(
            previous_response_id=None,
            conversation_id=conversation_id,
            user_id=owner,
            limit=_HISTORY_MAX_ITEMS,
        )
        return _history_groups(groups), False
    if previous_response_id is None or store is None:
        return [], False

    anchor = await store.get(previous_response_id)
    if anchor is None or anchor.get("user_id") != owner:
        raise _HistoryError(_not_found_response(previous_response_id))
    status = anchor["response"].get("status")
    if status in NON_TERMINAL_STATUSES:
        raise _HistoryError(
            _api_error(
                f"Previous response {previous_response_id!r} is still {status}; "
                "wait until it finishes.",
                param="previous_response_id",
            )
        )
    if isinstance(store, ResponseHistoryStore):
        groups = await store.get_history(
            previous_response_id=previous_response_id,
            conversation_id=None,
            user_id=owner,
            limit=_HISTORY_MAX_ITEMS,
        )
        return _history_groups(groups), False

    chain: list[StoredResponse] = []
    seen: set[str] = set()
    truncated = False
    record: StoredResponse | None = anchor
    while record is not None:
        response_id = record["response"].get("id")
        if response_id in seen:
            truncated = True
            break
        seen.add(response_id)
        chain.append(record)
        link = record["response"].get("previous_response_id")
        if not link:
            break
        if len(chain) >= _HISTORY_MAX_HOPS:
            truncated = True
            break
        record = await store.get(link)
        if record is None or record.get("user_id") != owner:
            # A deleted or foreign link ends the history; never cross users.
            truncated = True
            record = None
    return [_response_items(item) for item in reversed(chain)], truncated


def _history_groups(groups) -> list[list]:
    """Validate a ``ResponseHistoryStore.get_history`` result: one list per response."""
    if not isinstance(groups, list) or not all(isinstance(g, list) for g in groups):
        raise TypeError(
            "ResponseHistoryStore.get_history must return a list of per-response "
            "item lists, oldest first."
        )
    return [list(group) for group in groups]


def _capped_history(groups: list[list]) -> tuple[list[dict], bool]:
    """Normalize history and drop the oldest whole responses past the item cap.

    Groups are flattened before normalizing so a tool call in one response
    still pairs with its output in the next.
    """
    groups = [group for group in groups if group]
    truncated = False
    items = model_input_items(item for group in groups for item in group)
    while len(groups) > 1 and len(items) > _HISTORY_MAX_ITEMS:
        groups.pop(0)
        truncated = True
        items = model_input_items(item for group in groups for item in group)
    if len(items) > _HISTORY_MAX_ITEMS:
        items = model_input_items(items[-_HISTORY_MAX_ITEMS:])
        truncated = True
    return items, truncated


async def _resolve_history(
    store: ResponseStore | None,
    body: dict,
    *,
    previous_response_id: str | None,
    conversation_id: str | None,
    owner: str | None,
) -> list[dict]:
    groups, truncated = await _stored_history(
        store,
        previous_response_id=previous_response_id,
        conversation_id=conversation_id,
        owner=owner,
    )
    items, capped = _capped_history([*groups, _inline_history(body.get("input"))])
    _set_span_attribute("castia.responses.history.item_count", len(items))
    if truncated or capped:
        _set_span_attribute("castia.responses.history.truncated", True)
    return items


def _set_span_attribute(name: str, value) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute(name, value)
    except Exception:
        logger.debug("Could not set span attribute %s.", name, exc_info=True)


def _api_error(
    message: str,
    *,
    code: str = "invalid_request",
    param: str | None = None,
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "param": param,
                "code": code,
            }
        },
        status_code=status_code,
    )


def _not_found_response(response_id: str) -> JSONResponse:
    return _api_error(
        f"Response {response_id!r} was not found.",
        code="not_found",
        param="response_id",
        status_code=404,
    )


def _sse_event(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _sse_done() -> str:
    return "data: [DONE]\n\n"


def _record_stream_attributes(
    *,
    started: float,
    first_chunk_at: float | None,
    last_chunk_at: float | None,
    chunk_count: int,
    bytes_sent: int,
    terminal_status: str | None = None,
) -> None:
    """Attach aggregate streaming transport facts to the active request span."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if not span or not span.is_recording():
            return
        attributes = {
            "stream.chunk_count": chunk_count,
            "stream.bytes_sent": bytes_sent,
        }
        if terminal_status is not None:
            attributes["stream.terminal_status"] = terminal_status
        if first_chunk_at is not None:
            attributes["stream.first_chunk_ms"] = round(
                (first_chunk_at - started) * 1000
            )
        if last_chunk_at is not None:
            attributes["stream.last_chunk_ms"] = round((last_chunk_at - started) * 1000)
        span.set_attributes(attributes)
        if terminal_status == "failed":
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, "response.failed"))
    except Exception:  # pragma: no cover - telemetry must never break streaming
        logger.debug("Failed to record streaming attributes", exc_info=True)


def _responses_completed_event(
    text: str,
    *,
    response_id: str,
    output_item_id: str,
    created_at: int,
    response_options: dict,
    usage: TurnUsage | None = None,
) -> dict:
    body = _responses_body(
        text,
        response_id=response_id,
        output_item_id=output_item_id,
        created_at=created_at,
        usage=usage,
        **response_options,
    )
    return {"type": "response.completed", "response": body, **body}


def _responses_failed_event(
    text: str,
    *,
    response_id: str,
    output_item_id: str,
    created_at: int,
    response_options: dict,
    message: str = "An internal server error occurred.",
) -> dict:
    body = _responses_body(
        text,
        response_id=response_id,
        output_item_id=output_item_id,
        status="failed",
        created_at=created_at,
        error={"code": "server_error", "message": message},
        **response_options,
    )
    return {"type": "response.failed", "response": body, **body}


def _streaming_response(
    text: str,
    *,
    response_id: str,
    status: str,
    created_at: int,
    response_options: dict,
) -> dict:
    body = _responses_body(
        text,
        response_id=response_id,
        status=status,
        created_at=created_at,
        **response_options,
    )
    body["output"] = []
    return body


def _streaming_item(item_id: str, text: str, *, status: str) -> dict:
    content = []
    if text or status == "completed":
        content = [
            {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
        ]
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": content,
    }


def _streaming_content_part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": [], "logprobs": []}


def _chat_body(text: str) -> dict:
    """An OpenAI Chat Completions-shaped payload carrying ``text``."""
    return {
        "id": f"chatcmpl_{uuid4().hex}",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def _last_user_text(messages) -> str:
    """The content of the last user turn in a Chat Completions ``messages`` array."""
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return message.get("content") or ""
    return ""


def _responses_input(value) -> str:
    """The user text from an OpenAI Responses ``input`` field.

    ``input`` is polymorphic: a plain string, or a list of input items (the
    shape the Responses API, the eval harness, and the Agent Optimizer all
    send). For the list form, return the text of the last user item, flattening
    structured content parts (``[{type:"input_text", text:...}]``) to a string.
    A CLI ``invoke`` sends the string form, so this is the path exercised only
    by real Responses-API clients.
    """
    if isinstance(value, str):
        return value
    return _current_user_item(value)[1]


def _input_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict)
            and part.get("type") in ("input_text", "output_text", "text")
        )
    return ""


def _current_user_item(value) -> tuple[int | None, str]:
    """Index and text of the current user turn in a list ``input``.

    The last non-empty user item wins (a bare string counts as a user item);
    otherwise the last item with any text, so a role-less item still resolves.
    Items before the returned index are the turn's inline history.
    """
    if not isinstance(value, list):
        return None, ""
    fallback: tuple[int | None, str] = (None, "")
    for index in range(len(value) - 1, -1, -1):
        item = value[index]
        if isinstance(item, str):
            if item:
                return index, item
            continue
        if not isinstance(item, dict):
            continue
        text = _input_content_text(item.get("content"))
        if item.get("role") == "user" and text:
            return index, text
        if text and fallback[0] is None:
            fallback = (index, text)
    return fallback


def _invocations_body(text: str) -> dict:
    """A minimal Invocations response payload carrying ``text``.

    Invocations is pass-through: Foundry mandates no response schema, so we
    define our own honest, minimal shape rather than borrow the OpenAI envelope.
    """
    return {"output": text}


def _invocations_input(body) -> str:
    """The user text from an Invocations request body.

    Mirrors Foundry's own sample: accept a JSON object with ``message`` or
    ``input``, or a bare string body.
    """
    if isinstance(body, dict):
        return body.get("message") or body.get("input") or ""
    if isinstance(body, str):
        return body
    return ""


def _strip_leading_recipient_mentions(activity: Activity) -> tuple[str, bool]:
    """Remove leading Teams mention markup for this agent from activity text."""
    text = activity.text or ""
    recipient_id = activity.recipient.id if activity.recipient else None
    removed = False
    if not recipient_id:
        return text, removed

    mentions = [
        mention.text or ""
        for mention in activity.get_mentions()
        if mention.mentioned is not None and mention.mentioned.id == recipient_id
    ]
    while mentions:
        stripped = text.lstrip()
        for mention_text in sorted(mentions, key=len, reverse=True):
            if mention_text and stripped.startswith(mention_text):
                text = stripped.removeprefix(mention_text)
                removed = True
                break
        else:
            break
    return text, removed


def _command_text(activity: Activity) -> tuple[str, bool] | None:
    """Return command text for supported Teams message surfaces."""
    if teams_direct_message(activity) or teams_group_chat_message(activity):
        return _strip_leading_recipient_mentions(activity)
    if teams_tagged_channel_message(activity):
        return _strip_leading_recipient_mentions(activity)
    return None


def _command_lookup(activity: Activity) -> tuple[str, str] | None:
    """Return ``(name, trigger)`` for a targeted command Activity."""
    candidate = _command_text(activity)
    if candidate is None:
        return None
    text, had_mention = candidate
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("/"):
        token = stripped[1:].split(maxsplit=1)[0].strip().lower()
        return (token, "slash") if token else None
    if had_mention:
        token = stripped.split(maxsplit=1)[0].strip().lower()
        return (token, "mention") if token else None
    return None


def _build_activity_routes(routes):
    """Compile ``(surfaces, handler)`` pairs into ``(predicate, dispatch)`` pairs."""
    compiled = []
    for surfaces, func in routes:
        compiled.append((_any_of(surfaces), make_dispatch(func)))
    return compiled


def _build_command_routes(commands) -> dict[str, tuple[tuple[str, ...], Callable]]:
    """Compile command routes into ``{name: (triggers, dispatch)}``."""
    return {
        name: (command.metadata.triggers, make_dispatch(command.handler))
        for name, command in (commands or {}).items()
    }


def _build_invoke_routes(invokes) -> dict[str, Callable]:
    """Compile ``{name: handler}`` into ``{name: invoke-dispatch}``."""
    return {name: make_invoke_dispatch(func) for name, func in (invokes or {}).items()}


def _register_activity(app: FastAPI, routes, invokes=None, commands=None) -> None:
    """Serve the Activity Protocol on ``POST /activity/messages``.

    The one endpoint carries both halves of the protocol: fire-and-forget
    ``message`` turns (routed by surface predicate, reply posted out-of-band) and
    request/response ``invoke`` turns (routed by ``activity.name``, answered
    synchronously in the HTTP body).
    """
    compiled = _build_activity_routes(routes)
    compiled_commands = _build_command_routes(commands)
    compiled_invokes = _build_invoke_routes(invokes)

    @app.post("/activity/messages")
    async def messages(request: Request) -> Response:
        session_id = _activity_session_id(request)
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - malformed JSON is a protocol error
            logger.error("Activity: unparseable request body.")
            return Response(
                status_code=400,
                headers=_activity_response_headers(
                    request, None, session_id=session_id
                ),
            )
        if not isinstance(payload, dict):
            return Response(
                status_code=400,
                headers=_activity_response_headers(request, None, session_id=session_id),
            )

        response_headers = _activity_response_headers(
            request, payload, session_id=session_id
        )

        with _request_scope(request, session_id=session_id):
            try:
                activity = Activity.model_validate(payload)
            except ValidationError:
                logger.error("Activity: payload failed validation.")
                return Response(status_code=400, headers=response_headers)

            try:
                # Invoke is request/response: the answer is the HTTP body, not an
                # out-of-band connector send. Route on the invoke name and return the
                # handler's InvokeResponse body (or an empty 200 ack when unhandled --
                # e.g. a feedbackLoop "default" submission we simply acknowledge).
                if activity.type == "invoke":
                    # Record the raw wire name so invoke routing is never a black box: a
                    # client whose name we don't recognize is diagnosable from traces.
                    logger.info("Invoke received name=%r", activity.name)
                    dispatch = compiled_invokes.get(activity.name)
                    if dispatch is None:
                        logger.info(
                            "Invoke name=%r has no handler; acking 200.",
                            activity.name,
                        )
                        return JSONResponse(
                            {}, status_code=200, headers=response_headers
                        )
                    with turn_scope(activity):
                        body = await dispatch(activity)
                    return JSONResponse(
                        body or {}, status_code=200, headers=response_headers
                    )

                # One turn context spans dispatch *and* the connector send, so reply
                # decorations the handler/tools accumulate (AI label, citations, ...)
                # are still present when the answer message is posted.
                with turn_scope(activity):
                    command = _command_lookup(activity)
                    command_route = (
                        compiled_commands.get(command[0]) if command else None
                    )
                    if command_route is not None and command[1] in command_route[0]:
                        command_dispatch = command_route[1]
                        reply = await command_dispatch(activity)
                        if reply:
                            await send_reply(activity, reply)
                        return Response(status_code=200, headers=response_headers)

                    for predicate, dispatch in compiled:
                        if predicate(activity):
                            reply = await dispatch(activity)
                            if reply:
                                await send_reply(activity, reply)
                            return Response(status_code=200, headers=response_headers)

                logger.info(
                    "Ignoring unsupported activity type=%r channel=%r",
                    activity.type,
                    activity.channel_id,
                )
                return Response(status_code=200, headers=response_headers)
            except Exception:
                logger.exception("Activity handler failed.")
                return Response(status_code=500, headers=response_headers)


def _current_span_context():
    """The active request span context, used to link background work to it."""
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        return context if context.is_valid else None
    except Exception:  # noqa: BLE001 - telemetry must never break a request
        return None


@contextmanager
def _background_span(response_id: str, link_context) -> Iterator[None]:
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - opentelemetry ships with castia
        yield
        return
    links = [trace.Link(link_context)] if link_context is not None else []
    with trace.get_tracer(__name__).start_as_current_span(
        "castia.responses.background",
        links=links,
        attributes={"castia.responses.id": response_id},
    ):
        yield


class _BackgroundResponses:
    """Runs ``background=true`` turns on this replica and records them in the store.

    Execution is process-bound: the consumer store makes state visible to every
    replica, but a restart abandons the work. Every write is a status
    compare-and-set, so a cancel or delete from any replica wins over a late
    completion.
    """

    def __init__(self, responses_store: ResponseStore, dispatch: Callable) -> None:
        self._store = responses_store
        self._dispatch = dispatch
        self._tasks: dict[str, asyncio.Task] = {}

    def start(
        self,
        record: StoredResponse,
        *,
        text: str,
        history: list[dict],
        context: RequestContext,
        response_options: dict,
        link_context,
    ) -> None:
        response = record["response"]
        response_id = response["id"]
        # A fresh context keeps request-scoped usage/diagnostics/span state out
        # of the background turn; the request context is re-bound explicitly.
        task = asyncio.create_task(
            self._run(
                response_id,
                created_at=response["created_at"],
                input_items=record["input_items"],
                text=text,
                history=history,
                context=context,
                response_options=response_options,
                link_context=link_context,
            ),
            context=contextvars.Context(),
        )
        self._tasks[response_id] = task
        task.add_done_callback(lambda done: self._finished(response_id, done))

    def _finished(self, response_id: str, task: asyncio.Task) -> None:
        if self._tasks.get(response_id) is task:
            del self._tasks[response_id]
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "Background response %s worker crashed.",
                response_id,
                exc_info=task.exception(),
            )

    def cancel_local(self, response_id: str) -> None:
        """Stop the worker if it runs on this replica (best effort)."""
        task = self._tasks.get(response_id)
        if task is not None:
            task.cancel()

    async def shutdown(self, grace_seconds: float = 5.0) -> None:
        """Cancel local workers so their records end ``failed``, not stuck."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=grace_seconds)

    async def _run(
        self,
        response_id: str,
        *,
        created_at: int,
        input_items: list[dict],
        text: str,
        history: list[dict],
        context: RequestContext,
        response_options: dict,
        link_context,
    ) -> None:
        def record(response: dict) -> StoredResponse:
            return {
                "response": response,
                "input_items": input_items,
                "background": True,
                "user_id": context.user_id,
            }

        def snapshot(status: str, error: dict | None = None) -> StoredResponse:
            return record(
                _background_snapshot(
                    response_id=response_id,
                    created_at=created_at,
                    status=status,
                    response_options=response_options,
                    error=error,
                )
            )

        status = "queued"
        token = set_request_context(context)
        try:
            with _background_span(response_id, link_context):
                if not await _transition(
                    self._store, snapshot("in_progress"), expected_status=status
                ):
                    return
                status = "in_progress"
                try:
                    with bind_response_history(history), dev_diagnostics.turn(
                        "responses", text
                    ), usage_scope() as usage:
                        reply = await self._dispatch(text)
                        dev_diagnostics.record_output(reply)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Background response %s failed.", response_id)
                    await _transition(
                        self._store,
                        snapshot(
                            "failed",
                            {
                                "code": "server_error",
                                "message": "An internal server error occurred.",
                            },
                        ),
                        expected_status=status,
                    )
                    return
                completed = _responses_body(
                    reply,
                    response_id=response_id,
                    output_item_id=f"msg_{uuid4().hex}",
                    created_at=created_at,
                    usage=usage,
                    **response_options,
                )
                completed["background"] = True
                await _transition(self._store, record(completed), expected_status=status)
        except asyncio.CancelledError:
            # A cancel request already moved the record to ``cancelled`` (so this
            # compare-and-set loses); otherwise the process is stopping.
            await _transition(
                self._store,
                snapshot(
                    "failed",
                    {
                        "code": "server_error",
                        "message": "The agent stopped before the response finished.",
                    },
                ),
                expected_status=status,
            )
            raise
        finally:
            reset_request_context(token)


def _register_wire(
    app: FastAPI,
    wire: dict,
    responses_store: ResponseStore,
    *,
    background_store: ResponseStore | None = None,
) -> _BackgroundResponses | None:
    """Serve each registered wire-protocol handler on its native endpoint.

    Returns the background executor when a consumer store enables
    ``background=true``, so the app lifespan can stop it on shutdown.
    """
    background_runner: _BackgroundResponses | None = None
    responses_handler = wire.get("responses")
    if responses_handler is not None:
        responses_dispatch = make_return_dispatch(responses_handler)
        responses_stream_handler = wire.get("responses_stream")
        responses_stream_dispatch = (
            make_stream_dispatch(responses_stream_handler)
            if responses_stream_handler is not None
            else None
        )
        if background_store is not None:
            background_runner = _BackgroundResponses(background_store, responses_dispatch)

        @app.post("/responses")
        async def responses(request: Request) -> Response:
            with _request_scope(request) as request_context:
                owner = request_context.user_id
                try:
                    body = await request.json()
                except Exception:  # noqa: BLE001 - malformed JSON is a protocol error
                    return _api_error(
                        "request body must be a JSON object",
                        param="body",
                    )
                if not isinstance(body, dict):
                    return _api_error(
                        "request body must be a JSON object",
                        param="body",
                    )
                background = body.get("background") is True
                if background:
                    if background_runner is None:
                        return _api_error(
                            _BACKGROUND_STORE_REQUIRED,
                            code="unsupported_parameter",
                            param="background",
                        )
                    if body.get("store") is False:
                        return _api_error(
                            "background=true requires store=true.",
                            code="unsupported_parameter",
                            param="background",
                        )
                    if body.get("stream") is True:
                        return _api_error(
                            "background=true with stream=true is not supported by "
                            "this Castia runtime; omit stream and poll "
                            "GET /responses/{response_id}.",
                            code="unsupported_parameter",
                            param="stream",
                        )
                parsed_history = _history_request(body)
                if isinstance(parsed_history, JSONResponse):
                    return parsed_history
                previous_response_id, conversation_id = parsed_history
                if previous_response_id is not None and background_store is None:
                    return _api_error(
                        _HISTORY_STORE_REQUIRED,
                        code="unsupported_parameter",
                        param="previous_response_id",
                    )
                try:
                    history = await _resolve_history(
                        background_store,
                        body,
                        previous_response_id=previous_response_id,
                        conversation_id=conversation_id,
                        owner=owner,
                    )
                except _HistoryError as error:
                    return error.response
                except Exception:
                    logger.exception("Could not read Responses history.")
                    return _store_unavailable()
                text = _responses_input(body.get("input"))
                response_options = _responses_request_options(body)
                response_options["previous_response_id"] = previous_response_id
                response_options["conversation"] = (
                    {"id": conversation_id} if conversation_id is not None else None
                )
                store = body.get("store") is not False
                if background:
                    response_id = f"resp_{uuid4().hex}"
                    record: StoredResponse = {
                        "response": _background_snapshot(
                            response_id=response_id,
                            created_at=int(time.time()),
                            status="queued",
                            response_options=response_options,
                        ),
                        "input_items": _responses_input_items(
                            body.get("input"), response_id=response_id
                        ),
                        "background": True,
                        "user_id": owner,
                    }
                    queued = deepcopy(record["response"])
                    try:
                        await background_store.create(record)
                    except Exception:
                        logger.exception("Could not store background response.")
                        return _store_unavailable()
                    background_runner.start(
                        record,
                        text=text,
                        history=history,
                        context=current_request_context(),
                        response_options=response_options,
                        link_context=_current_span_context(),
                    )
                    return JSONResponse(queued)
                if body.get("stream") is True:

                    async def events():
                        started = time.perf_counter()
                        first_chunk_at: float | None = None
                        last_chunk_at: float | None = None
                        chunk_count = 0
                        bytes_sent = 0
                        sequence_number = 0
                        terminal_status: str | None = None
                        terminal_emitted = False

                        def emit_raw(event: str) -> str:
                            nonlocal first_chunk_at, last_chunk_at, chunk_count
                            nonlocal bytes_sent
                            now = time.perf_counter()
                            first_chunk_at = first_chunk_at or now
                            last_chunk_at = now
                            chunk_count += 1
                            bytes_sent += len(event.encode("utf-8"))
                            return event

                        def emit(event_type: str, payload: dict) -> str:
                            nonlocal sequence_number
                            payload["sequence_number"] = sequence_number
                            sequence_number += 1
                            return emit_raw(_sse_event(event_type, payload))

                        response_id = f"resp_{uuid4().hex}"
                        output_item_id = f"msg_{uuid4().hex}"
                        created_at = int(time.time())
                        input_items = _responses_input_items(
                            body.get("input"), response_id=response_id
                        )
                        chunks: list[str] = []
                        try:
                            with _request_scope(request), bind_response_history(
                                history
                            ), dev_diagnostics.turn(
                                "responses", text
                            ), usage_scope() as turn_usage:
                                yield emit(
                                    "response.created",
                                    {
                                        "type": "response.created",
                                        "response": _streaming_response(
                                            "",
                                            response_id=response_id,
                                            status="in_progress",
                                            created_at=created_at,
                                            response_options=response_options,
                                        ),
                                    },
                                )
                                yield emit(
                                    "response.in_progress",
                                    {
                                        "type": "response.in_progress",
                                        "response": _streaming_response(
                                            "",
                                            response_id=response_id,
                                            status="in_progress",
                                            created_at=created_at,
                                            response_options=response_options,
                                        ),
                                    },
                                )
                                yield emit(
                                    "response.output_item.added",
                                    {
                                        "type": "response.output_item.added",
                                        "output_index": 0,
                                        "item": _streaming_item(
                                            output_item_id, "", status="in_progress"
                                        ),
                                    },
                                )
                                yield emit(
                                    "response.content_part.added",
                                    {
                                        "type": "response.content_part.added",
                                        "item_id": output_item_id,
                                        "output_index": 0,
                                        "content_index": 0,
                                        "part": _streaming_content_part(""),
                                    },
                                )
                                if responses_stream_dispatch is not None:
                                    async for delta in responses_stream_dispatch(text):
                                        chunks.append(delta)
                                        yield emit(
                                            "response.output_text.delta",
                                            {
                                                "type": "response.output_text.delta",
                                                "item_id": output_item_id,
                                                "output_index": 0,
                                                "content_index": 0,
                                                "delta": delta,
                                                "logprobs": [],
                                            },
                                        )
                                else:
                                    reply = await responses_dispatch(text)
                                    if reply:
                                        chunks.append(reply)
                                        yield emit(
                                            "response.output_text.delta",
                                            {
                                                "type": "response.output_text.delta",
                                                "item_id": output_item_id,
                                                "output_index": 0,
                                                "content_index": 0,
                                                "delta": reply,
                                                "logprobs": [],
                                            },
                                        )
                                output_text = "".join(chunks)
                                dev_diagnostics.record_output(output_text)
                                yield emit(
                                    "response.output_text.done",
                                    {
                                        "type": "response.output_text.done",
                                        "item_id": output_item_id,
                                        "output_index": 0,
                                        "content_index": 0,
                                        "text": output_text,
                                        "logprobs": [],
                                    },
                                )
                                yield emit(
                                    "response.content_part.done",
                                    {
                                        "type": "response.content_part.done",
                                        "item_id": output_item_id,
                                        "output_index": 0,
                                        "content_index": 0,
                                        "part": _streaming_content_part(output_text),
                                    },
                                )
                                yield emit(
                                    "response.output_item.done",
                                    {
                                        "type": "response.output_item.done",
                                        "output_index": 0,
                                        "item": _streaming_item(
                                            output_item_id,
                                            output_text,
                                            status="completed",
                                        ),
                                    },
                                )
                                if store:
                                    await _save_terminal_response(
                                        responses_store,
                                        _responses_body(
                                            output_text,
                                            response_id=response_id,
                                            output_item_id=output_item_id,
                                            created_at=created_at,
                                            usage=turn_usage,
                                            **response_options,
                                        ),
                                        input_items,
                                        user_id=owner,
                                    )
                                yield emit(
                                    "response.completed",
                                    _responses_completed_event(
                                        output_text,
                                        response_id=response_id,
                                        output_item_id=output_item_id,
                                        created_at=created_at,
                                        response_options=response_options,
                                        usage=turn_usage,
                                    ),
                                )
                                terminal_status = "completed"
                                terminal_emitted = True
                                yield emit_raw(_sse_done())
                        except Exception:
                            logger.exception("Responses stream handler failed.")
                            if terminal_emitted:
                                return
                            output_text = "".join(chunks)
                            failed_event = _responses_failed_event(
                                output_text,
                                response_id=response_id,
                                output_item_id=output_item_id,
                                created_at=created_at,
                                response_options=response_options,
                            )
                            if store:
                                await _save_terminal_response(
                                    responses_store,
                                    failed_event["response"],
                                    input_items,
                                    user_id=owner,
                                )
                            terminal_status = "failed"
                            yield emit("response.failed", failed_event)
                            terminal_emitted = True
                            yield emit_raw(_sse_done())
                        finally:
                            _record_stream_attributes(
                                started=started,
                                first_chunk_at=first_chunk_at,
                                last_chunk_at=last_chunk_at,
                                chunk_count=chunk_count,
                                bytes_sent=bytes_sent,
                                terminal_status=terminal_status,
                            )

                    return StreamingResponse(events(), media_type="text/event-stream")

                with bind_response_history(history), dev_diagnostics.turn(
                    "responses", text
                ), usage_scope() as turn_usage:
                    reply = await responses_dispatch(text)
                    dev_diagnostics.record_output(reply)
                response_id = f"resp_{uuid4().hex}"
                output_item_id = f"msg_{uuid4().hex}"
                created_at = int(time.time())
                body_out = _responses_body(
                    reply,
                    response_id=response_id,
                    output_item_id=output_item_id,
                    created_at=created_at,
                    usage=turn_usage,
                    **response_options,
                )
                if store:
                    await _save_terminal_response(
                        responses_store,
                        body_out,
                        _responses_input_items(body.get("input"), response_id=response_id),
                        user_id=owner,
                    )
                return JSONResponse(body_out)

        logger.info("Serving responses protocol on POST /responses")

        async def _load(
            response_id: str, request: Request
        ) -> StoredResponse | JSONResponse:
            try:
                record = await responses_store.get(response_id)
            except Exception:
                logger.exception("Could not read response %s.", response_id)
                return _store_unavailable()
            # Another user's record is reported as missing, never as forbidden.
            caller = _header_value(request, FOUNDRY_USER_ID_HEADER)
            if record is None or record.get("user_id") != caller:
                return _not_found_response(response_id)
            return record

        @app.get("/responses/{response_id}")
        async def get_response(response_id: str, request: Request) -> Response:
            if request.query_params.get("stream", "").lower() == "true":
                return _api_error(
                    "Streaming a stored response is not supported by this Castia "
                    "runtime; omit stream to read the current response.",
                    code="unsupported_parameter",
                    param="stream",
                )
            record = await _load(response_id, request)
            if isinstance(record, JSONResponse):
                return record
            return JSONResponse(record["response"])

        @app.delete("/responses/{response_id}")
        async def delete_response(response_id: str, request: Request) -> Response:
            record = await _load(response_id, request)
            if isinstance(record, JSONResponse):
                return record
            try:
                deleted = await responses_store.delete(response_id)
            except Exception:
                logger.exception("Could not delete response %s.", response_id)
                return _store_unavailable()
            if not deleted:
                return _not_found_response(response_id)
            if background_runner is not None:
                background_runner.cancel_local(response_id)
            return JSONResponse(
                {"id": response_id, "object": "response", "deleted": True},
                status_code=200,
            )

        @app.post("/responses/{response_id}/cancel")
        async def cancel_response(response_id: str, request: Request) -> Response:
            record = await _load(response_id, request)
            if isinstance(record, JSONResponse):
                return record
            status = record["response"].get("status")
            # Status only moves forward (queued -> in_progress -> terminal), so a
            # cancel that loses to the worker retries at most once more.
            for _ in range(len(NON_TERMINAL_STATUSES)):
                if not (record.get("background") and status in NON_TERMINAL_STATUSES):
                    break
                cancelled = deepcopy(record["response"])
                cancelled.update(
                    status="cancelled", output=[], output_text="", usage=None
                )
                try:
                    swapped = await _transition(
                        responses_store,
                        {
                            "response": cancelled,
                            "input_items": record["input_items"],
                            "background": True,
                            "user_id": record.get("user_id"),
                        },
                        expected_status=status,
                        raise_errors=True,
                    )
                except _StoreWriteError:
                    logger.exception("Could not cancel response %s.", response_id)
                    return _store_unavailable()
                if swapped:
                    if background_runner is not None:
                        background_runner.cancel_local(response_id)
                    return JSONResponse(cancelled)
                record = await _load(response_id, request)
                if isinstance(record, JSONResponse):
                    return record
                status = record["response"].get("status")
            if record.get("background") and status == "cancelled":
                return JSONResponse(record["response"])
            return _api_error(
                f"Cannot cancel a {status} response.",
                code="unsupported_parameter",
                param="response_id",
            )

        @app.get("/responses/{response_id}/input_items")
        async def response_input_items(response_id: str, request: Request) -> Response:
            record = await _load(response_id, request)
            if isinstance(record, JSONResponse):
                return record
            try:
                limit = int(request.query_params.get("limit", "20"))
            except ValueError:
                return _api_error(
                    "limit must be an integer between 1 and 100",
                    param="limit",
                )
            if limit < 1 or limit > 100:
                return _api_error("limit must be between 1 and 100", param="limit")
            order = request.query_params.get("order", "desc").lower()
            if order not in {"asc", "desc"}:
                return _api_error("order must be 'asc' or 'desc'", param="order")
            return JSONResponse(
                _responses_list(
                    record["input_items"],
                    limit=limit,
                    order=order,
                    after=request.query_params.get("after"),
                    before=request.query_params.get("before"),
                )
            )

    chat_handler = wire.get("chat")
    if chat_handler is not None:
        chat_dispatch = make_return_dispatch(chat_handler)

        @app.post("/chat/completions")
        async def chat(request: Request) -> Response:
            with _request_scope(request):
                try:
                    body = await request.json()
                except Exception:  # noqa: BLE001 - malformed JSON is a protocol error
                    return _api_error(
                        "request body must be a JSON object",
                        param="body",
                    )
                if not isinstance(body, dict):
                    return _api_error(
                        "request body must be a JSON object",
                        param="body",
                    )
                text = _last_user_text(body.get("messages"))
                with dev_diagnostics.turn("chat", text):
                    reply = await chat_dispatch(text)
                    dev_diagnostics.record_output(reply)
                return JSONResponse(_chat_body(reply))

        logger.info("Serving chat protocol on POST /chat/completions")

    invocations_handler = wire.get("invocations")
    if invocations_handler is not None:
        invocations_dispatch = make_return_dispatch(invocations_handler)

        @app.post("/invocations")
        async def invocations(request: Request) -> Response:
            with _request_scope(request):
                # Pass-through: accept a JSON object ({"message"|"input"}) or a bare
                # text body, matching Foundry's Invocations sample.
                raw = await request.body()
                try:
                    body = json.loads(raw) if raw else ""
                except json.JSONDecodeError:
                    body = raw.decode("utf-8", errors="replace").strip()
                text = _invocations_input(body)
                with dev_diagnostics.turn("invocations", text):
                    reply = await invocations_dispatch(text)
                    dev_diagnostics.record_output(reply)
                return JSONResponse(_invocations_body(reply))

        logger.info("Serving invocations protocol on POST /invocations")

    return background_runner


def _registered_protocols(routes, wire=None, invokes=None, commands=None) -> list[str]:
    protocols: list[str] = []
    if routes or invokes or commands:
        protocols.append("activity")
    protocols.extend(
        protocol
        for protocol in ("responses", "invocations", "chat")
        if protocol in (wire or {})
    )
    return protocols


def _readiness_payload(
    routes,
    wire=None,
    invokes=None,
    commands=None,
    *,
    agent_name: str | None = None,
    required_env: tuple[str, ...] = (),
    diagnostics: bool = False,
) -> dict:
    env_names = tuple(dict.fromkeys((*_COMMON_ENV_CHECKS, *required_env)))
    environment = {
        name: {
            "present": bool(os.environ.get(name, "").strip()),
            "required": name in required_env,
        }
        for name in env_names
    }
    missing_required = [
        name
        for name, state in environment.items()
        if state["required"] and not state["present"]
    ]
    payload = {
        "status": "configuration_missing" if missing_required else "ok",
    }
    if not diagnostics:
        return payload

    protocols = _registered_protocols(routes, wire, invokes, commands)
    paths = {"readiness": "/readiness"}
    paths.update({protocol: _ROUTE_PATHS[protocol] for protocol in protocols})
    return payload | {
        "agent": {"name": agent_name},
        "protocols": protocols,
        "routes": paths,
        "invokes": sorted((invokes or {}).keys()),
        "commands": [
            {
                "name": command.metadata.name,
                "description": command.metadata.description,
                "triggers": list(command.metadata.triggers),
            }
            for command in (commands or {}).values()
        ],
        "configuration": {
            "environment": environment,
            "missing_required": missing_required,
        },
    }


def _is_loopback_host(value: str | None) -> bool:
    host = (value or "").split(":", 1)[0]
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return _is_loopback_host(host) and _is_loopback_host(request.url.hostname)


def _wants_readiness_diagnostics(request: Request) -> bool:
    return _is_loopback_request(request)


def build_app(
    routes,
    wire=None,
    invokes=None,
    commands=None,
    *,
    agent_name: str | None = None,
    required_env: tuple[str, ...] = (),
    response_store: ResponseStore | None = None,
) -> FastAPI:
    """Assemble the FastAPI app: readiness, the Activity endpoint, wire routes.

    ``response_store`` is the consumer-owned store that enables
    ``background=true``; without it, stored foreground responses fall back to a
    bounded process-local cache.
    """
    background_runner: _BackgroundResponses | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if background_runner is not None:
            await background_runner.shutdown()

    app = FastAPI(title="castia", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/readiness")
    async def readiness(request: Request) -> Response:
        body = _readiness_payload(
            routes,
            wire or {},
            invokes or {},
            commands or {},
            agent_name=agent_name,
            required_env=required_env,
            diagnostics=_wants_readiness_diagnostics(request),
        )
        return JSONResponse(
            body,
            status_code=200,
        )

    @app.get("/diagnostics/last-turn")
    async def diagnostics_last_turn(request: Request) -> Response:
        if not _is_loopback_request(request):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if not dev_diagnostics.enabled():
            return JSONResponse(
                {
                    "enabled": False,
                    "enable_with": dev_diagnostics.setting_name(),
                    "last_turn": None,
                },
                status_code=200,
            )
        return JSONResponse(
            {
                "enabled": True,
                "last_turn": dev_diagnostics.last_turn(),
            },
            status_code=200,
        )

    register_snapshot_routes(app)
    _register_activity(app, routes, invokes, commands)
    background_runner = _register_wire(
        app,
        wire or {},
        response_store or _ProcessLocalResponseCache(_MAX_STORED_RESPONSES),
        background_store=response_store,
    )
    return app


def _windows_port_owner(port: int) -> str | None:
    if os.name != "nt":
        return None
    command = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen | "
        "Select-Object -First 1; "
        "if ($c) { "
        "$p = Get-CimInstance Win32_Process -Filter "
        '"ProcessId=$($c.OwningProcess)"; '
        "[pscustomobject]@{Pid=$c.OwningProcess;Name=$p.Name} | "
        "ConvertTo-Json -Compress }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    pid = payload.get("Pid")
    name = payload.get("Name")
    if pid and name:
        return f"PID {pid} ({name})"
    if pid:
        return f"PID {pid}"
    return None


def _port_in_use_message(host: str, port: int) -> str:
    owner = _windows_port_owner(port)
    owner_text = f" Owned by {owner}." if owner else ""
    return (
        f"Cannot start Castia agent on {host}:{port}: port {port} is already in use."
        f"{owner_text} Stop that process or set PORT to a free value "
        '(for example, PowerShell: $env:PORT = "8089") before running the agent.'
    )


def _ensure_port_available(host: str, port: int) -> None:
    probe_host = "" if host in {"0.0.0.0", "::"} else host
    try:
        family = socket.getaddrinfo(
            probe_host or host,
            port,
            type=socket.SOCK_STREAM,
        )[0][0]
    except OSError:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((probe_host, port))
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or exc.errno in {48, 98, 10048}:
                message = _port_in_use_message(host, port)
            else:
                message = (
                    f"Cannot start Castia agent on {host}:{port}: {exc}. "
                    "Set HOST/PORT to a bindable local address."
                )
            logger.error(message)
            raise SystemExit(message) from exc


def serve(
    routes,
    wire=None,
    invokes=None,
    commands=None,
    *,
    host: str = "0.0.0.0",
    port: int = 8088,
    agent_name: str | None = None,
    required_env: tuple[str, ...] = (),
    response_store: ResponseStore | None = None,
) -> None:
    """Serve the Activity endpoint and any registered wire protocols."""
    logger.info("Starting agent %s on %s:%s...", agent_name or "<unnamed>", host, port)
    _ensure_port_available(host, port)
    uvicorn.run(
        build_app(
            routes,
            wire,
            invokes,
            commands,
            agent_name=agent_name,
            required_env=required_env,
            response_store=response_store,
        ),
        host=host,
        port=port,
        log_level="info",
    )

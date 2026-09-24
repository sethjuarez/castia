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

import json
import logging
import os
import socket
import subprocess
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ipaddress import ip_address
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

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
from castia.runtime.request_context import (
    FOUNDRY_CALL_ID_HEADER,
    FOUNDRY_SESSION_ID_HEADER,
    FOUNDRY_USER_ID_HEADER,
    RequestContext,
    reset_request_context,
    set_request_context,
)

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


@contextmanager
def _request_scope(request: Request) -> Iterator[RequestContext]:
    context = RequestContext(
        call_id=_header_value(request, FOUNDRY_CALL_ID_HEADER),
        user_id=_header_value(request, FOUNDRY_USER_ID_HEADER),
        session_id=_header_value(request, FOUNDRY_SESSION_ID_HEADER),
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
) -> dict:
    """An OpenAI Responses-shaped payload carrying ``text``.

    Minimal but real: ``output_text`` for simple readers and a structured
    ``output[]`` for clients that walk the content array. ``id`` lets a caller
    thread ``previous_response_id`` on a later turn.
    """
    content_part = {"type": "output_text", "text": text}
    message = {
        "type": "message",
        "role": "assistant",
        "content": [content_part],
    }
    if output_item_id is not None:
        content_part["annotations"] = []
        message["id"] = output_item_id
        message["status"] = status

    return {
        "id": response_id or f"resp_{uuid4().hex}",
        "object": "response",
        "status": status,
        "output_text": text,
        "output": [message],
    }


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


def _store_completed_response(
    responses_store: OrderedDict[str, dict],
    response_id: str,
    record: dict,
) -> None:
    responses_store[response_id] = record
    responses_store.move_to_end(response_id)
    while len(responses_store) > _MAX_STORED_RESPONSES:
        responses_store.popitem(last=False)


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
        if first_chunk_at is not None:
            attributes["stream.first_chunk_ms"] = round(
                (first_chunk_at - started) * 1000
            )
        if last_chunk_at is not None:
            attributes["stream.last_chunk_ms"] = round((last_chunk_at - started) * 1000)
        span.set_attributes(attributes)
    except Exception:  # pragma: no cover - telemetry must never break streaming
        logger.debug("Failed to record streaming attributes", exc_info=True)


def _responses_completed_event(
    text: str, *, response_id: str, output_item_id: str
) -> dict:
    body = _responses_body(
        text,
        response_id=response_id,
        output_item_id=output_item_id,
    )
    return {"type": "response.completed", "response": body, **body}


def _streaming_response(text: str, *, response_id: str, status: str) -> dict:
    body = _responses_body(text, response_id=response_id, status=status)
    body["output"] = []
    return body


def _streaming_item(item_id: str, text: str, *, status: str) -> dict:
    content = []
    if text or status == "completed":
        content = [{"type": "output_text", "text": text, "annotations": []}]
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": content,
    }


def _streaming_content_part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": []}


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
    if not isinstance(value, list):
        return ""

    def _content_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                part.get("text") or ""
                for part in content
                if isinstance(part, dict)
                and part.get("type") in ("input_text", "output_text", "text")
            ]
            return "".join(parts)
        return ""

    # Prefer the last user turn (mirrors ``_last_user_text``); fall back to the
    # last item with any text so a role-less input item still resolves.
    fallback = ""
    for item in reversed(value):
        if not isinstance(item, dict):
            if isinstance(item, str) and not fallback:
                fallback = item
            continue
        text = _content_text(item.get("content"))
        if item.get("role") == "user" and text:
            return text
        if text and not fallback:
            fallback = text
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


def _build_activity_routes(routes):
    """Compile ``(surfaces, handler)`` pairs into ``(predicate, dispatch)`` pairs."""
    compiled = []
    for surfaces, func in routes:
        compiled.append((_any_of(surfaces), make_dispatch(func)))
    return compiled


def _build_invoke_routes(invokes) -> dict[str, Callable]:
    """Compile ``{name: handler}`` into ``{name: invoke-dispatch}``."""
    return {name: make_invoke_dispatch(func) for name, func in (invokes or {}).items()}


def _register_activity(app: FastAPI, routes, invokes=None) -> None:
    """Serve the Activity Protocol on ``POST /activity/messages``.

    The one endpoint carries both halves of the protocol: fire-and-forget
    ``message`` turns (routed by surface predicate, reply posted out-of-band) and
    request/response ``invoke`` turns (routed by ``activity.name``, answered
    synchronously in the HTTP body).
    """
    compiled = _build_activity_routes(routes)
    compiled_invokes = _build_invoke_routes(invokes)

    @app.post("/activity/messages")
    async def messages(request: Request) -> Response:
        with _request_scope(request):
            try:
                payload = await request.json()
            except Exception:  # noqa: BLE001 - a malformed body is not our failure
                logger.error("Activity: unparseable request body.")
                return Response(status_code=200)

            activity = Activity.model_validate(payload)

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
                    logger.info("Invoke name=%r has no handler; acking 200.", activity.name)
                    return JSONResponse({}, status_code=200)
                with turn_scope(activity):
                    body = await dispatch(activity)
                return JSONResponse(body or {}, status_code=200)

            # One turn context spans dispatch *and* the connector send, so reply
            # decorations the handler/tools accumulate (AI label, citations, ...)
            # are still present when the answer message is posted.
            with turn_scope(activity):
                for predicate, dispatch in compiled:
                    if predicate(activity):
                        reply = await dispatch(activity)
                        if reply:
                            await send_reply(activity, reply)
                        return Response(status_code=200)

            logger.info(
                "Ignoring unsupported activity type=%r channel=%r",
                activity.type,
                activity.channel_id,
            )
            return Response(status_code=200)


def _register_wire(
    app: FastAPI, wire: dict, responses_store: OrderedDict[str, dict]
) -> None:
    """Serve each registered wire-protocol handler on its native endpoint."""
    responses_handler = wire.get("responses")
    if responses_handler is not None:
        responses_dispatch = make_return_dispatch(responses_handler)
        responses_stream_handler = wire.get("responses_stream")
        responses_stream_dispatch = (
            make_stream_dispatch(responses_stream_handler)
            if responses_stream_handler is not None
            else None
        )

        @app.post("/responses")
        async def responses(request: Request) -> Response:
            with _request_scope(request):
                body = await request.json()
                if not isinstance(body, dict):
                    return _api_error(
                        "request body must be a JSON object",
                        param="body",
                    )
                if body.get("background") is True:
                    return _api_error(
                        "background=true is not supported by this Castia runtime.",
                        code="unsupported_parameter",
                        param="background",
                    )
                text = _responses_input(body.get("input"))
                store = body.get("store") is not False
                if body.get("stream") is True:

                    async def events():
                        started = time.perf_counter()
                        first_chunk_at: float | None = None
                        last_chunk_at: float | None = None
                        chunk_count = 0
                        bytes_sent = 0

                        def emit(event: str) -> str:
                            nonlocal first_chunk_at, last_chunk_at, chunk_count, bytes_sent
                            now = time.perf_counter()
                            first_chunk_at = first_chunk_at or now
                            last_chunk_at = now
                            chunk_count += 1
                            bytes_sent += len(event.encode("utf-8"))
                            return event

                        response_id = f"resp_{uuid4().hex}"
                        output_item_id = f"msg_{uuid4().hex}"
                        input_items = _responses_input_items(
                            body.get("input"), response_id=response_id
                        )
                        chunks: list[str] = []
                        try:
                            with _request_scope(request), dev_diagnostics.turn(
                                "responses", text
                            ):
                                yield emit(
                                    _sse_event(
                                        "response.created",
                                        {
                                            "type": "response.created",
                                            "response": _streaming_response(
                                                "",
                                                response_id=response_id,
                                                status="in_progress",
                                            ),
                                        },
                                    )
                                )
                                yield emit(
                                    _sse_event(
                                        "response.in_progress",
                                        {
                                            "type": "response.in_progress",
                                            "response": _streaming_response(
                                                "",
                                                response_id=response_id,
                                                status="in_progress",
                                            ),
                                        },
                                    )
                                )
                                yield emit(
                                    _sse_event(
                                        "response.output_item.added",
                                        {
                                            "type": "response.output_item.added",
                                            "output_index": 0,
                                            "item": _streaming_item(
                                                output_item_id, "", status="in_progress"
                                            ),
                                        },
                                    )
                                )
                                yield emit(
                                    _sse_event(
                                        "response.content_part.added",
                                        {
                                            "type": "response.content_part.added",
                                            "item_id": output_item_id,
                                            "output_index": 0,
                                            "content_index": 0,
                                            "part": _streaming_content_part(""),
                                        },
                                    )
                                )
                                if responses_stream_dispatch is not None:
                                    async for delta in responses_stream_dispatch(text):
                                        chunks.append(delta)
                                        yield emit(
                                            _sse_event(
                                                "response.output_text.delta",
                                                {
                                                    "type": "response.output_text.delta",
                                                    "delta": delta,
                                                },
                                            )
                                        )
                                else:
                                    reply = await responses_dispatch(text)
                                    if reply:
                                        chunks.append(reply)
                                        yield emit(
                                            _sse_event(
                                                "response.output_text.delta",
                                                {
                                                    "type": "response.output_text.delta",
                                                    "delta": reply,
                                                },
                                            )
                                        )
                                output_text = "".join(chunks)
                                dev_diagnostics.record_output(output_text)
                                yield emit(
                                    _sse_event(
                                        "response.output_text.done",
                                        {
                                            "type": "response.output_text.done",
                                            "item_id": output_item_id,
                                            "output_index": 0,
                                            "content_index": 0,
                                            "text": output_text,
                                        },
                                    )
                                )
                                yield emit(
                                    _sse_event(
                                        "response.content_part.done",
                                        {
                                            "type": "response.content_part.done",
                                            "item_id": output_item_id,
                                            "output_index": 0,
                                            "content_index": 0,
                                            "part": _streaming_content_part(output_text),
                                        },
                                    )
                                )
                                yield emit(
                                    _sse_event(
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
                                )
                                if store:
                                    _store_completed_response(
                                        responses_store,
                                        response_id,
                                        {
                                            "response": _responses_body(
                                                output_text,
                                                response_id=response_id,
                                                output_item_id=output_item_id,
                                            ),
                                            "input_items": input_items,
                                        },
                                    )
                                yield emit(
                                    _sse_event(
                                        "response.completed",
                                        _responses_completed_event(
                                            output_text,
                                            response_id=response_id,
                                            output_item_id=output_item_id,
                                        ),
                                    ),
                                )
                                yield emit(_sse_done())
                        finally:
                            _record_stream_attributes(
                                started=started,
                                first_chunk_at=first_chunk_at,
                                last_chunk_at=last_chunk_at,
                                chunk_count=chunk_count,
                                bytes_sent=bytes_sent,
                            )

                    return StreamingResponse(events(), media_type="text/event-stream")

                with dev_diagnostics.turn("responses", text):
                    reply = await responses_dispatch(text)
                    dev_diagnostics.record_output(reply)
                response_id = f"resp_{uuid4().hex}"
                body_out = _responses_body(reply, response_id=response_id)
                if store:
                    _store_completed_response(
                        responses_store,
                        response_id,
                        {
                            "response": _responses_body(
                                reply,
                                response_id=response_id,
                                output_item_id=f"msg_{uuid4().hex}",
                            ),
                            "input_items": _responses_input_items(
                                body.get("input"), response_id=response_id
                            ),
                        },
                    )
                return JSONResponse(body_out)

        logger.info("Serving responses protocol on POST /responses")

        @app.get("/responses/{response_id}")
        async def get_response(response_id: str) -> Response:
            record = responses_store.get(response_id)
            if record is None:
                return _not_found_response(response_id)
            return JSONResponse(record["response"])

        @app.delete("/responses/{response_id}")
        async def delete_response(response_id: str) -> Response:
            if response_id not in responses_store:
                return _not_found_response(response_id)
            del responses_store[response_id]
            return JSONResponse(
                {"id": response_id, "object": "response", "deleted": True},
                status_code=200,
            )

        @app.post("/responses/{response_id}/cancel")
        async def cancel_response(response_id: str) -> Response:
            if response_id not in responses_store:
                return _not_found_response(response_id)
            return _api_error(
                "Cannot cancel a synchronous response.",
                code="unsupported_parameter",
                param="response_id",
            )

        @app.get("/responses/{response_id}/input_items")
        async def response_input_items(response_id: str, request: Request) -> Response:
            record = responses_store.get(response_id)
            if record is None:
                return _not_found_response(response_id)
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
                body = await request.json()
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


def _registered_protocols(routes, wire=None, invokes=None) -> list[str]:
    protocols: list[str] = []
    if routes or invokes:
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

    protocols = _registered_protocols(routes, wire, invokes)
    paths = {"readiness": "/readiness"}
    paths.update({protocol: _ROUTE_PATHS[protocol] for protocol in protocols})
    return payload | {
        "agent": {"name": agent_name},
        "protocols": protocols,
        "routes": paths,
        "invokes": sorted((invokes or {}).keys()),
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
    *,
    agent_name: str | None = None,
    required_env: tuple[str, ...] = (),
) -> FastAPI:
    """Assemble the FastAPI app: readiness, the Activity endpoint, wire routes."""
    app = FastAPI(title="castia", docs_url=None, redoc_url=None)

    @app.get("/readiness")
    async def readiness(request: Request) -> Response:
        body = _readiness_payload(
            routes,
            wire or {},
            invokes or {},
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

    _register_activity(app, routes, invokes)
    _register_wire(app, wire or {}, OrderedDict())
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
    *,
    host: str = "0.0.0.0",
    port: int = 8088,
    agent_name: str | None = None,
    required_env: tuple[str, ...] = (),
) -> None:
    """Serve the Activity endpoint and any registered wire protocols."""
    logger.info("Starting agent %s on %s:%s...", agent_name or "<unnamed>", host, port)
    _ensure_port_available(host, port)
    uvicorn.run(
        build_app(
            routes,
            wire,
            invokes,
            agent_name=agent_name,
            required_env=required_env,
        ),
        host=host,
        port=port,
        log_level="info",
    )

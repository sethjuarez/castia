"""The hidden wiring: build the FastAPI app and serve every protocol.

Everything a customer should never read lives here -- the route table, the
surface-to-predicate mapping, the Activity endpoint that parses an inbound turn
and posts its reply back through the connector, and the native wire endpoints.
``Agent.run`` imports this module only *after* telemetry is configured (FastAPI
and uvicorn are cheap, uninstrumented imports, so pulling them here is safe).

This replaces the old ``aiohttp`` + ``CloudAdapter`` + ``AgentApplication`` stack
with plain FastAPI. The Activity protocol is handled directly:

* parse the inbound Bot Framework Activity (:class:`castia.activity.Activity`),
* route it to the first handler whose Teams-surface predicate matches,
* run the handler and POST its reply out-of-band via
  :func:`castia.connector.send_reply` (the connector chooses the reply
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
from collections.abc import Callable
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .activity import Activity
from .activity_routing import (
    teams_direct_message,
    teams_group_chat_message,
    teams_tagged_channel_message,
)
from .connector import send_reply
from .context import turn_scope
from .dispatch import make_dispatch, make_invoke_dispatch, make_return_dispatch
from .surfaces import Teams

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


def _any_of(surfaces: tuple[Teams, ...]) -> Callable[[Activity], bool]:
    """Combine the selected surface predicates with OR."""
    predicates = [_PREDICATES[s] for s in surfaces]
    return lambda activity: any(predicate(activity) for predicate in predicates)


def _responses_body(text: str) -> dict:
    """An OpenAI Responses-shaped payload carrying ``text``.

    Minimal but real: ``output_text`` for simple readers and a structured
    ``output[]`` for clients that walk the content array. ``id`` lets a caller
    thread ``previous_response_id`` on a later turn.
    """
    return {
        "id": f"resp_{uuid4().hex}",
        "object": "response",
        "status": "completed",
        "output_text": text,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


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


def _register_wire(app: FastAPI, wire: dict) -> None:
    """Serve each registered wire-protocol handler on its native endpoint."""
    responses_handler = wire.get("responses")
    if responses_handler is not None:
        responses_dispatch = make_return_dispatch(responses_handler)

        @app.post("/responses")
        async def responses(request: Request) -> Response:
            body = await request.json()
            reply = await responses_dispatch(_responses_input(body.get("input")))
            return JSONResponse(_responses_body(reply))

        logger.info("Serving responses protocol on POST /responses")

    chat_handler = wire.get("chat")
    if chat_handler is not None:
        chat_dispatch = make_return_dispatch(chat_handler)

        @app.post("/chat/completions")
        async def chat(request: Request) -> Response:
            body = await request.json()
            reply = await chat_dispatch(_last_user_text(body.get("messages")))
            return JSONResponse(_chat_body(reply))

        logger.info("Serving chat protocol on POST /chat/completions")

    invocations_handler = wire.get("invocations")
    if invocations_handler is not None:
        invocations_dispatch = make_return_dispatch(invocations_handler)

        @app.post("/invocations")
        async def invocations(request: Request) -> Response:
            # Pass-through: accept a JSON object ({"message"|"input"}) or a bare
            # text body, matching Foundry's Invocations sample.
            raw = await request.body()
            try:
                body = json.loads(raw) if raw else ""
            except json.JSONDecodeError:
                body = raw.decode("utf-8", errors="replace").strip()
            reply = await invocations_dispatch(_invocations_input(body))
            return JSONResponse(_invocations_body(reply))

        logger.info("Serving invocations protocol on POST /invocations")


def build_app(routes, wire=None, invokes=None) -> FastAPI:
    """Assemble the FastAPI app: readiness, the Activity endpoint, wire routes."""
    app = FastAPI(title="castia", docs_url=None, redoc_url=None)

    @app.get("/readiness")
    async def readiness() -> Response:
        return PlainTextResponse("Agent running!")

    _register_activity(app, routes, invokes)
    _register_wire(app, wire or {})
    return app


def serve(
    routes, wire=None, invokes=None, *, host: str = "0.0.0.0", port: int = 8088
) -> None:
    """Serve the Activity endpoint and any registered wire protocols."""
    logger.info("Starting agent...")
    uvicorn.run(
        build_app(routes, wire, invokes), host=host, port=port, log_level="info"
    )

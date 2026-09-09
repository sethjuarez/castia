"""Turn a plain handler into a dispatch callback -- the FastAPI trick.

At registration we inspect the handler's signature *once* and decide, per
parameter, where its value comes from (this mirrors FastAPI building a
"dependant"). At call time we inject those values and return the handler's string
result to the caller, which is responsible for delivering it (the activity server
posts it back through the connector; a wire route puts it in the HTTP body). The
handler itself stays an ordinary function -- the decorator returns it unchanged.

There is no SDK ``TurnContext`` anymore: an activity handler receives the parsed
:class:`castia.activity.Activity` (directly, or wrapped as a
:class:`castia.messages.Message`) and the message text. Imported by ``server``
only after telemetry is configured.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from .activity import Activity
from .dependencies import _Depends, resolve
from .messages import Message
from .observability import flush_telemetry
from .tracing import invoke_agent

Handler = Callable[..., Awaitable[Any]]
ActivityDispatch = Callable[[Activity], Awaitable[str | None]]
InvokeDispatch = Callable[[Activity], Awaitable[dict | None]]


def make_dispatch(func: Handler) -> ActivityDispatch:
    """Adapt a user handler to the activity dispatch shape.

    Returns an async callable taking the parsed inbound :class:`Activity` and
    yielding the handler's reply string (or ``None`` to stay silent). The server
    posts that reply back through the Bot Framework connector.
    """
    parameters = list(inspect.signature(func).parameters.values())
    wants_text = any(_source_is_text(p) for p in parameters)

    async def dispatch(activity: Activity) -> str | None:
        text = activity.text or ""
        # Parity with the original: a text handler stays quiet on empty input.
        if wants_text and not text:
            return None

        try:
            with invoke_agent():
                kwargs: dict[str, Any] = {}
                for param in parameters:
                    if isinstance(param.default, _Depends):
                        kwargs[param.name] = await resolve(
                            param.default.dependency, activity
                        )
                    elif param.annotation is Activity:
                        kwargs[param.name] = activity
                    elif param.annotation is Message:
                        kwargs[param.name] = Message(activity)
                    else:
                        kwargs[param.name] = text

                result = await func(**kwargs)
                return result if isinstance(result, str) and result else None
        finally:
            # Frozen hosted containers may never flush the batch processors on
            # their own, so push this turn's spans/logs out before returning.
            flush_telemetry()

    return dispatch


def make_invoke_dispatch(func: Handler) -> InvokeDispatch:
    """Adapt a user handler to the **invoke** (request/response) dispatch shape.

    Invoke turns are synchronous: the handler's return value *is* the answer, so
    it becomes the ``InvokeResponse`` body the server returns in the HTTP
    response -- unlike a message handler, whose reply is posted out-of-band and
    whose return value is only "text or stay silent". Same dependency injection
    as :func:`make_dispatch` (``Activity`` / ``Message`` / ``Depends(...)``), but
    a bare, unannotated parameter receives the invoke ``value`` payload rather
    than the message text, since that is what an invoke handler acts on.

    Return an invoke-response body (build one with
    :func:`castia.invokes.card_invoke_response` /
    :func:`castia.invokes.message_invoke_response`) or ``None`` for an empty
    ``200`` ack (e.g. acknowledging a feedback submission).
    """
    parameters = list(inspect.signature(func).parameters.values())

    async def dispatch(activity: Activity) -> dict | None:
        try:
            with invoke_agent():
                kwargs: dict[str, Any] = {}
                for param in parameters:
                    if isinstance(param.default, _Depends):
                        kwargs[param.name] = await resolve(
                            param.default.dependency, activity
                        )
                    elif param.annotation is Activity:
                        kwargs[param.name] = activity
                    elif param.annotation is Message:
                        kwargs[param.name] = Message(activity)
                    else:
                        kwargs[param.name] = activity.value

                result = await func(**kwargs)
                return result if isinstance(result, dict) else None
        finally:
            flush_telemetry()

    return dispatch


def make_return_dispatch(func: Handler) -> Callable[[str], Awaitable[str]]:
    """Adapt a user handler for a *return-body* wire protocol (responses/chat).

    Same dependency injection as :func:`make_dispatch`, but there is no inbound
    Activity -- the handler runs from a plain input string and its string return
    value becomes the HTTP response body. A handler that asks for an ``Activity``
    or ``Message`` is Activity-only and rejected here, because we deliberately do
    not synthesize an Activity for wire protocols.
    """
    parameters = list(inspect.signature(func).parameters.values())

    for param in parameters:
        if param.annotation in (Activity, Message):
            raise TypeError(
                f"handler {func.__name__!r} asks for {param.annotation.__name__}, "
                "which only exists on the Activity Protocol; a handler served over "
                "@app.responses(), @app.chat(), or @app.invocations() must take "
                "the input text and Depends(...) only."
            )

    async def dispatch(text: str) -> str:
        try:
            with invoke_agent():
                kwargs: dict[str, Any] = {}
                for param in parameters:
                    if isinstance(param.default, _Depends):
                        kwargs[param.name] = await resolve(
                            param.default.dependency, None
                        )
                    else:
                        kwargs[param.name] = text

                result = await func(**kwargs)
                return result if isinstance(result, str) else ""
        finally:
            flush_telemetry()

    return dispatch


def _source_is_text(param: inspect.Parameter) -> bool:
    """A param is fed the message text unless it asks for the activity/DI/Message."""
    if isinstance(param.default, _Depends):
        return False
    return param.annotation not in (Activity, Message)

"""Prior conversation items for the current Responses turn.

A Responses handler receives only the latest user text. When the request
carries earlier turns -- inline in ``input``, through ``previous_response_id``,
or through ``conversation`` with a consumer history store -- Castia binds them
for the turn, and the handler reads them with :func:`response_history`::

    @app.responses()
    async def answer(
        text: str,
        model: Model = Depends(model_provider),
        history: list[dict] = Depends(response_history),
    ) -> str:
        return await model.respond(text, history=history)

Items are model-ready Responses input items, oldest first: ``message`` items
(``role`` plus ``input_text``/``output_text`` parts), and paired
``function_call``/``function_call_output`` items. Ids, statuses, and item types
a model cannot replay are dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

from castia.runtime.dependencies import no_cache

__all__ = ["response_history"]

_HISTORY: ContextVar[tuple[dict, ...]] = ContextVar(
    "castia_response_history", default=()
)
_INPUT_ROLES = frozenset({"user", "system", "developer"})
_MESSAGE_ROLES = _INPUT_ROLES | {"assistant"}
_TEXT_PARTS = frozenset({"input_text", "output_text", "text"})


@no_cache
def response_history() -> list[dict]:
    """Return a copy of the prior conversation items for this Responses turn.

    Empty outside a Responses turn and on the first turn of a conversation.
    """
    return deepcopy(list(_HISTORY.get()))


@contextmanager
def bind_response_history(items: Iterable[dict]) -> Iterator[None]:
    token = _HISTORY.set(tuple(deepcopy(list(items))))
    try:
        yield
    finally:
        _HISTORY.reset(token)


def model_input_items(items: Iterable[object]) -> list[dict]:
    """Normalize stored or client items into replayable Responses input items."""
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, str):
            if item:
                normalized.append(_message("user", item))
            continue
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "message")
        if kind == "message":
            message = _message_item(item)
            if message is not None:
                normalized.append(message)
        elif kind == "function_call":
            if all(isinstance(item.get(key), str) for key in ("call_id", "name")):
                normalized.append(
                    {
                        "type": "function_call",
                        "call_id": item["call_id"],
                        "name": item["name"],
                        "arguments": item.get("arguments")
                        if isinstance(item.get("arguments"), str)
                        else "{}",
                    }
                )
        elif kind == "function_call_output":
            if isinstance(item.get("call_id"), str):
                output = item.get("output")
                normalized.append(
                    {
                        "type": "function_call_output",
                        "call_id": item["call_id"],
                        "output": output if isinstance(output, str) else "",
                    }
                )
    return _paired_tool_calls(normalized)


def _message(role: str, text: str) -> dict:
    part = "input_text" if role in _INPUT_ROLES else "output_text"
    return {"role": role, "content": [{"type": part, "text": text}]}


def _message_item(item: dict) -> dict | None:
    role = item.get("role", "user")
    if role not in _MESSAGE_ROLES:
        return None
    content = item.get("content")
    if isinstance(content, str):
        return _message(role, content) if content else None
    if not isinstance(content, list):
        return None
    part_type = "input_text" if role in _INPUT_ROLES else "output_text"
    parts = [
        {"type": part_type, "text": part["text"]}
        for part in content
        if isinstance(part, dict)
        and part.get("type") in _TEXT_PARTS
        and isinstance(part.get("text"), str)
        and part["text"]
    ]
    return {"role": role, "content": parts} if parts else None


def _paired_tool_calls(items: list[dict]) -> list[dict]:
    """Keep each tool output only after its call; drop orphans of either kind.

    Models reject an output without a preceding call, and a call without an
    output. A repeated call id replaces the still-unanswered earlier call.
    """
    keep = [True] * len(items)
    pending: dict[str, int] = {}
    for index, item in enumerate(items):
        kind = item.get("type")
        if kind == "function_call":
            previous = pending.get(item["call_id"])
            if previous is not None:
                keep[previous] = False
            pending[item["call_id"]] = index
        elif kind == "function_call_output":
            if pending.pop(item["call_id"], None) is None:
                keep[index] = False
    for index in pending.values():
        keep[index] = False
    return [item for item, kept in zip(items, keep) if kept]

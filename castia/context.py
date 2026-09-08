"""The **turn context** -- an ambient handle on the in-flight Activity turn.

Two things a rich activity turn needs but the plain ``activity``-threading model
cannot give cleanly:

* a way for code running *deep inside* a turn (a tool the model called, a helper
  three frames down) to reach the current turn without every function passing
  ``activity`` along, and
* a place to *accumulate reply decorations* -- an AI label, citations, a
  sensitivity banner, feedback buttons, importance -- as the turn runs, so the
  framework can fold them onto whatever message finally answers the user, whether
  that message comes from ``return "text"`` (the connector's :func:`send_reply`)
  or from an explicit :meth:`castia.messages.Message.say`.

This is the FastAPI ``Request`` pattern done with a :class:`contextvars.ContextVar`:
the server opens a :func:`turn_scope` around the whole inbound handling (dispatch
*and* the connector send), so anything inside -- handler, model tool loop, tool
impl -- can call :func:`current_turn` to read identity or record a decoration.
The contextvar is task-local, so concurrent turns never see each other's context.

Kept dependency-light (the entity builders in :mod:`castia.entities` are pure;
the ``Activity`` import is type-checking only) so importing it stays cheap.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import entities

if TYPE_CHECKING:
    from .activity import Activity

#: Message importance values Teams understands.
IMPORTANCE = ("normal", "high", "urgent")


@dataclass
class Turn:
    """The in-flight Activity turn plus the reply decorations it accumulates.

    Held in a contextvar for the duration of one inbound turn. ``activity`` is the
    parsed inbound turn (identity + conversation). The remaining fields are the
    decorations folded onto the answer message when it is sent; a handler sets
    them directly, via :meth:`castia.messages.Message.say` keywords, or -- for
    citations -- via the model calling the ``cite_source`` tool.
    """

    activity: Activity
    ai_generated: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    sensitivity: dict[str, Any] | None = None
    feedback: str | None = None
    importance: str | None = None

    def cite(
        self,
        name: str,
        *,
        url: str = "",
        abstract: str = "",
        keywords: list[str] | None = None,
        icon: str = "",
    ) -> int:
        """Record a source citation; returns its 1-based ``[n]`` position.

        The position auto-increments in citation order, so a caller (or the
        ``cite_source`` tool) can weave the returned number into the answer text
        as ``[n]``. Folded onto the reply as a ``Claim`` when the message is sent.
        """
        position = len(self.citations) + 1
        self.citations.append(
            entities.citation(
                position,
                name,
                url=url,
                abstract=abstract,
                keywords=keywords,
                icon=icon,
            )
        )
        return position


# The one ambient turn. ``None`` outside an Activity turn (wire protocols, tests,
# startup), which callers must tolerate -- decoration is best-effort.
_current: ContextVar[Turn | None] = ContextVar("castia_turn", default=None)


def current_turn() -> Turn:
    """The turn in flight, or raise if called outside an Activity turn.

    Use inside handlers and tool impls that require a turn (to record a citation,
    read identity). For code that must also run off-turn, use
    :func:`current_turn_or_none`.
    """
    turn = _current.get()
    if turn is None:
        raise RuntimeError(
            "current_turn() called outside an Activity turn; a turn context is "
            "only established while handling an inbound activity."
        )
    return turn


def current_turn_or_none() -> Turn | None:
    """The turn in flight, or ``None`` outside one. Never raises."""
    return _current.get()


@contextlib.contextmanager
def turn_scope(activity: Activity) -> Iterator[Turn]:
    """Establish the ambient :class:`Turn` for the body's duration.

    Opened by the server around the whole inbound turn -- dispatch *and* the
    connector send -- so decorations accumulated by the handler/tools are still
    present when the answer message is posted. Resets on exit, so nothing leaks
    to the next turn on a reused worker.
    """
    turn = Turn(activity=activity)
    token = _current.set(turn)
    try:
        yield turn
    finally:
        _current.reset(token)


def decorate_message(
    payload: dict[str, Any],
    turn: Turn | None,
    *,
    ai_generated: bool = False,
    citations: list[dict[str, Any]] | None = None,
    sensitivity: dict[str, Any] | None = None,
    feedback: str | None = None,
    importance: str | None = None,
) -> dict[str, Any]:
    """Fold reply decorations onto an outgoing **message** ``payload`` in place.

    Combines what the turn accumulated with any per-call overrides (the keyword
    arguments, e.g. from :meth:`Message.say`), then stamps them where Teams reads
    them: the single root message entity (AI label + citations + sensitivity),
    the ``feedbackLoop`` ``channelData``, and the top-level ``importance``. Safe
    to call with ``turn=None`` (uses only the overrides) and only mutates the
    keys it sets, so existing ``text``/``attachments``/``entities`` survive.
    """
    label = ai_generated or (turn.ai_generated if turn else False)

    merged_citations: list[dict[str, Any]] = []
    if turn and turn.citations:
        merged_citations.extend(turn.citations)
    if citations:
        merged_citations.extend(citations)

    banner = sensitivity if sensitivity is not None else (turn.sensitivity if turn else None)
    loop = feedback if feedback is not None else (turn.feedback if turn else None)
    weight = importance if importance is not None else (turn.importance if turn else None)

    root = entities.message_entity(
        ai_generated=label,
        citations=merged_citations or None,
        sensitivity=banner,
    )
    if root is not None:
        payload.setdefault("entities", []).append(root)

    if loop:
        payload.setdefault("channelData", {}).update(
            entities.feedback_channel_data(loop)
        )

    if weight:
        payload["importance"] = weight

    return payload

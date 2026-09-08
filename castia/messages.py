"""The ``Message`` a handler can ask for by type.

A thin, framework-owned view over the raw Activity so a customer never touches
the SDK's ``TurnContext``/``Activity`` types unless they want to. Kept dependency
free (the Activity import is type-checking only) so importing it stays cheap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .identity import agentic_user_id, require_agentic_user

if TYPE_CHECKING:
    from .activity import Activity
    from .streaming import Streamer


class Message:
    """A received message, injected when a handler annotates a param ``Message``."""

    def __init__(self, activity: Activity) -> None:
        self._activity = activity

    @property
    def text(self) -> str:
        return self._activity.text or ""

    @property
    def activity(self) -> Activity:
        return self._activity

    @property
    def id(self) -> str | None:
        """The inbound activity's id (the message to react to / reply under)."""
        return self._activity.id

    @property
    def agentic_user_id(self) -> str | None:
        """The agent's own mailbox identity for this turn, or ``None``.

        Populated only when the turn runs as the agent's Agentic-User (see
        :func:`castia.identity.agentic_user_id`).
        """
        return agentic_user_id(self._activity)

    def require_agentic_user(self) -> str:
        """Return the agent's agentic user id, or raise ``AgenticIdentityError``.

        Fail-fast gate for acting as the agent itself (e.g. sending mail). See
        :func:`castia.identity.require_agentic_user`.
        """
        return require_agentic_user(self._activity)

    # -- Outbound conversation actions --------------------------------------
    #
    # Each reuses the turn's identity via the connector; all are best-effort.
    # The connector is imported lazily so ``import castia`` stays cheap.

    async def say(
        self,
        text: str = "",
        *,
        attachments: list[dict[str, Any]] | None = None,
        suggestions: dict[str, Any] | None = None,
        entities: list[dict[str, Any]] | None = None,
        ai_generated: bool = False,
        citations: list[dict[str, Any]] | None = None,
        sensitivity: dict[str, Any] | None = None,
        feedback: str | None = None,
        importance: str | None = None,
    ) -> str | None:
        """Send a reply to the conversation, optionally with a card / actions.

        Pass ``attachments`` from :func:`castia.cards.adaptive_card` and
        ``suggestions`` from :func:`castia.cards.suggested_actions`. The reply
        *decoration* keywords stamp message-level metadata Teams renders around
        the answer (see :mod:`castia.entities`):

        * ``ai_generated`` -- show the "AI generated" label.
        * ``citations`` -- a list from :func:`castia.entities.citation` (or
          accumulated via the ``cite_source`` tool / :meth:`Turn.cite`); referenced
          as ``[n]`` markers in ``text``.
        * ``sensitivity`` -- a banner from
          :func:`castia.entities.sensitivity_label`.
        * ``feedback`` -- ``"default"`` or ``"custom"`` to add thumbs up/down.
        * ``importance`` -- ``"high"`` / ``"urgent"`` to flag the message.
        * ``entities`` -- raw extra entities (e.g. an
          :func:`castia.entities.mention_entity`) with a matching ``<at>``
          tag in ``text``.

        Any decoration accumulated on the turn (e.g. citations from a tool) is
        merged in too. Returns the sent activity's id (usable with
        :meth:`update` / :meth:`delete`).
        """
        from .connector import post_activity
        from .context import current_turn_or_none, decorate_message

        payload: dict[str, Any] = {"type": "message"}
        if text:
            payload["text"] = text
        if attachments:
            payload["attachments"] = attachments
        if suggestions:
            payload["suggestedActions"] = suggestions
        if entities:
            payload["entities"] = list(entities)

        decorate_message(
            payload,
            current_turn_or_none(),
            ai_generated=ai_generated,
            citations=citations,
            sensitivity=sensitivity,
            feedback=feedback,
            importance=importance,
        )
        return await post_activity(self._activity, payload)

    async def typing(self) -> None:
        """Show the "...is typing" indicator in the conversation."""
        from .connector import send_typing

        await send_typing(self._activity)

    async def react(self, reaction: str = "like") -> bool:
        """Add an emoji reaction to this message (see :class:`castia.Reaction`)."""
        from .connector import add_reaction

        return await add_reaction(self._activity, reaction)

    async def unreact(self, reaction: str = "like") -> bool:
        """Remove an emoji reaction previously added to this message."""
        from .connector import remove_reaction

        return await remove_reaction(self._activity, reaction)

    async def update(self, activity_id: str, text: str, **payload: Any) -> bool:
        """Edit a message we previously sent (by its id)."""
        from .connector import update_activity

        body = {"type": "message", "text": text, **payload}
        return await update_activity(self._activity, activity_id, body)

    async def delete(self, activity_id: str) -> bool:
        """Delete (retract) a message we previously sent (by its id)."""
        from .connector import delete_activity

        return await delete_activity(self._activity, activity_id)

    def stream(self, *, min_interval: float = 0.75) -> Streamer:
        """Open a live-typing :class:`~castia.streaming.Streamer` for this turn.

        The handler drives it and returns ``None`` so the server does not also
        send a reply::

            s = msg.stream()
            async for delta in model.stream(text):
                await s.append(delta)
            await s.finish()
        """
        from .streaming import Streamer

        return Streamer(self._activity, min_interval=min_interval)

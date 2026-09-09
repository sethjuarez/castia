"""Live-typing **streaming** of a reply, the way Teams renders it.

Teams shows a message being written token-by-token when the agent sends a run of
``typing`` activities that carry stream metadata, then a final ``message`` that
replaces them. This module is that lifecycle, matching the Teams SDK's own
``http-stream`` implementation:

* Each interim chunk is a ``typing`` activity POSTed via the connector's
  ``createActivity``, carrying both a ``channelData`` block and a ``streaminfo``
  entity with ``streamId`` / ``streamType`` / ``streamSequence`` (1-based).
* The **first** chunk is sent with no ``streamId``; the id the connector returns
  becomes the ``streamId`` reused by every later chunk and the final message.
* Status-only updates ("Thinking...") use ``streamType: "informative"`` and are
  only meaningful before any text has accumulated.
* Text chunks use ``streamType: "streaming"``.
* :meth:`finish` sends a real ``message`` (``streamType: "final"``) correlated
  by ``streamId``, with ``streamSequence`` omitted (the Teams contract requires
  the final message to carry no sequence); it may also carry final attachments /
  suggested actions.

Two efficiency notes drive the design:

* ``connector.authorization`` is minted **once** at construction time and reused
  across every chunk, so an agentic turn does not re-run the ``user_fic`` token
  chain per token.
* Chunks are throttled to ``min_interval`` seconds (the SDK uses ~0.5-1s) so a
  fast model does not flood the connector; tests pass ``min_interval=0`` to flush
  every append.

Best-effort like the rest of the connector: a failed chunk is swallowed so a
transient error mid-stream never crashes the turn.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .activity import Activity

_INFORMATIVE = "informative"
_STREAMING = "streaming"
_FINAL = "final"


class Streamer:
    """Drives one streamed reply for a single turn.

    Create via :meth:`castia.messages.Message.stream`, then::

        s = msg.stream()
        await s.update("Thinking...")
        async for delta in model.stream(prompt):
            await s.append(delta)
        await s.finish()
    """

    def __init__(self, activity: Activity, *, min_interval: float = 0.75) -> None:
        self._activity = activity
        self._min_interval = min_interval
        self._stream_id: str | None = None
        self._sequence = 0
        self._text = ""
        self._headers: dict[str, str] | None = None
        self._last_flush = 0.0
        self._finished = False

    @property
    def text(self) -> str:
        """The text accumulated from :meth:`append` so far."""
        return self._text

    async def update(self, status: str) -> None:
        """Send an informative status ("Thinking...", "Searching your inbox...").

        A no-op once text has started accumulating -- Teams only shows informative
        status while the message body is still empty.
        """
        if self._finished or self._text:
            return
        await self._chunk(_INFORMATIVE, status, force=True)

    async def append(self, delta: str) -> None:
        """Append a token/delta to the message and flush if the throttle allows."""
        if self._finished or not delta:
            return
        self._text += delta
        now = time.monotonic()
        if (now - self._last_flush) >= self._min_interval:
            await self._chunk(_STREAMING, self._text, force=True)

    async def finish(
        self,
        *,
        text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        suggestions: dict[str, Any] | None = None,
    ) -> str | None:
        """Send the final message that replaces the streamed placeholder.

        ``text`` overrides the accumulated text (else the appended text is used).
        ``attachments`` / ``suggestions`` attach an Adaptive Card / suggested
        actions to the final message. Returns the final activity id.
        """
        if self._finished:
            return self._stream_id
        self._finished = True
        final_text = self._text if text is None else text
        return await self._chunk(
            _FINAL,
            final_text,
            force=True,
            final=True,
            attachments=attachments,
            suggestions=suggestions,
        )

    # -- internals ----------------------------------------------------------

    async def _ensure_headers(self) -> dict[str, str]:
        if self._headers is None:
            from .connector import authorization

            self._headers = await authorization(self._activity)
        return self._headers

    async def _chunk(
        self,
        stream_type: str,
        text: str,
        *,
        force: bool = False,
        final: bool = False,
        attachments: list[dict[str, Any]] | None = None,
        suggestions: dict[str, Any] | None = None,
    ) -> str | None:
        from .connector import post_activity

        headers = await self._ensure_headers()

        # The streamInfo entity carries the correlation id + phase. Per the Teams
        # streaming contract, streamSequence is a 1-based counter on the interim
        # (typing) chunks and MUST be omitted from the final message -- including
        # it leaves the bubble stuck rendering as "streaming" and it never
        # resolves. Correlation on the final message is via streamId, not an
        # activity-level id.
        info: dict[str, Any] = {"streamType": stream_type}
        if self._stream_id:
            info["streamId"] = self._stream_id
        if not final:
            self._sequence += 1
            info["streamSequence"] = self._sequence

        payload: dict[str, Any] = {
            "type": "message" if final else "typing",
            "channelData": dict(info),
            "entities": [{"type": "streamInfo", **info}],
        }
        if text:
            payload["text"] = text
        if final:
            if attachments:
                payload["attachments"] = attachments
            if suggestions:
                payload["suggestedActions"] = suggestions

        created = await post_activity(self._activity, payload, headers=headers)
        if self._stream_id is None and created:
            self._stream_id = created
        self._last_flush = time.monotonic()
        return self._stream_id or created

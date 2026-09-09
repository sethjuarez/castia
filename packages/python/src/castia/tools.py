"""Outbound capabilities modelled as **tools** the model can call.

The framework's split: *inbound* is a decorator (the wire ingress -- a turn
arrives), *outbound* is a tool (the model decides to act and emits a function
call, which we route to a plain async impl). **No MCP** -- these are direct Graph
REST calls behind a typed function signature.

A :class:`Tool` couples the model-facing *spec* (name, description, JSON-schema
parameters) with the async *impl* that runs it. Every impl is called with the
current turn's ``activity`` injected as the first argument -- that carries the
agent's Agentic-User identity, which the impl needs to mint a delegated Graph
token. Impls return a diagnostic ``dict`` (see ``mail.py`` / ``drive.py``); that
dict is fed straight back to the model as the tool result, so the model can
compose a natural reply and react to failures.

``scopes`` records the delegated Microsoft Graph permissions each tool needs.
It is advisory today (surfaced for auditing / a future consent preflight), not
enforced here -- consent is granted on the agent identity out of band.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .activity import Activity

# An impl is ``async def f(activity, **kwargs) -> dict``.
ToolImpl = Callable[..., Awaitable[dict]]


async def _react_to_message_impl(
    activity: Activity, *, reaction: str = "like", remove: bool = False
) -> dict:
    """Add (or remove) an emoji reaction on the message that triggered the turn."""
    from .connector import add_reaction, remove_reaction

    verb = remove_reaction if remove else add_reaction
    ok = await verb(activity, reaction)
    return {"ok": ok, "reaction": reaction, "removed": remove}


async def _cite_source_impl(
    activity: Activity,
    *,
    name: str,
    url: str = "",
    snippet: str = "",
    icon: str = "",
) -> dict:
    """Record a source citation on the turn; the framework folds it onto the reply.

    Does not send anything itself -- it accumulates a citation on the turn
    context, which :func:`castia.context.decorate_message` stamps onto the
    final answer message. Returns the ``[n]`` marker the model should weave into
    its answer text so the in-text reference lines up with the reference card.
    """
    from .context import current_turn_or_none

    turn = current_turn_or_none()
    if turn is None:
        return {"ok": False, "error": "no active turn to attach a citation to"}
    position = turn.cite(name, url=url, abstract=snippet, icon=icon)
    return {"ok": True, "position": position, "marker": f"[{position}]"}


@dataclass(frozen=True)
class Tool:
    """A model-callable outbound capability."""

    name: str
    description: str
    parameters: dict[str, Any]
    impl: ToolImpl
    scopes: tuple[str, ...] = field(default_factory=tuple)

    def spec(self) -> dict[str, Any]:
        """The OpenAI Responses-API function-tool spec for this tool."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def run(self, activity: Activity, **kwargs: Any) -> dict:
        """Invoke the impl with the turn's identity-bearing activity injected."""
        return await self.impl(activity, **kwargs)


def agent_tools() -> list[Tool]:
    """The full outbound tool suite for a turn: activity + Graph capabilities.

    Composed from the two families so a caller can offer just one (a Teams-only
    agent wants :func:`activity_tools`; a mailbox routine may want just
    :func:`graph_tools`). Kept as the default all-in suite for the demo handler.
    """
    return [*activity_tools(), *graph_tools()]


def activity_tools() -> list[Tool]:
    """Tools that act on the **conversation** -- reactions and citations.

    These are pure Bot Framework connector / reply-decoration actions: no
    Microsoft Graph, no delegated token, so they work on any activity turn.
    """
    return [
        Tool(
            name="react_to_message",
            description=(
                "Add (or remove) an emoji reaction on the message you are "
                "responding to. Use to acknowledge lightweight requests without a "
                "full reply -- a thumbs-up/like to confirm you'll do something, "
                "'eyes' while you look into it, or 'check' when done."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reaction": {
                        "type": "string",
                        "description": (
                            "Reaction id, e.g. 'like', 'heart', 'laugh', "
                            "'1f440_eyes' (looking), '2705_whiteheavycheckmark' "
                            "(done)."
                        ),
                    },
                    "remove": {
                        "type": "boolean",
                        "description": "Remove the reaction instead of adding it.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            impl=_react_to_message_impl,
        ),
        Tool(
            name="cite_source",
            description=(
                "Attribute a claim in your answer to a source. Call once per "
                "source BEFORE writing the sentence it supports, then put the "
                "returned [n] marker at the end of that sentence. The source "
                "appears as a numbered reference under your message; do not "
                "invent sources."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Source title shown in the reference (<=80 chars).",
                    },
                    "url": {
                        "type": "string",
                        "description": "Optional link to the source.",
                    },
                    "snippet": {
                        "type": "string",
                        "description": "Optional hover abstract (<=160 chars).",
                    },
                    "icon": {
                        "type": "string",
                        "description": (
                            "Optional document-type glyph, e.g. 'PDF', 'Word', "
                            "'Excel'."
                        ),
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            impl=_cite_source_impl,
        ),
    ]


def graph_tools() -> list[Tool]:
    """Tools that act as the agent's **agentic user** over Microsoft Graph.

    Imports the Graph impls lazily (like the handler does) so the instrumented
    auth SDK loads only when tools are actually offered, never at import time.
    Each impl mints a delegated Graph token from the turn's agentic identity.
    """
    from .drive import create_doc_as_agent
    from .mail import reply_as_agent, send_as_agent
    from .mailbox import read_inbox_as_agent

    return [
        Tool(
            name="send_email",
            description=(
                "Send a NEW email as the agent's own mailbox identity. Use when "
                "composing a fresh message to someone. To respond to a message the "
                "agent received, use reply_email instead so it threads correctly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {"type": "string", "description": "Email subject line."},
                    "body": {"type": "string", "description": "Plain-text email body."},
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
            impl=send_as_agent,
            scopes=("Mail.Send",),
        ),
        Tool(
            name="reply_email",
            description=(
                "Reply in-thread to an email the agent received. Use this (not "
                "send_email) to respond to an inbox message, so the reply stays in "
                "the original conversation thread. Pass the message's id from "
                "read_inbox."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": (
                            "The id of the received message to reply to, as "
                            "returned by read_inbox."
                        ),
                    },
                    "body": {"type": "string", "description": "Plain-text reply body."},
                },
                "required": ["message_id", "body"],
                "additionalProperties": False,
            },
            impl=reply_as_agent,
            scopes=("Mail.Send",),
        ),
        Tool(
            name="create_document",
            description=(
                "Create or replace a text/markdown document on the agent's own "
                "OneDrive. Use when the user asks to save, write, or leave a note "
                "or document."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Filename or relative path under the agent's OneDrive, "
                            "e.g. 'summary.md' or 'notes/2026/summary.md'."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The document's text content (markdown).",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            impl=create_doc_as_agent,
            scopes=("Files.ReadWrite",),
        ),
        Tool(
            name="read_inbox",
            description=(
                "Read the newest messages in the agent's own mailbox. Use when the "
                "user asks what email the agent has received, to check its inbox, or "
                "to look for a specific message. Each message includes an id you can "
                "pass to reply_email to respond in-thread."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "top": {
                        "type": "integer",
                        "description": "How many recent messages to return (1-25).",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only return unread messages when true.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            impl=read_inbox_as_agent,
            scopes=("Mail.Read",),
        ),
    ]

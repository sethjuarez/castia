"""Activity selectors for the Microsoft Teams surfaces supported by this agent.

Each selector is a plain predicate over the hand-rolled :class:`castia.activity.Activity`
(no SDK ``TurnContext`` wrapper anymore -- the server calls these directly on the
parsed inbound activity). A surface is a message on the ``msteams`` channel of a
particular conversation type; the channel mention surface additionally requires
the agent to be @-mentioned.
"""

from __future__ import annotations

from collections.abc import Callable

from .activity import Activity

ActivityMatcher = Callable[[Activity], bool]


def teams_direct_message(activity: Activity) -> bool:
    return (
        _is_message_on(activity, "msteams")
        and activity.conversation is not None
        and activity.conversation.conversation_type == "personal"
    )


def teams_group_chat_message(activity: Activity) -> bool:
    return (
        _is_message_on(activity, "msteams")
        and activity.conversation is not None
        and activity.conversation.conversation_type == "groupChat"
    )


def teams_tagged_channel_message(activity: Activity) -> bool:
    return (
        _is_message_on(activity, "msteams")
        and activity.conversation is not None
        and activity.conversation.conversation_type == "channel"
        and _agent_is_mentioned(activity)
    )


def _is_message_on(activity: Activity, channel: str) -> bool:
    return activity.type == "message" and activity.channel == channel


def _agent_is_mentioned(activity: Activity) -> bool:
    if activity.recipient is None:
        return False

    return any(
        mention.mentioned is not None and mention.mentioned.id == activity.recipient.id
        for mention in activity.get_mentions()
    )

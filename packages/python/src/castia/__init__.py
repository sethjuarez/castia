"""castia -- a tiny FastAPI-shaped framework for Foundry digital workers.

Public surface:

    from castia import Agent, Teams, Message, Depends

Everything else in this package is framework plumbing a customer never reads.
Importing this package is intentionally cheap (no instrumented SDK libraries) so
telemetry can be configured before they load.
"""

from __future__ import annotations

from .application import PUBLISHABLE_PROTOCOLS, Agent, Router
from .cards import (
    Reaction,
    action_chips,
    adaptive_card,
    decision_card,
    suggested_actions,
)
from .context import Turn, current_turn, current_turn_or_none
from .dependencies import Depends
from .entities import citation, mention_entity, sensitivity_label
from .identity import AgenticIdentityError, agentic_user_id, require_agentic_user
from .invokes import (
    InvokeNames,
    card_action,
    card_invoke_response,
    feedback_payload,
    message_invoke_response,
)
from .messages import Message
from .model import Model, get_model, use_model
from .streaming import Streamer
from .surfaces import Teams
from .tracing import OperationName, execute_tool, invoke_agent

__all__ = [
    "PUBLISHABLE_PROTOCOLS",
    "Agent",
    "AgenticIdentityError",
    "Depends",
    "InvokeNames",
    "Message",
    "Model",
    "OperationName",
    "Reaction",
    "Router",
    "Streamer",
    "Teams",
    "Turn",
    "action_chips",
    "adaptive_card",
    "agentic_user_id",
    "card_action",
    "card_invoke_response",
    "citation",
    "current_turn",
    "current_turn_or_none",
    "decision_card",
    "execute_tool",
    "feedback_payload",
    "get_model",
    "invoke_agent",
    "mention_entity",
    "message_invoke_response",
    "require_agentic_user",
    "sensitivity_label",
    "suggested_actions",
    "use_model",
]

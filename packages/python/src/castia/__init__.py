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
from .optimization import (
    AgentConfig,
    apply_optimized_tools,
    configured_model,
    load_agent_config,
    tools_json,
)
from .streaming import Streamer
from .surfaces import Teams
from .toolbox import (
    compose_toolbox_endpoint,
    knowledge_base_mcp_tool,
    platform_endpoint_env,
    resolve_toolbox_endpoint,
    toolbox_mcp_tool,
    toolbox_token,
)
from .tracing import OperationName, execute_tool, invoke_agent

__all__ = [
    "PUBLISHABLE_PROTOCOLS",
    "Agent",
    "AgentConfig",
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
    "apply_optimized_tools",
    "card_action",
    "card_invoke_response",
    "citation",
    "compose_toolbox_endpoint",
    "configured_model",
    "current_turn",
    "current_turn_or_none",
    "decision_card",
    "execute_tool",
    "feedback_payload",
    "get_model",
    "invoke_agent",
    "knowledge_base_mcp_tool",
    "load_agent_config",
    "mention_entity",
    "message_invoke_response",
    "platform_endpoint_env",
    "require_agentic_user",
    "resolve_toolbox_endpoint",
    "sensitivity_label",
    "suggested_actions",
    "toolbox_mcp_tool",
    "toolbox_token",
    "tools_json",
    "use_model",
]

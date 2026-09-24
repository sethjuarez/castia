"""castia -- a tiny FastAPI-shaped framework for Foundry digital workers.

Public surface:

    from castia import Agent, Teams, Message, Depends

Capability modules expose lower-level operations through explicit import paths.
Importing this package is intentionally cheap (no instrumented SDK libraries) so
telemetry can be configured before they load.
"""

from __future__ import annotations

from castia.hosting.identity import (
    AgenticIdentityError,
    agentic_user_id,
    require_agentic_user,
)
from castia.inference.model import Model, get_model, use_model
from castia.integrations.toolbox import (
    ToolboxAuthenticationError,
    ToolboxConfigurationError,
    apply_optimized_toolbox_tools,
    compose_toolbox_endpoint,
    knowledge_base_mcp_tool,
    platform_endpoint_env,
    resolve_toolbox_endpoint,
    toolbox_mcp_tool,
    toolbox_token,
    validate_toolbox_endpoint,
)
from castia.messaging.cards import (
    Reaction,
    action_chips,
    adaptive_card,
    decision_card,
    suggested_actions,
)
from castia.messaging.entities import citation, mention_entity, sensitivity_label
from castia.messaging.invokes import (
    InvokeNames,
    card_action,
    card_invoke_response,
    feedback_payload,
    message_invoke_response,
)
from castia.messaging.messages import Message
from castia.messaging.streaming import Streamer
from castia.messaging.surfaces import Teams
from castia.observe.tracing import (
    OperationName,
    TraceRecord,
    clear_trace_sinks,
    execute_tool,
    invoke_agent,
    jsonl_trace_sink,
    otel_trace_sink,
    register_trace_sink,
    registered_trace_sinks,
    remove_trace_sink,
    trace_attribute,
    trace_step,
)
from castia.optimizing.config import (
    AgentConfig,
    apply_optimized_tools,
    configured_model,
    load_agent_config,
    tools_json,
)
from castia.runtime.application import PUBLISHABLE_PROTOCOLS, Agent, Router
from castia.runtime.context import Turn, current_turn, current_turn_or_none
from castia.runtime.dependencies import Depends
from castia.runtime.request_context import RequestContext, current_request_context

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
    "RequestContext",
    "Router",
    "Streamer",
    "Teams",
    "ToolboxAuthenticationError",
    "ToolboxConfigurationError",
    "TraceRecord",
    "Turn",
    "action_chips",
    "adaptive_card",
    "agentic_user_id",
    "apply_optimized_toolbox_tools",
    "apply_optimized_tools",
    "card_action",
    "card_invoke_response",
    "citation",
    "clear_trace_sinks",
    "compose_toolbox_endpoint",
    "configured_model",
    "current_request_context",
    "current_turn",
    "current_turn_or_none",
    "decision_card",
    "execute_tool",
    "feedback_payload",
    "get_model",
    "invoke_agent",
    "jsonl_trace_sink",
    "knowledge_base_mcp_tool",
    "load_agent_config",
    "mention_entity",
    "message_invoke_response",
    "otel_trace_sink",
    "platform_endpoint_env",
    "register_trace_sink",
    "registered_trace_sinks",
    "remove_trace_sink",
    "require_agentic_user",
    "resolve_toolbox_endpoint",
    "sensitivity_label",
    "suggested_actions",
    "toolbox_mcp_tool",
    "toolbox_token",
    "tools_json",
    "trace_attribute",
    "trace_step",
    "use_model",
    "validate_toolbox_endpoint",
]

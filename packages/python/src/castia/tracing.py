"""GenAI operation spans that the Foundry Traces UI renders with a label.

The Foundry Traces tree labels a span from its ``gen_ai.operation.name``
attribute: ``chat`` shows as **Chat**, ``invoke_agent`` as **Agent**,
``execute_tool`` as **Tool**, and so on. A span without that attribute -- every
transport/HTTP/auth span an agent emits -- falls into the generic **Other**
bucket. The label vocabulary is closed: the UI recognises only the values in
:class:`OperationName`, assigns each its own colour/icon automatically, and
gives no way to define a custom label or colour. An unrecognised value renders
as **Other**, exactly like an unset one.

These helpers wrap a block of work in a correctly-shaped GenAI span so it reads
as a first-class step in the portal. The model call already emits its own
``chat`` span via the instrumentor enabled in :mod:`castia.observability`;
:func:`invoke_agent` gives that call an **Agent** parent, and
:func:`execute_tool` labels a tool/function call **Tool**.

    from castia import invoke_agent, execute_tool

    with invoke_agent():                 # -> "Agent"
        with execute_tool("get_weather"):  # -> "Tool"
            ...
        answer = await model.respond(text)  # model call -> "Chat" (nested)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

#: ``gen_ai.provider.name`` / ``gen_ai.system`` value for Foundry-hosted models.
#: Matches what the model call's own ``chat`` span carries, so parent and child
#: agree on the provider.
PROVIDER = "microsoft.foundry"

_TRACER_NAME = "castia"


class OperationName:
    """The ``gen_ai.operation.name`` values the Foundry Traces UI labels.

    This is the complete, closed set the portal recognises. Any other value is
    rendered as **Other** with no colour or icon -- the UI has no mechanism for
    custom labels or colours.
    """

    CHAT = "chat"
    INVOKE_AGENT = "invoke_agent"
    EXECUTE_TOOL = "execute_tool"
    CREATE_AGENT = "create_agent"
    EMBEDDINGS = "embeddings"
    GENERATE_CONTENT = "generate_content"
    TEXT_COMPLETION = "text_completion"


def _identity_attributes(name: str) -> dict[str, str]:
    """Agent identity attributes, mirroring the identity span processor.

    The processor stamps these on every span already; setting them here too
    keeps the helper self-contained (and correct even if reused without that
    processor) and costs nothing -- the values are identical.
    """
    attributes = {"gen_ai.agent.name": name}
    version = os.environ.get("FOUNDRY_AGENT_VERSION")
    if version:
        attributes["gen_ai.agent.id"] = f"{name}:{version}"
        attributes["gen_ai.agent.version"] = version
    return attributes


@contextmanager
def invoke_agent(name: str | None = None, *, system: str = PROVIDER) -> Iterator[Any]:
    """Wrap a turn's work in an ``invoke_agent`` span (labels it **Agent**).

    ``name`` defaults to the platform-injected ``FOUNDRY_AGENT_NAME``. The span
    is named ``invoke_agent {name}`` to match the ``chat {model}`` convention the
    instrumentor uses, and becomes the parent of any model or tool span created
    inside the block.
    """
    agent_name = name or os.environ.get("FOUNDRY_AGENT_NAME", "agent")
    attributes = {
        "gen_ai.operation.name": OperationName.INVOKE_AGENT,
        "gen_ai.system": system,
        "gen_ai.provider.name": system,
        **_identity_attributes(agent_name),
    }
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        f"{OperationName.INVOKE_AGENT} {agent_name}", attributes=attributes
    ) as span:
        yield span


@contextmanager
def execute_tool(name: str, *, system: str = PROVIDER) -> Iterator[Any]:
    """Wrap a tool/function call in an ``execute_tool`` span (labels it **Tool**).

    ``name`` is the tool being called; the span is named ``execute_tool {name}``
    and carries ``gen_ai.tool.name`` so the portal shows which tool ran.
    """
    attributes = {
        "gen_ai.operation.name": OperationName.EXECUTE_TOOL,
        "gen_ai.system": system,
        "gen_ai.provider.name": system,
        "gen_ai.tool.name": name,
    }
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        f"{OperationName.EXECUTE_TOOL} {name}", attributes=attributes
    ) as span:
        yield span

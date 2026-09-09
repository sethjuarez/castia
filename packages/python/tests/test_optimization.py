"""Unit tests for the optimizer-awareness surface.

Covers the two pure helpers that make *tools* an optimization asset
(``tools_json`` -> baseline serialize; ``apply_optimized_tools`` -> apply a
rewritten candidate), the ``Router.tools`` / ``registered_tools`` declaration
that lets the framework *see* an agent's tools, and the ``Agent.responses_only``
projection that derives the optimizer sibling from the live app. No network, no
Azure -- everything here is pure data.
"""

from __future__ import annotations

from castia import (
    Agent,
    Router,
    apply_optimized_tools,
    tools_json,
)
from castia.tools import Tool


def _tool(name: str, description: str = "orig") -> Tool:
    async def _impl(activity, **kwargs):  # pragma: no cover - never invoked
        return {}

    return Tool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "orig-to"},
                "count": {"type": "integer", "description": "orig-count"},
            },
            "required": ["to"],
            "additionalProperties": False,
        },
        impl=_impl,
    )


# --------------------------------------------------------------------------- #
# tools_json -- baseline serialization                                        #
# --------------------------------------------------------------------------- #


def test_tools_json_emits_nested_function_form():
    out = tools_json([_tool("send_email", "Send an email")])
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Send an email",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "orig-to"},
                        "count": {"type": "integer", "description": "orig-count"},
                    },
                    "required": ["to"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def test_tools_json_empty():
    assert tools_json([]) == []


# --------------------------------------------------------------------------- #
# apply_optimized_tools -- apply a rewritten candidate                         #
# --------------------------------------------------------------------------- #


def test_apply_no_definitions_is_identity():
    tools = [_tool("a"), _tool("b")]
    assert apply_optimized_tools(tools, ()) is tools


def test_apply_overlays_description_by_name():
    tools = [_tool("send_email", "orig"), _tool("read_inbox", "orig")]
    defs = [
        {
            "type": "function",
            "function": {"name": "send_email", "description": "REWRITTEN"},
        }
    ]
    applied = apply_optimized_tools(tools, defs)
    assert applied[0].description == "REWRITTEN"
    # untouched tool passes through by identity
    assert applied[1] is tools[1]


def test_apply_is_immutable_returns_new_tool():
    original = _tool("send_email", "orig")
    defs = [{"function": {"name": "send_email", "description": "new"}}]
    applied = apply_optimized_tools([original], defs)
    assert applied[0] is not original
    assert original.description == "orig"  # frozen source unchanged


def test_apply_overlays_parameter_description_only():
    original = _tool("send_email")
    defs = [
        {
            "function": {
                "name": "send_email",
                "parameters": {
                    "type": "object",
                    "properties": {
                        # rewritten description ...
                        "to": {"type": "string", "description": "BETTER to"},
                        # ... and an attempt to change the *shape* we must ignore
                        "count": {"type": "string", "description": "orig-count"},
                    },
                    "required": [],
                },
            }
        }
    ]
    applied = apply_optimized_tools([original], defs)[0]
    props = applied.parameters["properties"]
    assert props["to"]["description"] == "BETTER to"
    # shape stays authoritative in code: type unchanged, required unchanged
    assert props["count"]["type"] == "integer"
    assert applied.parameters["required"] == ["to"]


def test_apply_accepts_flat_responses_form():
    original = _tool("send_email", "orig")
    # flat spec (Tool.spec() form) rather than nested CC form
    defs = [{"type": "function", "name": "send_email", "description": "flat-new"}]
    applied = apply_optimized_tools([original], defs)[0]
    assert applied.description == "flat-new"


def test_apply_unknown_name_passes_through():
    tools = [_tool("send_email")]
    defs = [{"function": {"name": "not_a_tool", "description": "x"}}]
    applied = apply_optimized_tools(tools, defs)
    assert applied[0] is tools[0]


# --------------------------------------------------------------------------- #
# Router.tools / registered_tools -- declaration + discovery                   #
# --------------------------------------------------------------------------- #


def test_registered_tools_empty_by_default():
    assert Router().registered_tools() == []


def test_registered_tools_flattens_providers():
    r = Router()
    r.tools(lambda: [_tool("a")], lambda: [_tool("b")])
    names = [t.name for t in r.registered_tools()]
    assert names == ["a", "b"]


def test_registered_tools_dedupes_by_name_first_wins():
    r = Router()
    r.tools(lambda: [_tool("dup", "first")], lambda: [_tool("dup", "second")])
    tools = r.registered_tools()
    assert [t.name for t in tools] == ["dup"]
    assert tools[0].description == "first"


def test_tools_providers_are_lazy():
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return [_tool("a")]

    r = Router()
    r.tools(provider)
    assert calls["n"] == 0  # registration must not call the provider
    r.registered_tools()
    assert calls["n"] == 1


def test_include_merges_tool_providers():
    parent = Agent(name="p")
    child = Router()
    child.tools(lambda: [_tool("a")])
    parent.include(child)
    assert [t.name for t in parent.registered_tools()] == ["a"]


# --------------------------------------------------------------------------- #
# Agent.responses_only -- the optimizer sibling projection                     #
# --------------------------------------------------------------------------- #


def _responses_agent(name: str = "hal") -> Agent:
    app = Agent(name=name)

    @app.responses()
    async def reply(text: str) -> str:  # pragma: no cover - not invoked here
        return text

    return app


def test_responses_only_projects_handler_and_tools():
    app = _responses_agent()
    app.tools(lambda: [_tool("a")])
    sib = app.responses_only()
    assert sib.registered_protocols() == ["responses"]
    assert [t.name for t in sib.registered_tools()] == ["a"]


def test_responses_only_default_name():
    assert _responses_agent("hal").responses_only().name == "hal-optimize"
    assert _responses_agent("hal").responses_only(name="custom").name == "custom"


def test_responses_only_requires_responses_handler():
    app = Agent(name="hal")
    try:
        app.responses_only()
    except ValueError as exc:
        assert "responses" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_responses_only_is_independent_of_parent():
    app = _responses_agent()
    sib = app.responses_only()
    sib.tools(lambda: [_tool("only_on_sibling")])
    # mutating the projection must not leak back into the parent
    assert app.registered_tools() == []


def test_agentconfig_apply_tools_delegates():
    from castia import AgentConfig

    cfg = AgentConfig(
        model="gpt-4o",
        instructions=None,
        source="local",
        tool_definitions=({"function": {"name": "a", "description": "cfg"}},),
    )
    applied = cfg.apply_tools([_tool("a", "orig")])
    assert applied[0].description == "cfg"

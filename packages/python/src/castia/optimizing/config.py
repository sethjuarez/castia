"""Optimizer-awareness: resolve the agent's config from the Foundry Optimizer.

This is what makes a ``castia`` agent *optimizer-ready*. At startup the agent
loads its **baseline** configuration (system instructions, model, and optional
tool descriptions); during an optimization run it transparently loads the
**candidate** configuration the optimizer injects. The handler code path is
identical in both states -- no feature flags, no conditional logic -- so
optimizing an agent optimizes the exact config it serves in production.

Resolution is delegated to :func:`azure.ai.agentserver.optimization.load_config`,
which prefers an inline ``OPTIMIZATION_CONFIG`` JSON blob, then the resolver API
(``OPTIMIZATION_CANDIDATE_ID`` + endpoint), then the local ``.agent_configs/``
directory, then ``None``. When that package or a config source is absent we
degrade to environment defaults, so an agent runs identically with or without
the optimizer installed.

The optimizer's on-disk contract (``.agent_configs/<candidate>/``):

* ``metadata.yaml`` -- ``model`` / ``temperature`` / ``instruction_file`` /
  ``skill_dir`` / ``tool_file`` pointers (``tools_file`` is also accepted for
  compatibility with ``azd ai agent optimize``),
* ``instructions.md`` -- the system prompt,
* ``tools.json`` -- tool definitions in OpenAI *nested* function-calling list
  form (``[{"type":"function","function":{name,description,parameters}}]``),
* ``skills/<name>/SKILL.md`` -- learned skills.

Tool descriptions are an optimizer target only when a ``tools.json`` is present:
:func:`apply_optimized_tools` folds any rewritten descriptions back onto the
agent's :class:`~castia.inference.tools.Tool` list by name.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from castia.integrations.toolbox import (
    OPTIMIZER_TOOL_DEFINITIONS_KEY,
    apply_optimized_toolbox_tools,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from castia.inference.model import Model
    from castia.inference.tools import Tool

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentConfig:
    """The resolved agent configuration the handlers actually use.

    ``tool_definitions`` carries the optimizer's ``tools.json`` entries (nested
    OpenAI function-calling form) so rewritten tool descriptions can be applied
    to the live tool list via :meth:`apply_tools`.
    """

    model: str
    instructions: str | None
    source: str
    tool_definitions: tuple[dict, ...] = ()

    def apply_tools(self, tools: list[Tool | dict]) -> list[Tool | dict]:
        """Return ``tools`` with any optimizer-rewritten descriptions applied."""
        return apply_optimized_tools(tools, self.tool_definitions)


def load_agent_config(config_dir: str | os.PathLike | None = None) -> AgentConfig:
    """Resolve the agent's model + instructions (+ tools), preferring an optimizer config.

    Best-effort and non-fatal: any failure degrades to environment defaults so
    startup never breaks because of the optimizer integration. Pass ``config_dir``
    to anchor the local ``.agent_configs`` lookup explicitly; by default it honors
    an ``OPTIMIZATION_LOCAL_DIR`` override and otherwise lets ``load_config()``
    resolve ``.agent_configs`` next to the entrypoint.

    Directory-resolution gotcha: ``load_config()`` resolves a relative
    ``.agent_configs`` against ``__main__.__file__``'s directory -- correct under
    ``python main.py`` but wrong under ``python -m castia`` (it resolves to the
    package dir and finds nothing). Pass an explicit ``config_dir`` anchored to
    your app root, or set ``OPTIMIZATION_LOCAL_DIR``, so the baseline resolves
    identically no matter how the process is launched.
    """
    default_model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")

    try:
        from azure.ai.agentserver.optimization import load_config
    except Exception:  # noqa: BLE001 - package absent; run as a plain agent
        _logger.info("optimization package absent; using env defaults")
        return AgentConfig(default_model, None, "default")

    try:
        config = load_config(config_dir=config_dir)
    except Exception:  # never let config resolution break startup
        _logger.warning("load_config() failed; using env defaults", exc_info=True)
        return AgentConfig(default_model, None, "default")

    if config is None:
        _logger.info("no optimization config found; using env defaults")
        return AgentConfig(default_model, None, "default")

    # compose_instructions() returns the prompt with any discovered skills
    # appended; empty string -> None so the model call omits instructions.
    instructions = config.compose_instructions() or None
    model = config.model or default_model
    tool_definitions = tuple(config.tool_definitions or ())
    if not tool_definitions:
        # azd ai agent optimize currently writes/reads the local metadata key as
        # "tools_file", while azure-ai-agentserver-optimization reads "tool_file".
        # Tolerate the azd spelling so applied candidates still affect runtime.
        tool_definitions = tuple(_load_azd_tools_file_definitions(config))
    _logger.info(
        "optimization config source=%s | model=%s | prompt_len=%d | tools=%d",
        config.source,
        model,
        len(instructions or ""),
        len(tool_definitions),
    )
    return AgentConfig(model, instructions, config.source, tool_definitions)


def _load_azd_tools_file_definitions(config: object) -> list[dict]:
    source = getattr(config, "source", "")
    if not isinstance(source, str) or not source.startswith("local:"):
        return []

    candidate_path = Path(source.removeprefix("local:"))
    metadata_path = candidate_path / "metadata.yaml"
    if not metadata_path.is_file():
        return []

    tools_file = _metadata_value(metadata_path, "tools_file")
    if not tools_file:
        return []

    tools_path = Path(tools_file)
    if not tools_path.is_absolute():
        tools_path = candidate_path / tools_path
    if not tools_path.is_file():
        return []

    try:
        loaded = json.loads(tools_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _logger.warning("failed to load azd tools_file %s", tools_path, exc_info=True)
        return []
    return loaded if isinstance(loaded, list) else []


def _metadata_value(path: Path, key: str) -> str | None:
    prefix = f"{key}:"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix) :].strip()
            return value.strip("'\"") or None
    return None


def apply_optimized_tools(
    tools: list[Tool | dict], tool_definitions: tuple[dict, ...] | list[dict]
) -> list[Tool | dict]:
    """Fold optimizer-rewritten descriptions onto ``tools``, matched by name.

    The optimizer may rewrite tool (and tool-parameter) descriptions as a
    distinct optimization target from the system prompt. Its ``tools.json``
    entries are in nested OpenAI form (``{"type":"function","function":{...}}``);
    for each entry whose ``function.name`` matches a :class:`~castia.inference.tools.Tool`
    we return a **new** tool (``Tool`` is deliberately frozen/immutable) carrying
    the rewritten ``description`` and any rewritten parameter descriptions.
    Tools with no matching definition pass through unchanged, so an empty or
    partial ``tools.json`` is safe.
    """
    if not tool_definitions:
        return tools

    lookup: dict[str, dict] = {}
    for item in tool_definitions:
        if not isinstance(item, dict):
            continue
        func = item.get("function")
        # Accept both the nested CC form and a flat Responses-style spec.
        if not isinstance(func, dict):
            func = item if item.get("name") else {}
        name = func.get("name")
        if name:
            lookup[name] = func

    if not lookup:
        return tools

    applied: list[Tool | dict] = []
    for tool in tools:
        if isinstance(tool, dict):
            applied.append(apply_optimized_toolbox_tools(tool, tool_definitions))
            continue
        func_def = lookup.get(tool.name)
        if func_def is None:
            applied.append(tool)
            continue
        description = func_def.get("description") or tool.description
        parameters = _merge_parameter_descriptions(
            tool.parameters, func_def.get("parameters")
        )
        applied.append(replace(tool, description=description, parameters=parameters))
    return applied


def _merge_parameter_descriptions(
    current: dict, optimized: object | None
) -> dict:
    """Overlay optimized per-parameter ``description`` text onto ``current``.

    Only descriptions are copied across -- the JSON-schema shape (types,
    ``required``, ``additionalProperties``) stays authoritative in code, so a
    rewritten description can never change a tool's call contract.
    """
    if not isinstance(optimized, dict):
        return current
    opt_props = optimized.get("properties")
    cur_props = current.get("properties")
    if not isinstance(opt_props, dict) or not isinstance(cur_props, dict):
        return current

    merged_props = {}
    changed = False
    for name, schema in cur_props.items():
        opt_schema = opt_props.get(name)
        if (
            isinstance(schema, dict)
            and isinstance(opt_schema, dict)
            and opt_schema.get("description")
            and opt_schema.get("description") != schema.get("description")
        ):
            merged_props[name] = {**schema, "description": opt_schema["description"]}
            changed = True
        else:
            merged_props[name] = schema

    if not changed:
        return current
    return {**current, "properties": merged_props}


def tools_json(tools: list[Tool | dict]) -> list[dict]:
    """Serialize ``tools`` to the optimizer's ``tools.json`` (nested) format.

    Emits the OpenAI *nested* function-calling list form the optimizer's
    ``tool_file`` expects -- ``[{"type":"function","function":{name,description,
    parameters}}]`` -- so a baseline ``tools.json`` can be generated straight from
    the agent's registered tools (see ``python -m castia optimize``). Raw MCP
    specs produced by :func:`castia.toolbox_mcp_tool` may also carry private
    optimizer-visible federated tool definitions; those are emitted here while
    the raw MCP server spec stays out of ``tools.json``. This is the inverse of
    :func:`apply_optimized_tools` for local function tools.
    """
    out: list[dict] = []
    for tool in tools:
        if isinstance(tool, dict):
            out.extend(tool.get(OPTIMIZER_TOOL_DEFINITIONS_KEY, ()))
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return out


def tools_json_names(tools: list[Tool | dict]) -> list[str]:
    """Return the optimizer-visible tool names represented by ``tools``."""
    return [item["function"]["name"] for item in tools_json(tools)]


_FUNCTION_KEYS = frozenset({"name", "description", "parameters"})


def validate_optimized_tools(
    tools: list[Tool | dict],
    tool_definitions: Sequence[Mapping[str, Any]],
    *,
    require_all: bool = True,
) -> None:
    """Fail loud when an optimizer candidate changes more than tool wording.

    ``tool_definitions`` (a candidate ``tools.json``) may only rewrite what
    :func:`apply_optimized_tools` applies: each tool's ``description`` and the
    ``description`` of its top-level parameter properties. Anything else --
    a renamed, missing (with ``require_all``), duplicate or unknown tool, an
    extra envelope or function field (e.g. ``strict``), an empty description,
    or any schema change -- raises :class:`ValueError`, so a candidate can't
    be silently half-applied against the dispatched schema. Accepts nested
    (``{"type":"function","function":{...}}``) and flat Responses entries.
    """
    baseline = {item["function"]["name"]: item["function"] for item in tools_json(tools)}
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(tool_definitions):
        where = f"tool_definitions[{index}]"
        if not isinstance(item, Mapping) or item.get("type") != "function":
            problems.append(f"{where}: expected a function tool entry.")
            continue
        nested = "function" in item
        func = item.get("function") if nested else item
        allowed = {"type", "function"} if nested else {"type", *_FUNCTION_KEYS}
        if not isinstance(func, Mapping) or set(item) - allowed:
            problems.append(f"{where}: changes the function-tool envelope.")
            continue
        name = func.get("name")
        if name not in baseline:
            problems.append(f"{where}: unknown tool {name!r}.")
            continue
        if name in seen:
            problems.append(f"{where}: duplicate tool {name!r}.")
            continue
        seen.add(name)
        extra = set(func) - _FUNCTION_KEYS - ({"type"} if not nested else set())
        if extra:
            problems.append(f"{name}: unsupported function fields {sorted(extra)}.")
        description = func.get("description")
        if not isinstance(description, str) or not description.strip():
            problems.append(f"{name}: description must be non-empty text.")
        problems.extend(
            f"{name}: {issue}"
            for issue in _parameter_wording_issues(baseline[name].get("parameters"), func.get("parameters"))
        )
    if require_all:
        problems.extend(f"missing tool {name!r}." for name in baseline if name not in seen)
    if problems:
        raise ValueError("Optimized tool definitions change more than descriptions: " + " ".join(problems))


def _parameter_wording_issues(current: object, candidate: object) -> list[str]:
    if not isinstance(candidate, Mapping):
        return ["parameters are missing or not an object."]
    if not isinstance(current, Mapping):
        return [] if candidate == current else ["parameters schema changed."]
    cur_props = current.get("properties")
    cand_props = candidate.get("properties")
    if not isinstance(cur_props, Mapping) or not isinstance(cand_props, Mapping):
        return [] if dict(candidate) == dict(current) else ["parameters schema changed."]
    issues: list[str] = []
    normalized: dict[str, Any] = {}
    for prop, schema in cand_props.items():
        base = cur_props.get(prop)
        if isinstance(schema, Mapping) and isinstance(base, Mapping) and "description" in schema:
            text = schema["description"]
            if not isinstance(text, str) or not text.strip():
                issues.append(f"parameter {prop!r} description must be non-empty text.")
            schema = {k: v for k, v in schema.items() if k != "description"}
            if "description" in base:
                schema["description"] = base["description"]
        normalized[prop] = schema
    if {**candidate, "properties": normalized} != dict(current):
        issues.append("parameters schema changed (only descriptions may change).")
    return issues


def configured_model(
    config: AgentConfig | None = None,
) -> Callable[[], Model]:
    """A model dependency bound to the resolved optimizer config.

    Drop-in for :func:`castia.get_model` that threads the baseline (or
    optimizer-injected candidate) ``model`` + ``instructions`` + tool-definition
    rewrites into every :class:`~castia.inference.model.Model` it builds, so an optimized
    prompt or toolbox description takes effect without any handler change::

        from castia import Depends, Model, configured_model

        gpt = configured_model()

        @router.responses()
        async def reply(text: str, model: Model = Depends(gpt)) -> str:
            return await model.respond(text)

    The config is resolved once (at call time if not supplied) and captured, so
    the dependency stays cheap and every handler shares one config.
    """
    resolved = config or load_agent_config()

    def provider() -> Model:
        from castia.inference.model import Model

        return Model(
            resolved.model,
            instructions=resolved.instructions,
            tool_definitions=resolved.tool_definitions,
        )

    return provider

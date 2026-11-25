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
directory, then ``None``. When that package or every config source is absent
we degrade to environment defaults, so an agent runs identically with or
without the optimizer installed. A source that is *present but unusable*
(malformed inline JSON, unreadable tools file, resolver error, empty candidate
instructions) raises :class:`CandidateConfigError` instead of silently serving
defaults. A requested ``OPTIMIZATION_CANDIDATE_ID`` that falls back to baseline
logs a warning with the config hash, or raises when strict
(``strict=True`` / ``CASTIA_OPTIMIZATION_STRICT=1``). ``CASTIA_OPTIMIZATION_LENIENT=1``
restores the old degrade-to-defaults behavior. Resolution is not cached, so
each call reflects the currently injected candidate.

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

import hashlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

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
    config_hash: str = field(default="", compare=False)

    def apply_tools(self, tools: list[Tool | dict]) -> list[Tool | dict]:
        """Return ``tools`` with any optimizer-rewritten descriptions applied."""
        return apply_optimized_tools(tools, self.tool_definitions)


class CandidateConfigError(RuntimeError):
    """An optimizer config source was present but could not be used."""


STRICT_ENV = "CASTIA_OPTIMIZATION_STRICT"
LENIENT_ENV = "CASTIA_OPTIMIZATION_LENIENT"
_CANDIDATE_ENV = "OPTIMIZATION_CANDIDATE_ID"
_INLINE_ENV = "OPTIMIZATION_CONFIG"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _config_hash(model: str, instructions: str | None, tools: tuple[dict, ...]) -> str:
    payload = json.dumps(
        {"model": model, "instructions": instructions, "tools": list(tools)},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _default_config(model: str) -> AgentConfig:
    return AgentConfig(model, None, "default", (), _config_hash(model, None, ()))


def load_agent_config(
    config_dir: str | os.PathLike | None = None, *, strict: bool | None = None
) -> AgentConfig:
    """Resolve the agent's model + instructions (+ tools), preferring an optimizer config.

    A config source that is present but unusable raises
    :class:`CandidateConfigError` instead of silently serving defaults: a
    malformed or empty inline ``OPTIMIZATION_CONFIG``, an unreadable
    ``metadata.yaml`` or ``tools.json``, or an optimizer candidate with no
    instructions. When ``OPTIMIZATION_CANDIDATE_ID`` is set but resolution falls
    back to the baseline or defaults, a warning names the candidate and the
    served config hash; ``strict=True`` (or ``CASTIA_OPTIMIZATION_STRICT=1``)
    turns that fallback into an error too. ``strict=False`` (or
    ``CASTIA_OPTIMIZATION_LENIENT=1``) restores the old best-effort behavior,
    logging every failure and serving environment defaults. A missing
    optimization package always degrades to defaults.

    Pass ``config_dir`` to anchor the local ``.agent_configs`` lookup
    explicitly; by default it honors an ``OPTIMIZATION_LOCAL_DIR`` override and
    otherwise lets ``load_config()`` resolve ``.agent_configs`` next to the
    entrypoint.

    Directory-resolution gotcha: ``load_config()`` resolves a relative
    ``.agent_configs`` against ``__main__.__file__``'s directory -- correct under
    ``python main.py`` but wrong under ``python -m castia`` (it resolves to the
    package dir and finds nothing). Pass an explicit ``config_dir`` anchored to
    your app root, or set ``OPTIMIZATION_LOCAL_DIR``, so the baseline resolves
    identically no matter how the process is launched.
    """
    default_model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    if strict is None:
        strict = True if _env_flag(STRICT_ENV) else (False if _env_flag(LENIENT_ENV) else None)
    lenient = strict is False
    candidate_id = os.environ.get(_CANDIDATE_ENV, "").strip()

    try:
        from azure.ai.agentserver.optimization import load_config
    except Exception:  # noqa: BLE001 - package absent; run as a plain agent
        _logger.info("optimization package absent; using env defaults")
        return _default_config(default_model)

    try:
        _check_inline_config()
        config = load_config(config_dir=config_dir)
        if config is not None:
            _check_local_artifact(config)
    except Exception as exc:
        if lenient:
            _logger.warning(
                "optimizer config failed (lenient); using env defaults", exc_info=True
            )
            return _default_config(default_model)
        if isinstance(exc, CandidateConfigError):
            raise
        raise CandidateConfigError(f"optimizer config could not be loaded: {exc}") from exc

    if config is None:
        result = _default_config(default_model)
    else:
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
        result = AgentConfig(
            model,
            instructions,
            config.source,
            tool_definitions,
            _config_hash(model, instructions, tool_definitions),
        )
        if instructions is None and _served_candidate(config.source, candidate_id) and not lenient:
            raise CandidateConfigError(
                f"optimizer candidate {candidate_id!r} from {config.source} has no instructions"
            )

    if candidate_id and not _served_candidate(result.source, candidate_id):
        message = (
            f"{_CANDIDATE_ENV}={candidate_id!r} did not resolve; serving "
            f"{result.source} config (hash {result.config_hash}) instead"
        )
        if strict:
            raise CandidateConfigError(message)
        _logger.warning(message)

    _logger.info(
        "optimization config source=%s | hash=%s | model=%s | prompt_len=%d | tools=%d",
        result.source,
        result.config_hash,
        result.model,
        len(result.instructions or ""),
        len(result.tool_definitions),
    )
    return result


def _served_candidate(source: str, candidate_id: str) -> bool:
    """Whether ``source`` is the requested candidate rather than a fallback."""
    if source.startswith("env:"):
        return True
    if not candidate_id:
        return False
    if source == f"api:candidate:{candidate_id}":
        return True
    return source.startswith("local:") and Path(source.removeprefix("local:")).name == candidate_id


def _check_inline_config() -> None:
    raw = os.environ.get(_INLINE_ENV)
    if raw is None or not raw.strip():
        if raw is not None and raw != "":
            raise CandidateConfigError(f"{_INLINE_ENV} is set but blank")
        return
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CandidateConfigError(f"{_INLINE_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict) or not loaded:
        raise CandidateConfigError(f"{_INLINE_ENV} must be a non-empty JSON object")
    instructions = loaded.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise CandidateConfigError(f"{_INLINE_ENV} must include non-empty instructions")


def _check_local_artifact(config: object) -> None:
    source = getattr(config, "source", "")
    if not isinstance(source, str) or not source.startswith("local:"):
        return
    candidate_path = Path(source.removeprefix("local:"))
    metadata_path = candidate_path / "metadata.yaml"
    names = {"tools.json"}
    if metadata_path.is_file():
        for key in ("tool_file", "tools_file"):
            value = _metadata_value(metadata_path, key)
            if value:
                names.add(value)
    for name in sorted(names):
        path = Path(name) if Path(name).is_absolute() else candidate_path / name
        if not path.is_file():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CandidateConfigError(f"invalid tools file {path}: {exc}") from exc
        if not isinstance(loaded, list):
            raise CandidateConfigError(f"invalid tools file {path}: expected a JSON list")


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

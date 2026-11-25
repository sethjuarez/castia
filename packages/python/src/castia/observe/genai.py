"""Pure OpenTelemetry GenAI semantic-convention shapes Castia emits.

Everything here is side-effect free and import-cheap: no OpenTelemetry, Azure,
or HTTP imports. :mod:`castia.observe.tracing`, :mod:`castia.observe.server_tools`
and :mod:`castia.observe.correlation` turn these shapes into spans and events.
``spec/conformance/observe/genai_semconv.json`` pins the same behavior for every
Castia SDK.

The GenAI conventions moved out of the core semantic-conventions repository in
v1.42.0 into ``open-telemetry/semantic-conventions-genai``, which has no tagged
release yet. Castia pins the last versioned cut, v1.42.0, and reports it as the
instrumentation schema URL.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

#: GenAI semantic-convention version Castia's spans and events follow.
GENAI_SEMCONV_VERSION = "1.42.0"
#: Schema URL attached to Castia's tracer and logger.
GENAI_SEMCONV_SCHEMA_URL = f"https://opentelemetry.io/schemas/{GENAI_SEMCONV_VERSION}"

#: ``gen_ai.provider.name`` / ``gen_ai.system`` value for Foundry-hosted models.
PROVIDER = "microsoft.foundry"

#: Event name for evaluation results, human or automated.
EVALUATION_RESULT_EVENT = "gen_ai.evaluation.result"

#: Responses ``output`` item types the provider executes server-side.
SERVER_TOOL_TYPES = (
    "mcp_call",
    "web_search_call",
    "file_search_call",
    "code_interpreter_call",
    "image_generation_call",
    "computer_call",
    "local_shell_call",
    "custom_tool_call",
)

_MAX_ERROR_MESSAGE = 500
_HTTP_ERROR = re.compile(r"\b([45]\d\d)\b")
_REQUEST_PARAMETERS = (
    ("temperature", "gen_ai.request.temperature"),
    ("top_p", "gen_ai.request.top_p"),
    ("top_k", "gen_ai.request.top_k"),
    ("max_output_tokens", "gen_ai.request.max_tokens"),
    ("max_tokens", "gen_ai.request.max_tokens"),
    ("frequency_penalty", "gen_ai.request.frequency_penalty"),
    ("presence_penalty", "gen_ai.request.presence_penalty"),
    ("seed", "gen_ai.request.seed"),
    ("choice_count", "gen_ai.request.choice.count"),
)


# --------------------------------------------------------------------------- content


def text_messages(
    role: str, text: str, *, finish_reason: str | None = None
) -> list[dict[str, Any]]:
    """Return one ``gen_ai.input.messages`` / ``gen_ai.output.messages`` entry."""
    message: dict[str, Any] = {"role": role, "parts": [{"type": "text", "content": text}]}
    if finish_reason:
        message["finish_reason"] = finish_reason
    return [message]


def system_instructions(text: str) -> list[dict[str, Any]]:
    """Return the ``gen_ai.system_instructions`` value for plain-text instructions."""
    return [{"type": "text", "content": text}]


def request_parameter_attributes(params: Mapping[str, Any]) -> dict[str, Any]:
    """Map request parameters to ``gen_ai.request.*``, keeping zero values.

    ``temperature=0`` and ``seed=0`` are deliberate settings and must be
    recorded; only ``None`` (unset) is dropped. Booleans are rejected because
    ``True`` would otherwise be recorded as ``1``.
    """
    attributes: dict[str, Any] = {}
    for key, attribute in _REQUEST_PARAMETERS:
        value = params.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            attributes.setdefault(attribute, value)
    stop = params.get("stop")
    if isinstance(stop, str):
        attributes["gen_ai.request.stop_sequences"] = [stop]
    elif isinstance(stop, list | tuple) and all(isinstance(item, str) for item in stop):
        attributes["gen_ai.request.stop_sequences"] = list(stop)
    return attributes


# --------------------------------------------------------------------------- server tools


def server_tool_calls(output: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Normalize provider-executed tool items from a Responses ``output`` list.

    Each entry has ``name``, ``call_id``, ``item_type``, ``tool_type``,
    ``server_label``, ``status`` (``ok`` or ``error``), ``error_type``,
    ``error_message``, ``arguments`` and ``result``. ``arguments`` and ``result``
    are only populated for ``mcp_call`` items.
    """
    calls: list[dict[str, Any]] = []
    for item in output or []:
        item_type = str(_field(item, "type") or "")
        if item_type not in SERVER_TOOL_TYPES:
            continue
        error = _field(item, "error")
        item_status = _field(item, "status")
        failed = bool(error) or item_status in ("failed", "incomplete")
        is_mcp = item_type == "mcp_call"
        calls.append(
            {
                "name": _text(_field(item, "name"))
                or _text(_field(item, "server_label"))
                or item_type,
                "call_id": _text(_field(item, "call_id")) or _text(_field(item, "id")),
                "item_type": item_type,
                "tool_type": "datastore" if item_type == "file_search_call" else "extension",
                "server_label": _text(_field(item, "server_label")),
                "status": "error" if failed else "ok",
                "error_type": _error_type(error, item_status) if failed else None,
                "error_message": _error_message(error) if failed else None,
                "arguments": _field(item, "arguments") if is_mcp else None,
                "result": _field(item, "output") if is_mcp else None,
            }
        )
    return calls


def server_tool_span(
    call: Mapping[str, Any],
    *,
    system: str | None = None,
    iteration: int | None = None,
    include_content: bool = False,
) -> dict[str, Any]:
    """Return the ``execute_tool`` span shape for one normalized server tool call."""
    provider = system or PROVIDER
    name = str(call.get("name") or "tool")
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.system": provider,
        "gen_ai.provider.name": provider,
        "gen_ai.tool.name": name,
        "gen_ai.tool.type": call.get("tool_type") or "extension",
        "castia.telemetry.source": "castia",
        "castia.tool.source": "server",
        "castia.tool.synthesized": True,
        "castia.tool.item_type": call.get("item_type"),
        "castia.tool.status": call.get("status") or "ok",
    }
    if call.get("call_id"):
        attributes["gen_ai.tool.call.id"] = call["call_id"]
    if call.get("server_label"):
        attributes["castia.mcp.server_label"] = call["server_label"]
    if iteration is not None:
        attributes["castia.model.iteration"] = iteration
    if call.get("error_type"):
        attributes["error.type"] = call["error_type"]
    if include_content:
        for key, attribute in (
            ("arguments", "gen_ai.tool.call.arguments"),
            ("result", "gen_ai.tool.call.result"),
        ):
            value = call.get(key)
            if value is not None:
                attributes[attribute] = value if isinstance(value, str) else _json(value)
    failed = call.get("status") == "error"
    return {
        "span_name": f"execute_tool {name}",
        "status": "error" if failed else "ok",
        "status_description": call.get("error_message") if failed else None,
        "attributes": attributes,
    }


# --------------------------------------------------------------------------- evaluations


def human_evaluation_attributes(
    name: str,
    *,
    score: float | None = None,
    label: str | None = None,
    explanation: str | None = None,
    response_id: str | None = None,
    agent_id: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    """Return ``gen_ai.evaluation.result`` attributes for a human review."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("evaluation name must be a non-empty string")
    if score is None and not label:
        raise ValueError("a human evaluation needs a score, a label, or both")
    if score is not None and (isinstance(score, bool) or not isinstance(score, int | float)):
        raise TypeError("evaluation score must be a number")
    attributes: dict[str, Any] = {
        "gen_ai.evaluation.name": name.strip(),
        "castia.evaluation.source": "human",
    }
    if score is not None:
        attributes["gen_ai.evaluation.score.value"] = float(score)
    if label:
        attributes["gen_ai.evaluation.score.label"] = label
    if explanation:
        attributes["gen_ai.evaluation.explanation"] = explanation
    if response_id:
        attributes["gen_ai.response.id"] = response_id
    if agent_id:
        attributes["gen_ai.agent.id"] = agent_id
    if reviewer:
        attributes["castia.evaluation.reviewer"] = reviewer
    return attributes


def parse_traceparent(value: str) -> tuple[str, str, bool] | None:
    """Return ``(trace_id, span_id, sampled)`` from a W3C ``traceparent``."""
    match = re.fullmatch(
        r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})",
        (value or "").strip().lower(),
    )
    if not match or match.group(1) == "ff":
        return None
    trace_id, span_id = match.group(2), match.group(3)
    if set(trace_id) == {"0"} or set(span_id) == {"0"}:
        return None
    return trace_id, span_id, bool(int(match.group(4), 16) & 1)


# --------------------------------------------------------------------------- helpers


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _error_type(error: Any, item_status: Any) -> str:
    if isinstance(error, Mapping) or (error and not isinstance(error, str)):
        for key in ("type", "code"):
            value = _field(error, key)
            if value not in (None, ""):
                return str(value)
        return "server_tool_error"
    if isinstance(error, str) and error:
        match = _HTTP_ERROR.search(error)
        return f"http_{match.group(1)}" if match else "server_tool_error"
    return f"server_tool_{item_status}" if item_status else "server_tool_error"


def _error_message(error: Any) -> str | None:
    if isinstance(error, str):
        message = error
    elif error:
        message = _field(error, "message")
        if not isinstance(message, str):
            return None
    else:
        return None
    return message[:_MAX_ERROR_MESSAGE]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "EVALUATION_RESULT_EVENT",
    "GENAI_SEMCONV_SCHEMA_URL",
    "GENAI_SEMCONV_VERSION",
    "PROVIDER",
    "SERVER_TOOL_TYPES",
    "human_evaluation_attributes",
    "parse_traceparent",
    "request_parameter_attributes",
    "server_tool_calls",
    "server_tool_span",
    "system_instructions",
    "text_messages",
]

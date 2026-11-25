"""Foundry Responses identifiers, matching the agentserver ``IdGenerator``.

Hosted Foundry agents share one id space with the platform: the gateway may
allocate a response id (``x-agent-response-id``), a caller may supply one
(``response_id``), and Foundry storage rejects ids that do not have the
``{prefix}_{partition}{entropy}`` shape. These helpers mirror
``azure.ai.agentserver.responses._id_generator.IdGenerator`` so Castia mints,
accepts, and co-locates ids the same way.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from uuid import uuid4

__all__ = [
    "RESPONSE_ID_HEADER",
    "InvalidResponseIdError",
    "item_id",
    "platform_id",
    "resolve_response_id",
    "validate_id",
]

RESPONSE_ID_HEADER = "x-agent-response-id"

_PARTITION_SUFFIX = "00"
_PARTITION_LENGTH = 18
_ENTROPY_LENGTH = 32
_BODY_LENGTH = _PARTITION_LENGTH + _ENTROPY_LENGTH
_LEGACY_BODY_LENGTH = 48
_LEGACY_PARTITION_LENGTH = 16

# Item ``type`` -> id prefix, as in ``IdGenerator.new_item_id``.
_ITEM_PREFIXES = {
    "message": "msg",
    "output_message": "om",
    "function_call": "fc",
    "function_call_output": "fco",
    "custom_tool_call": "ctc",
    "custom_tool_call_output": "ctco",
    "computer_call": "cu",
    "computer_call_output": "cuo",
    "file_search_call": "fs",
    "web_search_call": "ws",
    "image_generation_call": "ig",
    "code_interpreter_call": "ci",
    "local_shell_call": "lsh",
    "local_shell_call_output": "lsho",
    "shell_call": "lsh",
    "shell_call_output": "lsho",
    "apply_patch_call": "ap",
    "apply_patch_call_output": "apo",
    "mcp_list_tools": "mcpl",
    "mcp_call": "mcp",
    "mcp_approval_request": "mcpr",
    "mcp_approval_response": "mcpa",
    "reasoning": "rs",
    "program": "prog",
    "program_output": "progo",
    "compaction": "cmp",
    "compaction_summary": "cmp",
    "structured_outputs": "fco",
    "tool_search_call": "ts",
    "tool_search_output": "tso",
    "additional_tools": "adt",
    "oauth_consent_request": "oauth",
    "memory_search_call": "mem",
    "workflow_action": "wfa",
    "a2a_preview_call": "a2a",
    "a2a_preview_call_output": "a2ao",
    "bing_grounding_call": "bg",
    "bing_grounding_call_output": "bgo",
    "sharepoint_grounding_preview_call": "sp",
    "sharepoint_grounding_preview_call_output": "spo",
    "azure_ai_search_call": "ais",
    "azure_ai_search_call_output": "aiso",
    "bing_custom_search_preview_call": "bcs",
    "bing_custom_search_preview_call_output": "bcso",
    "openapi_call": "oa",
    "openapi_call_output": "oao",
    "browser_automation_preview_call": "ba",
    "browser_automation_preview_call_output": "bao",
    "fabric_dataagent_preview_call": "fda",
    "fabric_dataagent_preview_call_output": "fdao",
    "azure_function_call": "azf",
    "azure_function_call_output": "azfo",
}


class InvalidResponseIdError(ValueError):
    """A caller- or gateway-supplied response id is not a Foundry id."""


def extract_partition_key(value: str | None) -> str | None:
    """``IdGenerator.extract_partition_key``: the raw partition segment, or ``None``.

    New-format ids yield their 18-char key; legacy ids yield the trailing
    16 chars unchanged (no ``00`` suffix).
    """
    if not value:
        return None
    _, delimiter, body = value.partition("_")
    if not delimiter:
        return None
    if len(body) == _BODY_LENGTH:
        return body[:_PARTITION_LENGTH]
    if len(body) == _LEGACY_BODY_LENGTH:
        return body[-_LEGACY_PARTITION_LENGTH:]
    return None


def partition_key(like: str | None) -> str | None:
    """The partition key to reuse when minting beside ``like``, or ``None``."""
    raw = extract_partition_key(like)
    if raw is not None and len(raw) == _LEGACY_PARTITION_LENGTH:
        return raw + _PARTITION_SUFFIX
    return raw


def platform_id(prefix: str, like: str | None = None) -> str:
    """Mint ``{prefix}_{16 hex}00{32 hex}``, the Foundry Responses id format.

    ``like`` reuses an existing id's partition key (new or legacy format) so
    items are co-located with their response, as ``IdGenerator.new_id`` does.
    """
    if not prefix:
        raise ValueError("Prefix must not be empty.")
    partition = partition_key(like) or f"{secrets.token_hex(8)}{_PARTITION_SUFFIX}"
    return f"{prefix}_{partition}{uuid4().hex}"


def validate_id(value: object) -> str | None:
    """Return why ``value`` is not a valid Foundry id, or ``None`` when it is.

    Same rules as ``IdGenerator.is_valid``: a ``_`` delimiter, a non-empty
    prefix, and a body of 50 (current) or 48 (legacy) characters.
    """
    if not isinstance(value, str) or not value:
        return "ID must not be null or empty."
    prefix, delimiter, body = value.partition("_")
    if not delimiter:
        return f"ID '{value}' has no '_' delimiter."
    if not prefix:
        return "ID has an empty prefix."
    if len(body) not in (_BODY_LENGTH, _LEGACY_BODY_LENGTH):
        return (
            f"ID '{value}' has unexpected body length {len(body)} "
            f"(expected {_BODY_LENGTH} or {_LEGACY_BODY_LENGTH})."
        )
    return None


def resolve_response_id(
    header_value: str | None,
    body_value: object,
    *,
    prefix: str,
    like: str | None = None,
) -> str:
    """Pick the response id: gateway header, then request body, then mint one.

    Mirrors the agentserver request parser: a non-blank ``x-agent-response-id``
    wins, then a non-blank string ``response_id`` from the body, then a newly
    minted id partitioned like ``like`` (the previous response or
    conversation). A supplied id that is not a Foundry id raises
    :class:`InvalidResponseIdError`.
    """
    candidate = header_value.strip() if isinstance(header_value, str) else ""
    if not candidate and isinstance(body_value, str):
        candidate = body_value.strip()
    if not candidate:
        return platform_id(prefix, like)
    problem = validate_id(candidate)
    if problem is not None:
        raise InvalidResponseIdError(f"Malformed identifier '{candidate}': {problem}")
    return candidate


def item_id(item: Mapping, like: str | None) -> str | None:
    """A typed item id co-located with ``like``; ``None`` for references/unknown types."""
    item_type = item.get("type")
    if item_type is None and ("role" in item or "content" in item):
        item_type = "message"
    item_prefix = _ITEM_PREFIXES.get(str(item_type or ""))
    return platform_id(item_prefix, like) if item_prefix else None

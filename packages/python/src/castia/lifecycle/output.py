"""Stable, versioned JSON output for Castia lifecycle commands.

Every ``--json`` command prints exactly one envelope on stdout::

    {
      "schema": "castia.cli.v1",
      "command": "optimize status",
      "ok": true,
      "status": "running",
      "effects": {"network": true, "billable": false, "mutating": false},
      "result": {...},
      "diagnostics": [{"severity": "warning", "code": null, "message": "..."}],
      "next": ["python -m castia optimize status --watch"]
    }

``result`` is command-specific; every other key is shared. Values are passed
through :func:`redact_secrets` so keys, tokens, and SAS signatures never reach
stdout. The same shape is pinned for every SDK by ``spec/lifecycle/output.tsp``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

CLI_SCHEMA = "castia.cli.v1"
SEVERITIES = ("error", "warning", "info")
EFFECT_KEYS = ("network", "billable", "mutating")
NOT_OK_STATUSES = frozenset({"error", "failed", "problems", "cancelled", "canceled"})
REDACTED = "[REDACTED]"

_SECRET_KEY_EXACT = frozenset({
    "token", "authorization", "sig", "signature", "credential", "credentials", "sas",
})
_SECRET_KEY_SUFFIXES = (
    "secret", "password", "apikey", "accesskey", "accountkey", "accesstoken",
    "refreshtoken", "idtoken", "bearertoken", "connectionstring", "sastoken",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)([?&]sig=)[^&\s\"']+"),
    re.compile(r"(?i)(AccountKey=)[^;\s\"']+"),
    re.compile(r"(?i)(SharedAccessSignature=)[^;\s\"']+"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


def _normalized_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def is_secret_key(key: str) -> bool:
    """True when a mapping key names a credential-bearing value."""
    normalized = _normalized_key(key)
    return normalized in _SECRET_KEY_EXACT or normalized.endswith(_SECRET_KEY_SUFFIXES)


def redact_secrets(value: Any) -> Any:
    """Return a JSON-safe copy of ``value`` with secrets replaced by ``[REDACTED]``."""
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if is_secret_key(str(k)) and v is not None else redact_secrets(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(v) for v in value]
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            value = pattern.sub(lambda m: m.group(1) + REDACTED, value)
        return value
    return value


def diagnostic(severity: str, message: str, code: str | None = None) -> dict[str, Any]:
    """One categorized diagnostic for an envelope."""
    if severity not in SEVERITIES:
        raise ValueError(f"diagnostic severity must be one of {', '.join(SEVERITIES)}")
    return {"severity": severity, "code": code, "message": str(message)}


def _normalize_diagnostics(diagnostics: Iterable[Any]) -> list[dict[str, Any]]:
    normalized = []
    for item in diagnostics or ():
        if isinstance(item, str):
            normalized.append(diagnostic("error", item))
        elif isinstance(item, Mapping):
            code = item.get("code")
            normalized.append(
                diagnostic(str(item.get("severity") or "error"), str(item.get("message") or ""),
                           None if code is None else str(code))
            )
        else:
            raise TypeError("diagnostics must be strings or objects")
    return normalized


def _normalize_effects(effects: Mapping[str, Any] | None) -> dict[str, bool]:
    effects = dict(effects or {})
    unknown = sorted(set(effects) - set(EFFECT_KEYS))
    if unknown:
        raise ValueError(f"unknown effect keys: {', '.join(unknown)}")
    return {key: bool(effects.get(key, False)) for key in EFFECT_KEYS}


def command_envelope(
    command: str,
    *,
    status: str,
    result: Any = None,
    diagnostics: Iterable[Any] = (),
    next_actions: Iterable[str] = (),
    effects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the versioned ``castia.cli.v1`` envelope.

    ``ok`` is derived, not passed: it is false when any diagnostic is an error or
    the status is one of ``error``, ``failed``, ``problems``, or ``cancelled``.
    String diagnostics are treated as errors.
    """
    if not command:
        raise ValueError("command is required")
    if not status:
        raise ValueError("status is required")
    normalized = _normalize_diagnostics(diagnostics)
    ok = status.lower() not in NOT_OK_STATUSES and not any(
        d["severity"] == "error" for d in normalized
    )
    return {
        "schema": CLI_SCHEMA,
        "command": command,
        "ok": ok,
        "status": status,
        "effects": _normalize_effects(effects),
        "result": redact_secrets(result),
        "diagnostics": redact_secrets(normalized),
        "next": [redact_secrets(str(n)) for n in next_actions],
    }


def error_envelope(
    command: str,
    exc: BaseException | str,
    *,
    code: str | None = None,
    effects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Represent a failed command structurally (``status: error``)."""
    if isinstance(exc, BaseException):
        code = code or type(exc).__name__
        message = str(exc) or type(exc).__name__
    else:
        message = exc
    return command_envelope(
        command, status="error", diagnostics=[diagnostic("error", message, code)],
        effects=effects,
    )


def emit_envelope(envelope: Mapping[str, Any], stream: TextIO | None = None) -> None:
    """Print one envelope as indented JSON."""
    print(json.dumps(envelope, indent=2, allow_nan=False, default=str), file=stream or sys.stdout)


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--json`` flag to a command parser.

    ``default=SUPPRESS`` lets a parent and its subcommands both accept the
    flag without the subparser's default overwriting the parent's value.
    """
    parser.add_argument(
        "--json", dest="json", action="store_true", default=argparse.SUPPRESS,
        help=f"print one {CLI_SCHEMA} JSON envelope on stdout",
    )


def wants_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


__all__ = [
    "CLI_SCHEMA", "EFFECT_KEYS", "NOT_OK_STATUSES", "REDACTED", "SEVERITIES",
    "add_json_argument", "command_envelope", "diagnostic", "emit_envelope",
    "error_envelope", "is_secret_key", "redact_secrets", "wants_json",
]

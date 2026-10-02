"""Turn-scoped token usage, aggregated across every model call in a turn.

The hosting server opens a :func:`usage_scope` around each Responses turn;
model paths (``Model`` and the Prompty client proxy) call
:func:`record_response_usage` after each Responses API call, so the protocol
body can report the turn's real token usage instead of zeros. Recording is
best effort and never raises.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class TurnUsage:
    """Mutable token totals for one turn (shared by child tasks)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        input_tokens = _int(_get(usage, "input_tokens"))
        output_tokens = _int(_get(usage, "output_tokens"))
        total = _get(usage, "total_tokens")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += _int(total) if total is not None else input_tokens + output_tokens
        self.cached_tokens += _int(_get(_get(usage, "input_tokens_details"), "cached_tokens"))
        self.reasoning_tokens += _int(_get(_get(usage, "output_tokens_details"), "reasoning_tokens"))
        self.calls += 1

    def to_responses_usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "input_tokens_details": {"cached_tokens": self.cached_tokens, "cache_write_tokens": 0},
            "output_tokens": self.output_tokens,
            "output_tokens_details": {"reasoning_tokens": self.reasoning_tokens},
            "total_tokens": self.total_tokens,
        }


_CURRENT_USAGE: ContextVar[TurnUsage | None] = ContextVar("castia_turn_usage", default=None)


@contextmanager
def usage_scope() -> Iterator[TurnUsage]:
    """Collect usage recorded by model calls made inside this block."""
    usage = TurnUsage()
    token = _CURRENT_USAGE.set(usage)
    try:
        yield usage
    finally:
        try:
            _CURRENT_USAGE.reset(token)
        except ValueError:
            # Async generators (SSE) may be finalized in another context.
            _CURRENT_USAGE.set(None)


def current_usage() -> TurnUsage | None:
    return _CURRENT_USAGE.get()


def record_response_usage(response: Any) -> None:
    """Add a Responses API result's ``usage`` to the current turn, if any."""
    try:
        usage = _CURRENT_USAGE.get()
        if usage is None:
            return
        raw = _get(response, "usage")
        if raw is None:
            return
        usage.add(raw)
    except Exception:  # noqa: BLE001 - usage accounting must never break a turn
        return


def _get(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

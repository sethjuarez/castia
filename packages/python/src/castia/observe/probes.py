"""Telemetry-to-suite adapters; no implicit live query or probe submission."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from .records import summarize
from .suite import ProbeResult
from .telemetry import AppInsightsClient, TraceQuery, verify_probe


def telemetry_probes(
    client: AppInsightsClient, query: TraceQuery, *,
    timeout_seconds: float = 60, poll_seconds: float = 5, max_attempts: int = 12,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Callable[[], ProbeResult]]:
    """Return query/ingestion probes using the exact caller-supplied scope.

    ``telemetry.query`` proves query access, even with zero rows; it does not
    prove ingestion. ``telemetry.ingestion`` requires ``query.probe_tag`` and an
    already submitted tagged probe. No model call is silently added here.
    Report evidence always excludes content even if the query opted into it.
    Prerequisites for ``run_suite`` are ``app_insights`` and ``probe_tag``.
    """
    safe_query = replace(query, include_content=False)

    def read() -> ProbeResult:
        rows = client.query(safe_query)
        return ProbeResult(
            "pass", "Scoped telemetry query succeeded; row presence is not ingestion proof.",
            evidence={"summary": summarize(rows)},
        )

    def ingestion() -> ProbeResult:
        if not safe_query.probe_tag:
            return ProbeResult("blocked", "An exact tagged probe is required.", "prerequisite")
        result = verify_probe(
            lambda: client.query(safe_query), probe_tag=safe_query.probe_tag,
            timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
            max_attempts=max_attempts, clock=clock, sleep=sleep,
        )
        return ProbeResult(
            result.status, result.diagnostic,
            None if result.status == "pass" else "ingestion_delay",
            result.to_dict(),
        )

    return {"telemetry.query": read, "telemetry.ingestion": ingestion}

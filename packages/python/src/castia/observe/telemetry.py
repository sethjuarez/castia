"""Bounded App Insights queries, safe REST diagnostics, and ingestion verification."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import quote

from .records import ExecutionRecord, normalize_record


class ObserveError(RuntimeError):
    """A safe diagnostic whose category survives transport/credential failures."""

    def __init__(self, category: str, message: str, *, status_code: int | None = None):
        self.category = category
        self.status_code = status_code
        super().__init__(message)


def classify_exception(exc: Exception) -> ObserveError:
    """Never copy exception text: SDK errors can embed bearer tokens or content."""
    if isinstance(exc, ObserveError):
        return exc
    import httpx

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ObserveError("timeout", "The operation exceeded its time limit.")
    if isinstance(exc, httpx.HTTPStatusError):
        return http_error(exc.response.status_code)
    if isinstance(exc, httpx.RequestError):
        return ObserveError("network", "The service could not be reached.")
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ObserveError("invalid_response", "The service response did not match its contract.")
    if isinstance(exc, ImportError):
        return ObserveError("dependency", "A required optional SDK is unavailable.")
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return http_error(status)
    if type(exc).__name__ in {"CredentialUnavailableError", "ClientAuthenticationError"}:
        return ObserveError("authentication", "Azure credential acquisition failed.")
    return ObserveError("unexpected", "The probe raised an unexpected exception.")


def http_error(status: int) -> ObserveError:
    category = {
        401: "authentication", 403: "authorization", 404: "not_found",
        408: "timeout", 429: "throttled",
    }.get(status, "service" if status >= 500 else "request")
    return ObserveError(category, f"Service returned HTTP {status}.", status_code=status)


def positive(value: Any, name: str, *, integer: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or (integer and not isinstance(value, int))
    ):
        raise ValueError(f"{name} must be a positive {'integer' if integer else 'finite number'}")


@dataclass(frozen=True)
class TraceQuery:
    agent_name: str
    start: datetime
    end: datetime
    limit: int = 100
    agent_version: str | None = None
    probe_tag: str | None = None
    include_content: bool = False
    anchor: str = "requests"
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.agent_name, str) or not self.agent_name.strip():
            raise ValueError("agent_name is required")
        if any(not isinstance(t, datetime) or t.tzinfo is None for t in (self.start, self.end)):
            raise ValueError("start and end must be timezone-aware datetimes")
        if self.start >= self.end:
            raise ValueError("start must precede end")
        positive(self.limit, "limit", integer=True)
        if self.limit > 10000:
            raise ValueError("limit must not exceed 10000")
        if not isinstance(self.include_content, bool):
            raise TypeError("include_content must be a boolean")
        if self.anchor not in {"requests", "invoke_agent"}:
            raise ValueError("anchor must be requests or invoke_agent")
        for name in ("agent_version", "probe_tag", "trace_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonempty when supplied")

    def to_kql(self) -> str:
        """Select hosted requests, then correlate child spans by operation ID.

        Downstream GenAI spans often use a class name as ``gen_ai.agent.name``.
        Filtering every table by that attribute silently loses model/tool usage.
        Request identity is therefore propagated into the normalized projection.
        Explicit ``anchor="invoke_agent"`` instead selects named agent invocation
        spans for local, non-HTTP delivery probes; it does not prove hosted routing.
        """
        literal = lambda value: json.dumps(value, ensure_ascii=True)
        start = self.start.astimezone(UTC).isoformat()
        end = self.end.astimezone(UTC).isoformat()
        source = (
            "requests" if self.anchor == "requests"
            else "union isfuzzy=true dependencies, customEvents"
        )
        lines = [
            f"let scoped_operations = {source}",
            f"| where timestamp >= datetime({start}) and timestamp < datetime({end})",
        ]
        if self.anchor == "invoke_agent":
            lines.append('| where tostring(customDimensions["gen_ai.operation.name"]) == "invoke_agent"')
        if self.trace_id:
            lines.append(f"| where operation_Id == {literal(self.trace_id)}")
        lines.extend([
            ('| extend agent_name = tostring(customDimensions["gen_ai.agent.name"]), '
            'hosted_agent_name = tostring(customDimensions["azure.ai.agentserver.agent_name"]), '
            'agent_version = coalesce(tostring(customDimensions["gen_ai.agent.version"]), '
            'tostring(customDimensions["azure.ai.agentserver.agent_version"]), '
            'tostring(customDimensions["service.version"])), '
            'agent_id = tostring(customDimensions["gen_ai.agent.id"]), '
            'probe_tag = coalesce(tostring(customDimensions["castia.probe_tag"]), '
            'tostring(customDimensions["castia.probe_id"]), tostring(customDimensions["probe_id"]))'),
            (f"| where agent_name == {literal(self.agent_name)} or "
             f"hosted_agent_name == {literal(self.agent_name)} or "
             f"agent_id startswith {literal(self.agent_name + ':')}"),
        ])
        if self.agent_version:
            lines.append(
                f"| where agent_version == {literal(self.agent_version)} or "
                f"agent_id == {literal(self.agent_name + ':' + self.agent_version)}"
            )
        lines.extend([
            "| where isnotempty(operation_Id)",
            "| summarize arg_max(timestamp, agent_version, probe_tag) by operation_Id",
            "| project operation_Id, root_agent_version=agent_version, root_probe_tag=probe_tag;",
            "union isfuzzy=true withsource=castia_table_name requests, dependencies, customEvents",
            f"| where timestamp >= datetime({start}) and timestamp < datetime({end})",
            "| join kind=inner (scoped_operations) on operation_Id",
            (f"| extend agent_name={literal(self.agent_name)}, agent_version=root_agent_version, "
             'probe_tag=coalesce(tostring(customDimensions["castia.probe_tag"]), '
             'tostring(customDimensions["castia.probe_id"]), tostring(customDimensions["probe_id"]), root_probe_tag)'),
        ])
        if self.probe_tag:
            lines.append(f"| where probe_tag == {literal(self.probe_tag)}")
        keys = [
            "gen_ai.agent.name", "gen_ai.agent.version", "gen_ai.agent.id",
            "azure.ai.agentserver.agent_name", "azure.ai.agentserver.agent_version",
            "service.name", "service.version", "gen_ai.tool.name", "castia.probe_tag",
            "castia.probe_id", "probe_id", "gen_ai.operation.name",
            "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
            "gen_ai.usage.prompt_tokens", "gen_ai.usage.completion_tokens",
            "gen_ai.usage.total_tokens", "otel.status_code", "status.code", "error.type",
        ]
        if self.include_content:
            keys += [
                "gen_ai.input.messages", "gen_ai.output.messages",
                "gen_ai.prompt", "gen_ai.completion",
            ]
        packed = ", ".join(
            f"{literal(key)}, customDimensions[{literal(key)}]" for key in keys
        )
        lines.extend([
            (f"| project timestamp, source=castia_table_name, name, operation_Id, operation_ParentId, "
            "agent_name, agent_version, probe_tag, "
            'id=tostring(column_ifexists("id", "")), '
            'success=tostring(column_ifexists("success", "")), '
            'duration=todouble(column_ifexists("duration", real(null))), '
            f"customDimensions=bag_pack({packed}), "
            "customMeasurements=bag_pack("
            '"gen_ai.usage.input_tokens", customMeasurements["gen_ai.usage.input_tokens"], '
            '"gen_ai.usage.output_tokens", customMeasurements["gen_ai.usage.output_tokens"], '
            '"gen_ai.usage.total_tokens", customMeasurements["gen_ai.usage.total_tokens"])'),
            "| order by timestamp desc",
            f"| take {self.limit}",
        ])
        return "\n".join(lines)


class AppInsightsClient:
    """Use local Azure credentials; only this adapter performs telemetry I/O.

    Pass an httpx Client and a credential to test without Azure. Credential and
    HTTP clients created internally are closed by ``close``/the context manager.
    """

    def __init__(
        self, app_id: str, *, credential: Any = None, client: Any = None,
        timeout_seconds: float = 30,
    ):
        if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", app_id):
            raise ValueError("app_id must be an Application Insights application ID")
        positive(timeout_seconds, "timeout_seconds")
        self.app_id = app_id
        self.credential = credential
        self.client = client
        self.timeout_seconds = timeout_seconds
        self._owns_credential = credential is None
        self._owns_client = client is None

    def query(self, query: TraceQuery) -> list[ExecutionRecord]:
        import httpx

        try:
            if self.credential is None:
                from azure.identity import DefaultAzureCredential
                self.credential = DefaultAzureCredential()
            try:
                token = self.credential.get_token("https://api.applicationinsights.io/.default").token
            except Exception:  # noqa: BLE001 - credential diagnostics can include authentication secrets
                raise ObserveError("authentication", "Azure credential acquisition failed.") from None
            if self.client is None:
                self.client = httpx.Client(follow_redirects=False)
            response = self.client.post(
                f"https://api.applicationinsights.io/v1/apps/{quote(self.app_id, safe='')}/query",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query.to_kql()},
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise http_error(response.status_code)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ObserveError("invalid_response", "Query returned non-object JSON.")
            if payload.get("error"):
                raise ObserveError("partial_query", "Query returned an error or partial results.")
            tables = payload.get("tables")
            if not isinstance(tables, list) or not tables:
                raise ObserveError("invalid_response", "Query response is missing result tables.")
            table = tables[0]
            columns = [column["name"] for column in table["columns"]]
            rows = table["rows"]
            if not isinstance(rows, list) or any(
                not isinstance(row, list) or len(row) != len(columns) for row in rows
            ):
                raise ObserveError("invalid_response", "Query returned malformed rows.")
            return [
                normalize_record(dict(zip(columns, row)), include_content=query.include_content)
                for row in rows[:query.limit]
            ]
        except Exception as exc:  # noqa: BLE001 - every REST/SDK error is sanitized and classified
            raise classify_exception(exc) from None

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()
        if self._owns_credential and self.credential is not None:
            self.credential.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


@dataclass(frozen=True)
class VerificationResult:
    status: str
    probe_tag: str
    attempts: int
    matched_records: int
    elapsed_seconds: float
    diagnostic: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_probe(
    query_fn: Callable[[], Iterable[ExecutionRecord]], *, probe_tag: str,
    timeout_seconds: float = 60, poll_seconds: float = 5, max_attempts: int = 12,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> VerificationResult:
    """Poll a *scoped* query for an exact tagged trace; query failures propagate.

    The callable must itself have a bounded request timeout. No unrelated span
    is accepted as proof of ingestion. A match verifies visibility, not success
    of the agent turn or complete ingestion of all spans.
    """
    if not isinstance(probe_tag, str) or not probe_tag.strip():
        raise ValueError("probe_tag is required")
    positive(timeout_seconds, "timeout_seconds")
    positive(poll_seconds, "poll_seconds")
    positive(max_attempts, "max_attempts", integer=True)
    started = clock()
    attempts = 0
    while attempts < max_attempts and clock() - started < timeout_seconds:
        attempts += 1
        matches = sum(record.probe_tag == probe_tag for record in query_fn())
        elapsed = max(0., clock() - started)
        if matches and elapsed <= timeout_seconds:
            return VerificationResult("pass", probe_tag, attempts, matches, elapsed, "Tagged trace visible.")
        remaining = timeout_seconds - elapsed
        if remaining <= 0 or attempts >= max_attempts:
            break
        sleep(min(poll_seconds, remaining))
    return VerificationResult(
        "blocked", probe_tag, attempts, 0, max(0., clock() - started),
        "Tagged trace not observed within the ingestion window; not proof of agent failure.",
    )

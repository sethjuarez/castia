"""Small REST client for Foundry evals, plus run-naming helpers.

Foundry exposes evals and eval runs through the OpenAI-compatible surface on
the project endpoint (``{project}/openai/v1/evals/...``). Deletes there are
asynchronous: ``DELETE`` can answer ``{"deleted": false, "location": ...}``
and the resource disappears later. :meth:`FoundryEvalClient.delete_eval_run`
with ``wait=True`` polls that location until the resource is gone and raises
:class:`TimeoutError` otherwise, so callers don't need timed cleanup scripts.

``azure.core`` / ``azure.identity`` are imported lazily so ``import castia``
stays cheap.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

AUTH_SCOPE = "https://ai.azure.com/.default"
OPENAI_PREFIX = "/openai/v1"

#: Separator in templated run names: ``"<rung> · n<rows>"``.
RUN_NAME_SEP = " · "
_RUN_NAME_RE = re.compile(r"^.+ · n\d+( · r\d+)?$")


def run_name(rung: str, rows: int, *, repeat: int | None = None) -> str:
    """Return the templated eval run name ``"<rung> · n<rows>"``.

    Foundry runs can't be renamed after submit, so the name has to carry the
    comparison context up front: which ladder rung (baseline, candidate, ...)
    and how many rows were scored. ``repeat`` appends ``" · r<i>"`` for
    repeated runs of the same rung.
    """
    rung = rung.strip()
    if not rung:
        raise ValueError("rung must be a non-empty label")
    if RUN_NAME_SEP.strip() in rung:
        raise ValueError(f"rung {rung!r} must not contain {RUN_NAME_SEP.strip()!r}")
    if rows < 1:
        raise ValueError("rows must be >= 1")
    name = f"{rung}{RUN_NAME_SEP}n{rows}"
    if repeat is not None:
        if repeat < 1:
            raise ValueError("repeat must be >= 1")
        name += f"{RUN_NAME_SEP}r{repeat}"
    return name


def is_templated_run_name(name: str) -> bool:
    """True when ``name`` follows ``"<rung> · n<rows>[ · r<i>]"``."""
    return bool(_RUN_NAME_RE.match(name))


def count_jsonl_rows(path: str | Path) -> int:
    """Count non-blank lines in a JSONL dataset."""
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


@dataclass
class _Reply:
    status: int
    data: dict[str, Any]
    headers: dict[str, str]


class FoundryEvalClient:
    """REST client for Foundry evals on a project endpoint.

    ``sleep`` and ``clock`` are injectable so polling is testable without real
    waits.
    """

    def __init__(
        self,
        project_endpoint: str,
        *,
        credential: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from azure.core import PipelineClient
        from azure.core.pipeline.policies import (
            BearerTokenCredentialPolicy,
            RetryPolicy,
        )

        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        self.project_endpoint = project_endpoint.rstrip("/")
        self._sleep = sleep
        self._clock = clock
        self._client = PipelineClient(
            base_url=self.project_endpoint,
            policies=[
                BearerTokenCredentialPolicy(credential, AUTH_SCOPE),
                RetryPolicy(),
            ],
        )

    # -- deletes -----------------------------------------------------------

    def delete_eval_run(
        self,
        eval_id: str,
        run_id: str,
        *,
        wait: bool = False,
        timeout: float = 600.0,
        interval: float = 10.0,
    ) -> dict[str, Any]:
        """Delete one eval run; with ``wait`` block until it is really gone."""
        path = f"{OPENAI_PREFIX}/evals/{quote(eval_id, safe='')}/runs/{quote(run_id, safe='')}"
        return self._delete(path, wait=wait, timeout=timeout, interval=interval)

    def delete_eval(
        self,
        eval_id: str,
        *,
        wait: bool = False,
        timeout: float = 600.0,
        interval: float = 10.0,
    ) -> dict[str, Any]:
        """Delete an eval (and its runs); with ``wait`` block until gone."""
        path = f"{OPENAI_PREFIX}/evals/{quote(eval_id, safe='')}"
        return self._delete(path, wait=wait, timeout=timeout, interval=interval)

    def _delete(
        self, path: str, *, wait: bool, timeout: float, interval: float
    ) -> dict[str, Any]:
        reply = self._send("DELETE", path, ok_statuses=(404,))
        if reply.status == 404 or reply.data.get("deleted") is True or not wait:
            return reply.data
        location = (
            reply.data.get("location")
            or reply.headers.get("location")
            or reply.headers.get("operation-location")
            or path
        )
        deadline = self._clock() + timeout
        while True:
            if self._clock() >= deadline:
                raise TimeoutError(
                    f"delete of {path} not confirmed within {timeout:g}s "
                    f"(last polled {location})"
                )
            self._sleep(interval)
            poll = self._send("GET", location, ok_statuses=(404,))
            if poll.status == 404 or poll.data.get("deleted") is True:
                return {**reply.data, "deleted": True}
            status = str(poll.data.get("status") or "").lower()
            if status in {"succeeded", "deleted", "completed"} and location != path:
                return {**reply.data, "deleted": True}
            if status in {"failed", "canceled", "cancelled"}:
                raise RuntimeError(f"delete of {path} ended with status {status!r}: {poll.data}")

    # -- transport ---------------------------------------------------------

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        if not path_or_url.startswith("/"):
            path_or_url = "/" + path_or_url
        return f"{self.project_endpoint}{path_or_url}"

    def _send(
        self,
        method: str,
        path_or_url: str,
        *,
        json_body: dict[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (),
    ) -> _Reply:
        from azure.core.rest import HttpRequest

        url = self._url(path_or_url)
        request = HttpRequest(method, url)
        if json_body is not None:
            request.headers["Content-Type"] = "application/json"
            request.set_json_body(json_body)
        response = self._client.send_request(request)
        status = int(getattr(response, "status_code", 200))
        headers = {k.lower(): v for k, v in dict(getattr(response, "headers", {}) or {}).items()}
        if status in ok_statuses:
            return _Reply(status, _json_or_empty(response), headers)
        if status >= 400:
            raise RuntimeError(f"{method} {url} failed ({status}): {_text(response)}")
        return _Reply(status, _json_or_empty(response), headers)


def _text(response) -> str:
    text = getattr(response, "text", None)
    if callable(text):
        try:
            return text()
        except (ValueError, UnicodeDecodeError):  # pragma: no cover - defensive
            return ""
    return text if isinstance(text, str) else ""


def _json_or_empty(response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

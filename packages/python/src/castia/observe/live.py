"""Reusable, explicitly configured live probes. Importing this module does no I/O.

All URLs (including hosted protocol paths and model API versions) come from
discovery/configuration, never guessed from an agent name. Output-token limits
are service request limits, not a dollar cap or an input-token accounting claim.
There is deliberately no fine-tuning submission, candidate apply, or deployment.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import quote, unquote, urlsplit

from castia.observe.suite import ProbeResult
from castia.observe.telemetry import (
    ObserveError,
    classify_exception,
    http_error,
    positive,
)

AI_SCOPE = "https://ai.azure.com/.default"
OPTIMIZER_HEADERS = {"Foundry-Features": "AgentsOptimization=V2Preview"}
OPTIMIZER_STATUSES = frozenset({
    "notstarted", "not_started", "queued", "pending", "running", "inprogress",
    "in_progress", "completed", "succeeded", "failed", "cancelled", "canceled",
    "cancelling", "canceling",
})


def _no_finetuning(url: str) -> None:
    path = unquote(urlsplit(url).path).lower().replace("-", "").replace("_", "")
    if "/finetuning/jobs" in path or "/finetune/jobs" in path:
        raise ValueError("Observation probes never submit or mutate fine-tuning jobs")


def _optimizer_status(payload: Any, *, expected_id: str | None = None) -> str:
    if not isinstance(payload, Mapping):
        raise ObserveError("invalid_response", "Optimizer status must be an object.")
    status = payload.get("status")
    if not isinstance(status, str) or status.lower() not in OPTIMIZER_STATUSES:
        raise ObserveError("invalid_response", "Optimizer status is missing or unrecognized.")
    for key in ("operation_id", "operationId", "id", "job_id", "jobId"):
        if key in payload and (
            not isinstance(payload[key], str) or not payload[key]
            or (expected_id is not None and payload[key] != expected_id)
        ):
            raise ObserveError("invalid_response", "Optimizer returned an invalid or mismatched job identity.")
    result = payload.get("result")
    if result is not None:
        if not isinstance(result, Mapping):
            raise ObserveError("invalid_response", "Optimizer result must be an object or null.")
        for key in ("best", "best_candidate_id", "bestCandidateId"):
            if key in result and result[key] is not None and not isinstance(result[key], str):
                raise ObserveError("invalid_response", "Optimizer best-candidate identity must be text.")
        if "candidates" in result and (
            not isinstance(result["candidates"], list)
            or any(not isinstance(candidate, Mapping) for candidate in result["candidates"])
        ):
            raise ObserveError("invalid_response", "Optimizer candidates must be an object list.")
    return status.lower()


def _candidate_config(payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise ObserveError("invalid_response", "Optimizer candidate config must be an object.")
    fields = {"model", "instructions", "system_prompt", "tools", "skills", "temperature"}
    if not any(key in payload and payload[key] is not None for key in fields):
        raise ObserveError("invalid_response", "Optimizer candidate config has no recognized configuration fields.")
    for key in ("model", "instructions", "system_prompt"):
        if key in payload and payload[key] is not None and (
            not isinstance(payload[key], str) or (key == "model" and not payload[key].strip())
        ):
            raise ObserveError("invalid_response", "Optimizer model/prompt fields must contain valid text.")
    for key in ("tools", "skills"):
        if key in payload and payload[key] is not None and (
            not isinstance(payload[key], list)
            or any(not isinstance(item, Mapping) for item in payload[key])
        ):
            raise ObserveError("invalid_response", "Optimizer tools/skills must be object lists.")
    if payload.get("temperature") is not None:
        value = payload["temperature"]
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 2
        ):
            raise ObserveError("invalid_response", "Optimizer temperature must be a finite number from 0 to 2.")


@dataclass(frozen=True)
class Limits:
    max_requests: int = 20
    max_seconds: float = 120
    max_output_tokens: int = 128
    max_total_output_tokens: int = 1024
    max_response_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        for name in ("max_requests", "max_output_tokens", "max_total_output_tokens", "max_response_bytes"):
            positive(getattr(self, name), name, integer=True)
        positive(self.max_seconds, "max_seconds")
        if self.max_output_tokens > self.max_total_output_tokens:
            raise ValueError("max_output_tokens cannot exceed max_total_output_tokens")


@dataclass(frozen=True)
class LiveConfig:
    project_endpoint: str | None = None
    project_read_url: str | None = None
    project_read_scope: str = AI_SCOPE
    model_url: str | None = None
    model: str | None = None
    hosted_responses_url: str | None = None
    hosted_responses_body: Mapping[str, Any] | None = None
    hosted_responses_expected_text: str | None = None
    hosted_invocations_url: str | None = None
    hosted_invocations_body: Mapping[str, Any] | None = None
    hosted_invocations_output_field: str | None = None
    hosted_scope: str = AI_SCOPE
    toolbox_url: str | None = None
    toolbox_tools: tuple[str, ...] = ()
    toolbox_prompt: str | None = None
    optimizer_job_id: str | None = None
    optimizer_candidate_id: str | None = None
    optimizer_request: Mapping[str, Any] | None = None
    allow_optimizer_submit: bool = False
    optimizer_timeout_seconds: float = 60
    optimizer_poll_seconds: float = 5
    cleanup_timeout_seconds: float = 5
    probe_tag: str | None = None
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self) -> None:
        for name in (
            "project_endpoint", "project_read_url", "model_url",
            "hosted_responses_url", "hosted_invocations_url", "toolbox_url",
        ):
            value = getattr(self, name)
            if value is not None:
                _no_finetuning(value)
                url = urlsplit(value)
                if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
                    raise ValueError(f"{name} must be an absolute HTTPS URL without credentials/fragments")
                if name == "project_endpoint" and url.query:
                    raise ValueError("project_endpoint must not include a query")
        if not isinstance(self.limits, Limits):
            raise TypeError("limits must be Limits")
        if not isinstance(self.allow_optimizer_submit, bool):
            raise TypeError("allow_optimizer_submit must be a boolean")
        for name in ("optimizer_timeout_seconds", "optimizer_poll_seconds", "cleanup_timeout_seconds"):
            positive(getattr(self, name), name)
        if not isinstance(self.toolbox_tools, (list, tuple)) or any(
            not isinstance(tool, str) or not tool.strip() for tool in self.toolbox_tools
        ):
            raise ValueError("toolbox_tools must contain explicit known tool names")
        if len(set(self.toolbox_tools)) != len(self.toolbox_tools):
            raise ValueError("toolbox_tools must not contain duplicates")
        object.__setattr__(self, "toolbox_tools", tuple(self.toolbox_tools))
        for name in ("project_read_scope", "hosted_scope"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.optimizer_request is not None and not isinstance(self.optimizer_request, Mapping):
            raise ValueError("optimizer_request must be a request mapping")
        if self.hosted_responses_body is not None and not isinstance(self.hosted_responses_body, Mapping):
            raise TypeError("hosted_responses_body must be an object")
        if self.hosted_responses_expected_text is not None and not isinstance(
            self.hosted_responses_expected_text, str
        ):
            raise TypeError("hosted_responses_expected_text must be text")
        if self.allow_optimizer_submit:
            if not self.optimizer_request or not self.project_endpoint:
                raise ValueError("optimizer submission requires project_endpoint and optimizer_request")
            options = self.optimizer_request.get("options", {})
            maximum = options.get("max_candidates") if isinstance(options, Mapping) else None
            positive(maximum, "optimizer options.max_candidates", integer=True)
            if maximum > 3:
                raise ValueError("observe optimizer probes support at most 3 candidates")
            if self.limits.max_requests < 3 or self.limits.max_seconds <= self.cleanup_timeout_seconds:
                raise ValueError("optimizer submission needs at least 3 requests and reserved cleanup time")

    def prerequisites(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in (
                "project_endpoint", "project_read_url", "model_url", "model",
                "hosted_responses_url", "hosted_invocations_url",
                "hosted_invocations_body", "hosted_invocations_output_field", "toolbox_url",
                "toolbox_tools", "toolbox_prompt", "optimizer_job_id", "optimizer_candidate_id",
                "optimizer_request", "allow_optimizer_submit", "probe_tag",
            )
        }


class RestAdapter:
    """httpx + lazy DefaultAzureCredential, no retries or redirect token forwarding.

    ``request`` is the sole adapter seam. Inject an object with this method into
    ``live_probes`` for tests; the outer runner supplies budgets even when injected.
    """

    def __init__(self, *, credential: Any = None, client: Any = None):
        self.credential = credential
        self.client = client
        self._owns_credential = credential is None
        self._owns_client = client is None

    def token(self, scope: str) -> str:
        try:
            if self.credential is None:
                from azure.identity import DefaultAzureCredential
                self.credential = DefaultAzureCredential()
            return self.credential.get_token(scope).token
        except Exception:  # noqa: BLE001 - credentials may embed secrets in arbitrary SDK failures
            raise ObserveError("authentication", "Azure credential acquisition failed.") from None

    def request(
        self, method: str, url: str, *, scope: str, json_body: Mapping[str, Any] | None,
        headers: Mapping[str, str], timeout_seconds: float,
        max_response_bytes: int, stream: bool = False,
    ) -> Any:
        import httpx

        positive(timeout_seconds, "timeout_seconds")
        positive(max_response_bytes, "max_response_bytes", integer=True)
        try:
            if method.upper() != "GET":
                _no_finetuning(url)
            started = time.monotonic()
            token = self.token(scope)
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise ObserveError("timeout", "Credential acquisition exceeded the request deadline.")
            if self.client is None:
                self.client = httpx.Client(follow_redirects=False)
            with self.client.stream(
                method, url, headers={**headers, "Authorization": f"Bearer {token}"},
                json=json_body, timeout=remaining, follow_redirects=False,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise http_error(response.status_code)
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > max_response_bytes:
                        raise ObserveError("budget", "Response exceeds the configured byte limit.")
                    if time.monotonic() - started >= timeout_seconds:
                        raise ObserveError("timeout", "Response exceeded its wall-clock deadline.")
                if response.status_code == 204:
                    return {}
                text = chunks.decode("utf-8")
                if not stream:
                    result = json.loads(text)
                    if not isinstance(result, dict):
                        raise ObserveError("invalid_response", "Expected an object response.")
                    return result
                events = []
                for block in text.replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                    if data and data != "[DONE]":
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            raise ObserveError("invalid_response", "Expected an object SSE event.")
                        events.append(event)
                return events
        except Exception as exc:  # noqa: BLE001 - every adapter failure becomes a safe categorized error
            raise classify_exception(exc) from None

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()
        if self._owns_credential and self.credential is not None:
            self.credential.close()


class _Budget:
    def __init__(self, limits: Limits, clock: Callable[[], float]):
        self.limits, self.clock = limits, clock
        self.started: float | None = None
        self.requests = 0
        self.reserved_output_tokens = 0
        self.cleanup_reserved = False
        self.cleanup_seconds = 0.

    def remaining(self, *, cleanup: bool = False) -> float:
        if self.started is None:
            self.started = self.clock()
        return self.limits.max_seconds - (self.clock() - self.started) - (
            self.cleanup_seconds if self.cleanup_reserved and not cleanup else 0.
        )

    def take(self, *, tokens: int = 0, cleanup: bool = False) -> float:
        remaining = self.remaining(cleanup=cleanup)
        available = self.limits.max_requests - self.requests - (
            1 if self.cleanup_reserved and not cleanup else 0
        )
        if available <= 0 or remaining <= 0:
            raise ObserveError("budget", "The shared request or time budget is exhausted.")
        if self.reserved_output_tokens + tokens > self.limits.max_total_output_tokens:
            raise ObserveError("budget", "The shared requested output-token budget is exhausted.")
        self.requests += 1
        self.reserved_output_tokens += tokens
        return remaining


def _response_text(data: Mapping[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    return "".join(
        item.get("text", "")
        for output in data.get("output", []) if isinstance(output, Mapping)
        for item in output.get("content", []) if isinstance(item, Mapping)
        if item.get("type") == "output_text" and isinstance(item.get("text"), str)
    )


def _usage(data: Mapping[str, Any]) -> dict[str, int | None]:
    usage = data.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    return {
        key: usage.get(key) if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)
        and usage[key] >= 0 else None
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


class LiveProbes(dict):
    """Probe mapping owning one shared budget; close after the suite."""

    def __init__(
        self, config: LiveConfig, adapter: Any, *, owns_adapter: bool,
        clock: Callable[[], float], sleep: Callable[[float], None],
    ):
        super().__init__()
        self.config, self.adapter = config, adapter
        self._owns_adapter = owns_adapter
        self.clock, self.sleep = clock, sleep
        self.budget = _Budget(config.limits, clock)
        self.update({
            "project.read": self.project_read,
            "model.respond": self.model_respond,
            "model.stream": self.model_stream,
            "model.function_call": self.model_function_call,
            "runtime.responses": self.hosted_responses,
            "runtime.invocations": self.hosted_invocations,
            "tools.toolbox": self.toolbox,
            "optimizer.status": self.optimizer_status,
            "optimizer.candidate": self.optimizer_candidate,
            "optimizer.run": self.optimizer_run,
        })

    def close(self) -> None:
        if self._owns_adapter:
            self.adapter.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(
        self, method: str, url: str | None, *, body: Mapping[str, Any] | None = None,
        scope: str = AI_SCOPE, headers: Mapping[str, str] | None = None,
        stream: bool = False, model: bool = False, cleanup: bool = False,
        timeout: float | None = None,
    ) -> Any:
        if not url:
            raise ObserveError("prerequisite", "A discovered service URL is required.")
        available = self.budget.take(
            tokens=self.config.limits.max_output_tokens if model else 0, cleanup=cleanup,
        )
        request_headers = dict(headers or {})
        if self.config.probe_tag:
            request_headers["x-castia-probe-tag"] = self.config.probe_tag
        result = self.adapter.request(
            method, url, scope=scope, json_body=body, headers=request_headers,
            timeout_seconds=min(available, timeout) if timeout else available,
            max_response_bytes=self.config.limits.max_response_bytes, stream=stream,
        )
        if self.budget.remaining(cleanup=cleanup) < 0:
            raise ObserveError("timeout", "The operation exceeded the shared wall-clock budget.")
        return result

    def _model(self, *, stream: bool = False, **kwargs: Any) -> Any:
        if not self.config.model:
            raise ObserveError("prerequisite", "A selected model deployment is required.")
        return self._request(
            "POST", self.config.model_url, model=True, stream=stream,
            body={
                "model": self.config.model,
                "max_output_tokens": self.config.limits.max_output_tokens,
                "stream": stream,
                **kwargs,
            },
        )

    def _text_result(self, response: Mapping[str, Any], *, expected: str | None = None) -> ProbeResult:
        if response.get("error") or response.get("status") in {"failed", "incomplete", "cancelled"}:
            return ProbeResult("fail", "Response did not complete successfully.", "service")
        text = _response_text(response)
        passed = bool(text.strip()) and (expected is None or text.strip() == expected)
        return ProbeResult(
            "pass" if passed else "fail",
            "Response text assertion passed." if passed else "Response text assertion failed.",
            None if passed else "assertion",
            {"usage": _usage(response), "output_characters": len(text), "cost": None},
        )

    def project_read(self) -> ProbeResult:
        data = self._request("GET", self.config.project_read_url, scope=self.config.project_read_scope)
        if not isinstance(data, Mapping) or not (data.get("id") or data.get("name")):
            return ProbeResult("fail", "Project read did not return project identity.", "invalid_response")
        return ProbeResult("pass", "Selected project is readable.")

    def model_respond(self) -> ProbeResult:
        return self._text_result(self._model(input="Reply with exactly CASTIA_OK."), expected="CASTIA_OK")

    def model_stream(self) -> ProbeResult:
        events = self._model(stream=True, input="Reply with exactly CASTIA_OK.")
        deltas = [
            event.get("delta", "") for event in events
            if event.get("type") == "response.output_text.delta"
        ]
        completed = [event for event in events if event.get("type") == "response.completed"]
        failed = any(event.get("type") in {"error", "response.failed", "response.incomplete"} for event in events)
        passed = bool(completed) and "".join(deltas).strip() == "CASTIA_OK" and not failed
        return ProbeResult(
            "pass" if passed else "fail",
            "Streaming completion and delta assertions passed." if passed else "Streaming completion/delta assertion failed.",
            None if passed else "assertion",
            {"delta_count": len(deltas), "completed_count": len(completed),
             "usage": _usage(completed[-1].get("response", {})) if completed else _usage({})},
        )

    def model_function_call(self) -> ProbeResult:
        tool = {
            "type": "function", "name": "castia_probe_add", "description": "Add two integers.",
            "strict": True,
            "parameters": {
                "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"], "additionalProperties": False,
            },
        }
        response = self._model(
            input="Call castia_probe_add with a=2 and b=3.",
            tools=[tool], tool_choice={"type": "function", "name": "castia_probe_add"},
        )
        if response.get("error") or response.get("status") in {"failed", "incomplete", "cancelled"}:
            return ProbeResult("fail", "Function-call response did not complete successfully.", "service")
        calls = [item for item in response.get("output", []) if item.get("type") == "function_call"]
        if len(calls) != 1 or calls[0].get("name") != "castia_probe_add" or not calls[0].get("call_id"):
            return ProbeResult("fail", "Expected exactly the selected function call.", "assertion")
        call = calls[0]
        arguments = json.loads(call.get("arguments", "{}"))
        if arguments != {"a": 2, "b": 3}:
            return ProbeResult("fail", "Function arguments failed the deterministic assertion.", "assertion")
        result = arguments["a"] + arguments["b"]
        final = self._model(input=[
            {"role": "user", "content": "Add 2 and 3 using the tool. Return only the resulting integer."},
            {"type": "function_call", "name": call["name"], "call_id": call["call_id"], "arguments": call["arguments"]},
            {"type": "function_call_output", "call_id": call["call_id"], "output": str(result)},
        ], tools=[tool], tool_choice="none")
        outcome = self._text_result(final, expected="5")
        return ProbeResult(
            outcome.status, outcome.diagnostic, outcome.category,
            {**outcome.evidence, "function_executed": True, "call_usage": _usage(response)},
        )

    def hosted_responses(self) -> ProbeResult:
        body = dict(self.config.hosted_responses_body or {"input": "Reply with exactly CASTIA_OK."})
        body["max_output_tokens"] = self.config.limits.max_output_tokens
        response = self._request(
            "POST", self.config.hosted_responses_url, scope=self.config.hosted_scope, model=True,
            body=body,
        )
        return self._text_result(response, expected=self.config.hosted_responses_expected_text)

    def hosted_invocations(self) -> ProbeResult:
        if not self.config.hosted_invocations_body or not self.config.hosted_invocations_output_field:
            raise ObserveError("prerequisite", "Invocations requires an explicit body and output field.")
        response = self._request(
            "POST", self.config.hosted_invocations_url, scope=self.config.hosted_scope,
            body=self.config.hosted_invocations_body,
        )
        value = response.get(self.config.hosted_invocations_output_field)
        passed = isinstance(value, str) and bool(value.strip()) and not response.get("error")
        return ProbeResult(
            "pass" if passed else "fail",
            "Invocations output field is populated." if passed else "Invocations output assertion failed.",
            None if passed else "assertion",
        )

    def toolbox(self) -> ProbeResult:
        if not self.config.toolbox_url or not self.config.toolbox_tools or not self.config.toolbox_prompt:
            raise ObserveError("prerequisite", "Toolbox requires explicit known tools, URL, and a safe test prompt.")
        # MCP authorization is required by the selected toolbox and is never reported.
        token = self.adapter.token(AI_SCOPE)
        from castia.integrations.toolbox import toolbox_mcp_tool
        spec = toolbox_mcp_tool(
            endpoint=self.config.toolbox_url, token=token,
            allowed_tools=self.config.toolbox_tools,
        )
        spec = {key: value for key, value in spec.items() if not key.startswith("x-castia-")}
        response = self._model(input=self.config.toolbox_prompt, tools=[spec])
        calls = [item for item in response.get("output", []) if item.get("type") == "mcp_call"]
        observed = {item.get("name") for item in calls}
        errors = sum(bool(item.get("error")) for item in calls)
        for item in calls:
            output = item.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    output = None
            if isinstance(output, Mapping) and (
                output.get("isError") is True or output.get("ok") is False
                or output.get("success") is False
            ):
                errors += 1
        passed = (
            set(self.config.toolbox_tools).issubset(observed)
            and observed.issubset(set(self.config.toolbox_tools))
            and not errors and not response.get("error")
            and response.get("status") not in {"failed", "incomplete", "cancelled"}
            and all(item.get("output") is not None for item in calls)
        )
        return ProbeResult(
            "pass" if passed else "fail",
            "Every selected toolbox tool produced output." if passed else "Selected toolbox execution was not established.",
            None if passed else "assertion",
            {"selected_tools": list(self.config.toolbox_tools), "call_count": len(calls),
             "tool_error_count": errors, "usage": _usage(response)},
        )

    def _optimizer_url(self, suffix: str = "") -> str:
        if not self.config.project_endpoint:
            raise ObserveError("prerequisite", "A selected project endpoint is required.")
        return self.config.project_endpoint.rstrip("/") + "/agent_optimization_jobs" + suffix + "?api-version=v1"

    def optimizer_status(self) -> ProbeResult:
        if not self.config.optimizer_job_id:
            raise ObserveError("prerequisite", "A selected optimizer job is required.")
        result = self._request(
            "GET", self._optimizer_url("/" + quote(self.config.optimizer_job_id, safe="")),
            headers=OPTIMIZER_HEADERS,
        )
        status = _optimizer_status(result, expected_id=self.config.optimizer_job_id)
        return ProbeResult("pass", "Selected optimizer job status is readable.",
                           evidence={"job_status": status})

    def optimizer_candidate(self) -> ProbeResult:
        if not self.config.optimizer_job_id or not self.config.optimizer_candidate_id:
            raise ObserveError("prerequisite", "Selected optimizer job and candidate IDs are required.")
        suffix = (
            "/" + quote(self.config.optimizer_job_id, safe="") + "/candidates/"
            + quote(self.config.optimizer_candidate_id, safe="") + "/config"
        )
        result = self._request("GET", self._optimizer_url(suffix), headers=OPTIMIZER_HEADERS)
        _candidate_config(result)
        return ProbeResult("pass", "Selected optimizer candidate config is readable.")

    def optimizer_run(self) -> ProbeResult:
        if not self.config.allow_optimizer_submit:
            raise ObserveError("prerequisite", "Optimizer submission requires explicit opt-in.")
        if self.budget.limits.max_requests - self.budget.requests < 3:
            raise ObserveError("budget", "Insufficient requests for submit, status, and cancellation.")
        self.budget.cleanup_reserved = True
        self.budget.cleanup_seconds = self.config.cleanup_timeout_seconds
        identifier = None
        terminal = False
        outcome = ProbeResult("fail", "Optimizer submission did not establish a job.", "invalid_response")
        deadline = self.clock() + min(
            self.config.optimizer_timeout_seconds, max(0., self.budget.remaining()),
        )
        try:
            result = self._request(
                "POST", self._optimizer_url(), body={"inputs": dict(self.config.optimizer_request or {})},
                headers=OPTIMIZER_HEADERS, timeout=max(.001, deadline - self.clock()),
            )
            identifier = next(
                (result[key] for key in ("operation_id", "operationId", "id", "job_id", "jobId")
                 if result.get(key)),
                None,
            )
            if not isinstance(identifier, str) or not identifier:
                return ProbeResult(
                    "fail", "Submission returned no job ID; remote work may exist and cannot be cancelled.",
                    "invalid_response", {
                        "cancellation": "unavailable_no_job_id", "cost": None,
                        "cleanup_pending": True, "remote_job_may_be_running": True,
                    },
                )
            while self.clock() < deadline:
                result = self._request(
                    "GET", self._optimizer_url("/" + quote(identifier, safe="")),
                    headers=OPTIMIZER_HEADERS, timeout=deadline - self.clock(),
                )
                status = _optimizer_status(result, expected_id=identifier)
                if status in {"completed", "succeeded", "failed", "cancelled", "canceled"}:
                    terminal = True
                    passed = status in {"completed", "succeeded"}
                    outcome = ProbeResult(
                        "pass" if passed else "fail", "Optimizer reached a terminal state.",
                        None if passed else "service",
                        {"job_id": identifier, "job_status": status, "cost": None,
                         "cost_status": "unknown", "applied": False, "deployed": False,
                         "cleanup_pending": False, "remote_job_may_be_running": False},
                    )
                    break
                remaining = deadline - self.clock()
                if remaining > 0:
                    self.sleep(min(self.config.optimizer_poll_seconds, remaining))
            if not terminal:
                outcome = ProbeResult("fail", "Optimizer polling deadline expired.", "timeout",
                                      {"job_id": identifier, "cost": None})
        except Exception as exc:  # noqa: BLE001 - preserve categorized failure and always attempt cleanup
            error = classify_exception(exc)
            outcome = ProbeResult(
                "blocked" if error.category in {"budget", "authentication", "authorization"} else "fail",
                str(error), error.category, {
                    "job_id": identifier, "cost": None,
                    "cleanup_pending": identifier is None,
                    "remote_job_may_be_running": identifier is None,
                },
            )
        finally:
            if identifier and not terminal:
                try:
                    cancellation = self._request(
                        "POST", self._optimizer_url("/" + quote(identifier, safe="") + ":cancel"),
                        headers=OPTIMIZER_HEADERS, cleanup=True,
                        timeout=self.config.cleanup_timeout_seconds,
                    )
                    if not isinstance(cancellation, Mapping) or cancellation.get("error"):
                        raise ObserveError("invalid_response", "Optimizer cancellation returned an error envelope.")
                    cancellation_status = (
                        _optimizer_status(cancellation, expected_id=identifier)
                        if "status" in cancellation else None
                    )
                    confirmed = cancellation_status in {
                        "completed", "succeeded", "failed", "cancelled", "canceled",
                    }
                    outcome = ProbeResult(
                        outcome.status, outcome.diagnostic, outcome.category,
                        {
                            **outcome.evidence,
                            "cancellation": "confirmed_terminal" if confirmed else "requested_not_confirmed",
                            "cancellation_status": cancellation_status,
                            "cleanup_pending": not confirmed,
                            "remote_job_may_be_running": not confirmed,
                        },
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup failures must be visible, never swallowed
                    error = classify_exception(exc)
                    outcome = ProbeResult(
                        "fail", "Optimizer cleanup failed; inspect the remote job before rerunning.",
                        "cleanup", {**outcome.evidence, "cancellation": "failed",
                                    "cleanup_category": error.category,
                                    "cleanup_pending": True, "remote_job_may_be_running": True},
                    )
            self.budget.cleanup_reserved = False
            self.budget.cleanup_seconds = 0.
        return outcome


def live_probes(
    config: LiveConfig, *, adapter: Any = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> LiveProbes:
    """Build reusable probes without network activity; use as a context manager."""
    return LiveProbes(
        config, adapter if adapter is not None else RestAdapter(),
        owns_adapter=adapter is None, clock=clock, sleep=sleep,
    )

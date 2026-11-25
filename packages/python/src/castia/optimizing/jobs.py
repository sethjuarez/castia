"""Castia-native Foundry Agent Optimizer client and config builder.

This module owns the optimizer request shape directly instead of shelling out to
``azd ai agent optimize``. ``azd`` remains the deployment rail; Castia owns the
canonical ``eval.yaml`` + ``.agent_configs`` interpretation so prompt/tool
optimization cannot drift through azd metadata rewrites.

The wire shape is the GA ``agent_optimization_jobs`` contract (``api-version=v1``,
no ``Foundry-Features`` preview header), matching ``azure-ai-projects`` 2.8+.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from azure.core import PipelineClient
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
from azure.core.rest import HttpRequest

from castia.optimizing.baseline import DEFAULT_CANDIDATE, DEFAULT_CONFIG_DIR

API_VERSION = "v1"
AUTH_SCOPE = "https://ai.azure.com/.default"
LAST_RUN_FILE = ".castia/last_optimize.json"

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "skipped"})
TARGET_ATTRIBUTES = frozenset({"instructions", "model", "skills", "tools"})
GROUND_TRUTH_ALIASES = ("ground_truth", "answer", "expected")
UNSUPPORTED_OPTIONS = {
    "max_stalls": "the GA optimizer has no stall budget; drop it",
    "evaluation_level": (
        "the GA optimizer evaluates conversations only; drop it and use "
        "conversation-level evaluators"
    ),
}
_OPTIMIZATION_CONFIG_KEYS = frozenset(
    {"model", "system_prompt", "skills", "tools", "target_attributes", "model_search_space"}
)
_TRACE_KEYS = frozenset({"resource_id", "agent_name", "window", "training_split_ratio"})


@dataclass(frozen=True)
class OptimizerState:
    """Local record of the latest optimizer job submitted through Castia."""

    job_id: str
    project_endpoint: str
    agent_name: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CandidateApplyPlan:
    """Files written when a candidate is materialized locally."""

    candidate_dir: Path
    metadata_path: Path
    instructions_path: Path | None
    tools_path: Path | None
    skills_dir: Path | None


class CandidateResolutionError(RuntimeError):
    """A candidate's configuration could not be reconstructed exactly."""


def load_eval_config(path: str | os.PathLike = "eval.yaml") -> dict[str, Any]:
    """Load ``eval.yaml`` as a plain dictionary."""

    yaml = _yaml()
    data = yaml.load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected a YAML mapping")
    return data


def build_optimizer_request(
    *,
    eval_config: dict[str, Any],
    root: str | os.PathLike = ".",
    agent_name: str | None = None,
    agent_version: str | None = None,
    eval_model: str | None = None,
    optimize_model: str | None = None,
    max_candidates: int | None = None,
    config_dir: str | os.PathLike = DEFAULT_CONFIG_DIR,
    candidate: str = DEFAULT_CANDIDATE,
) -> dict[str, Any]:
    """Build a GA ``agent_optimization_jobs`` request from Castia's canonical files.

    ``root`` is the directory containing ``eval.yaml``. Local dataset paths are
    resolved relative to it; baseline prompt/tools paths resolve through
    ``agent.config`` when present, otherwise ``config_dir/candidate``.

    A top-level ``traces:`` block switches the job to skill optimization over
    Application Insights agent traces instead of a target-completion dataset.
    """

    base = Path(root)
    agent = dict(eval_config.get("agent") or {})
    options = dict(eval_config.get("options") or {})
    opt_config = dict(options.get("optimization_config") or {})

    for key, reason in UNSUPPORTED_OPTIONS.items():
        if key in options:
            raise ValueError(f"options.{key} is not supported: {reason}")
    unknown = sorted(set(opt_config) - _OPTIMIZATION_CONFIG_KEYS)
    if unknown:
        raise ValueError(
            "options.optimization_config has unsupported keys: "
            + ", ".join(unknown)
            + f" (supported: {', '.join(sorted(_OPTIMIZATION_CONFIG_KEYS))})"
        )

    resolved_agent = agent_name or agent.get("name")
    if not resolved_agent:
        raise ValueError("agent.name is required (or pass --agent)")

    resolved_eval_model = eval_model or options.get("eval_model")
    if not resolved_eval_model:
        raise ValueError("options.eval_model is required (or pass --eval-model)")

    resolved_opt_model = optimize_model or options.get("optimization_model")
    if not resolved_opt_model:
        raise ValueError(
            "options.optimization_model is required (or pass --optimize-model)"
        )

    resolved_max = max_candidates if max_candidates is not None else options.get("max_candidates")
    if resolved_max is not None and (
        isinstance(resolved_max, bool) or int(resolved_max) < 1
    ):
        raise ValueError("max_candidates must be >= 1")

    target: dict[str, Any] = {"type": "foundry_agent", "name": str(resolved_agent)}
    version = agent_version or agent.get("version")
    if version not in (None, ""):
        target["version"] = str(version)

    request: dict[str, Any] = {}
    if options.get("display_name"):
        request["display_name"] = str(options["display_name"])
    request["target_configuration"] = target
    request["optimization_model_configuration"] = {"model": str(resolved_opt_model)}

    evaluators = eval_config.get("evaluators") or []
    validation = _evaluation_set(base, eval_config.get("validation_dataset"), None,
                                 where="validation_dataset")
    concurrency = options.get("max_concurrent_agent_runs")

    traces = eval_config.get("traces")
    if traces is not None:
        conflicts = [
            name
            for name, present in (
                ("dataset", eval_config.get("dataset") is not None),
                ("dataset_file", eval_config.get("dataset_file") is not None),
                ("options.max_candidates", resolved_max is not None),
                ("options.optimization_config", bool(opt_config)),
            )
            if present
        ]
        if conflicts:
            raise ValueError(
                "traces: runs skill optimization from agent traces and cannot be "
                "combined with " + ", ".join(conflicts)
            )
        evaluation: dict[str, Any] = {"evaluation_model": {"model": str(resolved_eval_model)}}
        if evaluators:
            evaluation["evaluators"] = _evaluator_refs(evaluators)
        if validation is not None:
            evaluation["validation_set"] = validation
        if concurrency is not None:
            evaluation["max_concurrent_agent_runs"] = _positive_int(
                concurrency, "options.max_concurrent_agent_runs"
            )
        request["optimization_configuration"] = _skill_optimization(
            traces, evaluation, agent_name=str(resolved_agent)
        )
        return request

    training = _evaluation_set(
        base,
        eval_config.get("dataset"),
        eval_config.get("dataset_file"),
        where="dataset",
    )
    if training is None:
        raise ValueError("dataset.local_uri, dataset.name, or dataset_file is required")

    baseline = _load_baseline(base=base, agent=agent, config_dir=config_dir, candidate=candidate)
    baseline_config: dict[str, Any] = {}
    system_prompt = opt_config.get("system_prompt") or baseline.get("instructions")
    if system_prompt:
        baseline_config["system_prompt"] = system_prompt
    model = opt_config.get("model") or agent.get("model") or baseline.get("model")
    if model:
        baseline_config["current_model"] = str(model)
    baseline_config["skills"] = list(opt_config.get("skills") or [])
    baseline_config["tools"] = list(opt_config.get("tools") or baseline.get("tools") or [])

    space: dict[str, Any] = {}
    if opt_config.get("target_attributes") is not None:
        attributes = [str(item) for item in opt_config["target_attributes"]]
        bad = sorted(set(attributes) - TARGET_ATTRIBUTES)
        if bad:
            raise ValueError(
                "optimization_config.target_attributes has unsupported values: "
                + ", ".join(bad)
            )
        space["target_attributes"] = attributes
    if opt_config.get("model_search_space") is not None:
        space["model_search_space"] = [str(item) for item in opt_config["model_search_space"]]

    evaluation = {
        "training_set": training,
        "evaluators": _evaluator_refs(evaluators),
        "evaluation_model": {"model": str(resolved_eval_model)},
    }
    if validation is not None:
        evaluation["validation_set"] = validation
    if concurrency is not None:
        evaluation["max_concurrent_agent_runs"] = _positive_int(
            concurrency, "options.max_concurrent_agent_runs"
        )

    search: dict[str, Any] = {}
    if resolved_max is not None:
        search["max_candidates"] = int(resolved_max)

    request["optimization_configuration"] = {
        "type": "agent_optimization",
        "goal": str(options.get("goal") or "improve_quality"),
        "evaluation_configuration": evaluation,
        "candidate_search_configuration": search,
        "baseline_agent_configuration": baseline_config,
        "agent_optimization_space": space,
    }
    return request


def request_summary(request: dict[str, Any]) -> dict[str, Any]:
    """Return the human-facing facts of a GA optimizer request."""

    opt = request.get("optimization_configuration") or {}
    evaluation = opt.get("evaluation_configuration") or {}
    baseline = opt.get("baseline_agent_configuration") or {}
    return {
        "agent": (request.get("target_configuration") or {}).get("name"),
        "type": opt.get("type"),
        "eval_model": (evaluation.get("evaluation_model") or {}).get("model"),
        "optimization_model": (request.get("optimization_model_configuration") or {}).get(
            "model"
        ),
        "tools": [
            (tool.get("function") or {}).get("name", "?")
            for tool in baseline.get("tools") or []
            if isinstance(tool, dict)
        ],
    }


class OptimizerClient:
    """Small REST client for the GA Foundry Agent Optimizer API."""

    def __init__(
        self,
        project_endpoint: str,
        *,
        credential: TokenCredential | None = None,
    ) -> None:
        from azure.identity import DefaultAzureCredential

        self.project_endpoint = project_endpoint.rstrip("/")
        self._credential = credential or DefaultAzureCredential()
        self._client = PipelineClient(
            base_url=self.project_endpoint,
            policies=[
                BearerTokenCredentialPolicy(self._credential, AUTH_SCOPE),
                RetryPolicy(),
            ],
        )

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        """Submit an optimizer job (billable) and return the job."""

        return self._send_json("POST", "/agent_optimization_jobs", json_body=request)

    def estimate(self, request: dict[str, Any]) -> dict[str, Any]:
        """Validate a job request and return its cost estimate without starting it."""

        return self._send_json("POST", "/agent_optimization_jobs:estimate", json_body=request)

    def status(self, job_id: str) -> dict[str, Any]:
        """Fetch an optimizer job."""

        return self._send_json("GET", f"/agent_optimization_jobs/{_seg(job_id)}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel an optimizer job."""

        return self._send_json("POST", f"/agent_optimization_jobs/{_seg(job_id)}:cancel")

    def list_candidates(self, job_id: str) -> list[dict[str, Any]]:
        """List every candidate of a job, following ``has_more`` pagination."""

        candidates: list[dict[str, Any]] = []
        params: dict[str, str] = {}
        while True:
            page = self._send_json(
                "GET", f"/agent_optimization_jobs/{_seg(job_id)}/candidates", params=params
            )
            data = page.get("data")
            if not isinstance(data, list):
                raise TypeError("candidate list response has no data array")
            candidates.extend(item for item in data if isinstance(item, dict))
            if not page.get("has_more") or not page.get("last_id"):
                return candidates
            params = {"after": str(page["last_id"])}

    def get_candidate(self, job_id: str, candidate_id: str) -> dict[str, Any]:
        """Fetch one candidate including its mutations and file manifest."""

        return self._send_json(
            "GET",
            f"/agent_optimization_jobs/{_seg(job_id)}/candidates/{_seg(candidate_id)}",
            params={"expand": "mutations"},
        )

    def download_file(
        self,
        job_id: str,
        path: str,
        *,
        candidate_id: str | None = None,
    ) -> bytes:
        """Download a job or candidate file (for example ``config.json``)."""

        params = {"path": path}
        if candidate_id is not None:
            params["candidate_id"] = candidate_id
        response = self._send(
            "GET", f"/agent_optimization_jobs/{_seg(job_id)}/files", params=params
        )
        return bytes(response.read() if hasattr(response, "read") else response.content)

    def candidate_config(self, job_id: str, candidate_id: str) -> dict[str, Any]:
        """Reconstruct a candidate's full runnable configuration.

        The job's server-side baseline is the base layer. The candidate's
        ``config.json`` file is authoritative for instructions and model; tool
        and skill mutations are layered on by name. Anything that cannot be
        reconstructed exactly raises :class:`CandidateResolutionError` instead of
        silently falling back to the local baseline.
        """

        job = self.status(job_id)
        candidate = self.get_candidate(job_id, candidate_id)
        config_file = None
        if _has_file(candidate, "config.json"):
            raw = self.download_file(job_id, "config.json", candidate_id=candidate_id)
            try:
                config_file = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CandidateResolutionError(
                    f"candidate {candidate_id}: config.json is not valid JSON: {exc}"
                ) from exc
        return resolve_candidate_config(job, candidate, config_file)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        query = dict(params or {})
        query["api-version"] = API_VERSION
        url = f"{self.project_endpoint}{path}?{urlencode(query)}"
        request = HttpRequest(method, url)
        if json_body is not None:
            request.headers["Content-Type"] = "application/json"
            request.set_json_body(json_body)
        response = self._client.send_request(request)
        try:
            response.raise_for_status()
        except HttpResponseError as exc:
            body = ""
            text = getattr(response, "text", None)
            if callable(text):
                body = text()
            elif isinstance(text, str):
                body = text
            detail = f": {body}" if body else ""
            raise RuntimeError(f"{method} {url} failed: {exc.message}{detail}") from exc
        return response

    def _send_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._send(method, path, params=params, json_body=json_body)
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError(f"{method} {path} returned non-object JSON")
        return data


def resolve_candidate_config(
    job: dict[str, Any],
    candidate: dict[str, Any],
    config_file: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine the job baseline, a candidate's ``config.json`` and its mutations."""

    candidate_id = str(candidate.get("candidate_id") or "?")
    opt = job.get("optimization_configuration") or {}
    if opt.get("type") not in (None, "agent_optimization"):
        raise CandidateResolutionError(
            f"candidate {candidate_id}: apply supports agent_optimization jobs only "
            f"(job type is {opt.get('type')!r})"
        )
    status = str(candidate.get("status") or "").lower()
    if status and status != "completed":
        raise CandidateResolutionError(
            f"candidate {candidate_id} is {status}; only completed candidates can be applied"
        )
    baseline = opt.get("baseline_agent_configuration") or {}
    config: dict[str, Any] = {
        "instructions": baseline.get("system_prompt"),
        "model": baseline.get("current_model"),
        "tools": list(baseline.get("tools") or []),
        "skills": list(baseline.get("skills") or []),
    }

    mutations = ((candidate.get("output") or {}).get("mutations")) or []
    if config_file is None and not mutations:
        raise CandidateResolutionError(
            f"candidate {candidate_id} has no config.json and no mutations "
            "(it is likely the baseline); nothing to apply"
        )
    if config_file is not None:
        if not isinstance(config_file, dict):
            raise CandidateResolutionError(
                f"candidate {candidate_id}: config.json must be a JSON object"
            )
        for key in ("instructions", "model"):
            if config_file.get(key):
                config[key] = config_file[key]
        for key in ("tools", "skills"):
            if isinstance(config_file.get(key), list):
                config[key] = list(config_file[key])

    for mutation in mutations:
        kind = mutation.get("type") if isinstance(mutation, dict) else None
        value = mutation.get("value") if isinstance(mutation, dict) else None
        if kind == "instructions" and isinstance(value, str) and value:
            if config_file is None:
                config["instructions"] = value
        elif kind == "model" and isinstance(value, str) and value:
            if config_file is None:
                config["model"] = value
        elif kind == "tools" and isinstance(value, list):
            config["tools"] = _merge_by_name(config["tools"], value, _tool_name)
        elif kind == "skills" and isinstance(value, list):
            config["skills"] = _merge_by_name(config["skills"], value, _skill_name)
        else:
            raise CandidateResolutionError(
                f"candidate {candidate_id}: unsupported or malformed mutation {kind!r}"
            )

    if not config.get("instructions"):
        raise CandidateResolutionError(
            f"candidate {candidate_id}: resolved configuration has no instructions"
        )
    return {key: value for key, value in config.items() if value not in (None, [])}


def save_optimizer_state(
    state: OptimizerState,
    *,
    config_dir: str | os.PathLike = DEFAULT_CONFIG_DIR,
) -> Path:
    """Persist the latest optimizer job under ``.agent_configs/.castia``."""

    path = Path(config_dir) / ".castia" / "last_optimize.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.__dict__, indent=2) + "\n", encoding="utf-8")
    return path


def load_optimizer_state(
    config_dir: str | os.PathLike = DEFAULT_CONFIG_DIR,
) -> OptimizerState:
    """Load the latest optimizer job submitted from this agent root."""

    path = Path(config_dir) / ".castia" / "last_optimize.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return OptimizerState(
        job_id=data["job_id"],
        project_endpoint=data["project_endpoint"],
        agent_name=data["agent_name"],
        created_at=float(data.get("created_at", 0)),
    )


def apply_candidate_config(
    candidate_id: str,
    config: dict[str, Any],
    *,
    config_dir: str | os.PathLike = DEFAULT_CONFIG_DIR,
) -> CandidateApplyPlan:
    """Write a candidate config into ``.agent_configs/<candidate_id>/``."""

    candidate_dir = Path(config_dir) / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)

    instructions_path: Path | None = None
    tools_path: Path | None = None
    skills_dir: Path | None = None
    metadata: dict[str, Any] = {}

    if config.get("model"):
        metadata["model"] = config["model"]
    if config.get("temperature") is not None:
        metadata["temperature"] = config["temperature"]

    instructions = config.get("instructions") or config.get("system_prompt")
    if instructions:
        instructions_path = candidate_dir / "instructions.md"
        instructions_path.write_text(str(instructions), encoding="utf-8")
        metadata["instruction_file"] = "instructions.md"

    tools = config.get("tools")
    if isinstance(tools, list) and tools:
        tools_path = candidate_dir / "tools.json"
        tools_path.write_text(
            json.dumps(tools, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        metadata["tool_file"] = "tools.json"
        metadata["tools_file"] = "tools.json"

    skills = config.get("skills")
    if isinstance(skills, list) and skills:
        skills_dir = candidate_dir / "skills"
        for skill in skills:
            if not isinstance(skill, dict) or not skill.get("name"):
                continue
            skill_path = skills_dir / str(skill["name"]) / "SKILL.md"
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(_render_skill(skill), encoding="utf-8")
        if skills_dir.exists():
            metadata["skill_dir"] = "skills"

    metadata_path = candidate_dir / "metadata.yaml"
    _write_yaml(metadata_path, metadata)
    return CandidateApplyPlan(
        candidate_dir=candidate_dir,
        metadata_path=metadata_path,
        instructions_path=instructions_path,
        tools_path=tools_path,
        skills_dir=skills_dir,
    )


def is_terminal_status(status: str | None) -> bool:
    """Return whether an optimizer status is terminal (``skipped`` included)."""

    return str(status or "").lower() in TERMINAL_STATUSES


def candidate_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Return ``result.candidate_summary`` from a job payload (or ``{}``)."""

    result = status.get("result")
    summary = result.get("candidate_summary") if isinstance(result, dict) else None
    return summary if isinstance(summary, dict) else {}


def best_candidate_id(status: dict[str, Any]) -> str | None:
    """Extract the best candidate id from a GA job payload."""

    best = candidate_summary(status).get("best_id")
    return str(best) if best else None


def job_id(payload: dict[str, Any]) -> str | None:
    """Extract an optimizer job id from a job payload."""

    for key in ("id", "job_id"):
        if payload.get(key):
            return str(payload[key])
    return None


def _skill_optimization(
    traces: object,
    evaluation: dict[str, Any],
    *,
    agent_name: str,
) -> dict[str, Any]:
    if not isinstance(traces, dict):
        raise TypeError("traces: expected a mapping")
    unknown = sorted(set(traces) - _TRACE_KEYS)
    if unknown:
        raise ValueError("traces has unsupported keys: " + ", ".join(unknown))
    resource_id = traces.get("resource_id")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("traces.resource_id (Application Insights resource id) is required")
    connection = {
        "type": "application_insights",
        "resource_id": resource_id.strip(),
        "agent_name": str(traces.get("agent_name") or agent_name),
    }
    config: dict[str, Any] = {
        "type": "skill_optimization",
        "context": [
            {
                "type": "agent_traces",
                "trace_connection_configuration": connection,
                "trace_window": _trace_window(traces.get("window")),
            }
        ],
        "evaluation_configuration": evaluation,
    }
    ratio = traces.get("training_split_ratio")
    if ratio is not None:
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio < 1:
            raise ValueError("traces.training_split_ratio must be between 0 and 1 (exclusive)")
        config["training_split_ratio"] = float(ratio)
    return config


def _trace_window(window: object) -> dict[str, Any]:
    if not isinstance(window, dict):
        raise ValueError(  # noqa: TRY004 - config errors stay ValueError for the CLI
            "traces.window is required: {start_time, end_time[, max_traces]} "
            "or {trace_ids, lookback_hours}"
        )
    fixed = {"start_time", "end_time", "max_traces"}
    by_id = {"trace_ids", "lookback_hours"}
    keys = set(window)
    if keys & fixed and keys & by_id:
        raise ValueError("traces.window cannot mix a time range with trace_ids")
    if keys & by_id:
        if keys - by_id:
            raise ValueError("traces.window has unsupported keys: " + ", ".join(sorted(keys - by_id)))
        ids = window.get("trace_ids")
        if not isinstance(ids, list) or not ids or not all(
            isinstance(item, str) and item.strip() for item in ids
        ):
            raise ValueError("traces.window.trace_ids must be a non-empty list of ids")
        return {
            "type": "trace_ids",
            "trace_ids": [item.strip() for item in ids],
            "lookback_hours": _positive_int(
                window.get("lookback_hours"), "traces.window.lookback_hours"
            ),
        }
    if keys - fixed:
        raise ValueError("traces.window has unsupported keys: " + ", ".join(sorted(keys - fixed)))
    start = _aware_datetime(window.get("start_time"), "traces.window.start_time")
    end = _aware_datetime(window.get("end_time"), "traces.window.end_time")
    if start >= end:
        raise ValueError("traces.window.start_time must be before end_time")
    payload: dict[str, Any] = {
        "type": "fixed",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }
    if window.get("max_traces") is not None:
        payload["max_traces"] = _positive_int(window["max_traces"], "traces.window.max_traces")
    return payload


def _aware_datetime(value: object, where: str) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = _dt.datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"{where}: not an ISO 8601 timestamp: {value!r}") from exc
    else:
        raise ValueError(f"{where} is required (ISO 8601 with a timezone)")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{where} must include a timezone (for example 2026-01-01T00:00:00Z)")
    return parsed


def _positive_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{where} must be a positive integer")
    return value


def _load_baseline(
    *,
    base: Path,
    agent: dict[str, Any],
    config_dir: str | os.PathLike,
    candidate: str,
) -> dict[str, Any]:
    config = agent.get("config")
    if config:
        config_path = base / str(config)
        if not config_path.exists():
            config_path = base / config_dir / candidate / "metadata.yaml"
    else:
        config_path = base / config_dir / candidate / "metadata.yaml"
    metadata = _read_yaml(config_path) if config_path.exists() else {}
    config_base = config_path.parent

    model = metadata.get("model")
    instructions = _read_text_ref(config_base, metadata, "instruction_file")
    tools = _read_json_ref(config_base, metadata, ("tool_file", "tools_file"))
    return {"model": model, "instructions": instructions, "tools": tools}


def _evaluation_set(
    base: Path,
    block: object,
    legacy_file: object,
    *,
    where: str,
) -> dict[str, Any] | None:
    source: dict[str, Any] | None = None
    if legacy_file:
        source = _inline_source(base / str(legacy_file))
    elif isinstance(block, dict) and block.get("local_uri"):
        source = _inline_source(base / str(block["local_uri"]))
    elif isinstance(block, dict) and block.get("name"):
        if block.get("version") in (None, ""):
            raise ValueError(
                f"{where}.version is required for a registered dataset "
                f"({where}.name={block['name']!r})"
            )
        source = {
            "type": "dataset_reference",
            "name": str(block["name"]),
            "version": str(block["version"]),
        }
    if source is None:
        return None
    return {"type": "target_completion", "source": source}


def _inline_source(path: Path) -> dict[str, Any]:
    cases = [
        _test_case(row, f"{path}:{lineno}") for lineno, row in _load_jsonl(path)
    ]
    return {"type": "inline", "test_cases": cases}


def _test_case(row: dict[str, Any], where: str) -> dict[str, Any]:
    query = row.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"{where}: 'query' must be non-empty text")
    unknown = sorted(set(row) - {"query", *GROUND_TRUTH_ALIASES})
    if unknown:
        raise ValueError(
            f"{where}: unsupported fields {', '.join(unknown)}; optimizer test cases "
            "accept query and ground_truth (aliases: answer, expected)"
        )
    truths = {
        key: _as_text(row[key]) for key in GROUND_TRUTH_ALIASES if row.get(key) is not None
    }
    if len(set(truths.values())) > 1:
        raise ValueError(
            f"{where}: conflicting ground truth values in {', '.join(sorted(truths))}"
        )
    case: dict[str, Any] = {"query": query}
    if truths:
        case["ground_truth"] = next(iter(truths.values()))
    return case


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _evaluator_refs(entries: list[Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, str):
            refs.append({"name": entry})
        elif isinstance(entry, dict) and entry.get("name"):
            ref = {"name": entry["name"]}
            if entry.get("version") is not None:
                ref["version"] = str(entry["version"])
            if entry.get("initialization_parameters") is not None:
                ref["initialization_parameters"] = entry["initialization_parameters"]
            refs.append(ref)
    if not refs:
        raise ValueError("at least one evaluator is required")
    return refs


def _load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise TypeError(f"{path}:{lineno}: expected a JSON object")
        rows.append((lineno, row))
    if not rows:
        raise ValueError(f"{path}: JSONL file is empty")
    return rows


def _has_file(candidate: dict[str, Any], path: str) -> bool:
    files = candidate.get("files")
    return isinstance(files, list) and any(
        isinstance(item, dict) and item.get("path") == path for item in files
    )


def _tool_name(tool: object) -> str | None:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        if tool.get("name"):
            return str(tool["name"])
    return None


def _skill_name(skill: object) -> str | None:
    if isinstance(skill, dict) and skill.get("name"):
        return str(skill["name"])
    return None


def _merge_by_name(base: list[Any], updates: list[Any], name_of) -> list[Any]:
    merged = list(base)
    index = {name_of(item): pos for pos, item in enumerate(merged) if name_of(item)}
    for item in updates:
        name = name_of(item)
        if name is None:
            raise CandidateResolutionError(f"mutation entry has no name: {item!r}")
        if name in index:
            merged[index[name]] = item
        else:
            index[name] = len(merged)
            merged.append(item)
    return merged


def _seg(value: str) -> str:
    return quote(str(value), safe="")


def _read_text_ref(base: Path, metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    return path.read_text(encoding="utf-8")


def _read_json_ref(
    base: Path,
    metadata: dict[str, Any],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    value = next((metadata.get(key) for key in keys if metadata.get(key)), None)
    if not value:
        return []
    path = Path(str(value))
    if not path.is_absolute():
        path = base / path
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _read_yaml(path: Path) -> dict[str, Any]:
    data = _yaml().load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected a YAML mapping")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    yaml = _yaml()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(data, handle)


def _yaml():
    try:
        from ruamel.yaml import YAML
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(
            "The optimizer CLI needs ruamel.yaml. Install with: "
            "pip install 'castia[optimize]'"
        ) from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _render_skill(skill: dict[str, Any]) -> str:
    lines = ["---", f"name: {skill['name']}"]
    if skill.get("description"):
        lines.append(f"description: {skill['description']}")
    lines.append("---")
    body = str(skill.get("body") or "")
    if body:
        lines.append(body.rstrip("\n"))
    return "\n".join(lines) + "\n"


class StaticTokenCredential(TokenCredential):
    """Tiny test helper credential used by CLI/client unit tests."""

    def __init__(self, token: str = "token") -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken(self._token, int(time.time()) + 3600)

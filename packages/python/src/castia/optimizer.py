"""Castia-native Foundry Agent Optimizer client and config builder.

This module owns the optimizer request shape directly instead of shelling out to
``azd ai agent optimize``. ``azd`` remains the deployment rail; Castia owns the
canonical ``eval.yaml`` + ``.agent_configs`` interpretation so prompt/tool
optimization cannot drift through azd metadata rewrites.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from azure.core import PipelineClient
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
from azure.core.rest import HttpRequest

from .optimize import DEFAULT_CANDIDATE, DEFAULT_CONFIG_DIR

API_VERSION = "v1"
AUTH_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_FEATURES = "AgentsOptimization=V2Preview"
LAST_RUN_FILE = ".castia/last_optimize.json"


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
    """Build the Agent Optimizer request from Castia's canonical files.

    ``root`` is the directory containing ``eval.yaml``. Local dataset paths are
    resolved relative to it; baseline prompt/tools paths resolve through
    ``agent.config`` when present, otherwise ``config_dir/candidate``.
    """

    base = Path(root)
    agent = dict(eval_config.get("agent") or {})
    options = dict(eval_config.get("options") or {})
    opt_config = dict(options.get("optimization_config") or {})

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
    if resolved_max is not None and int(resolved_max) < 1:
        raise ValueError("max_candidates must be >= 1")

    baseline = _load_baseline(
        base=base,
        agent=agent,
        config_dir=config_dir,
        candidate=candidate,
    )

    model = agent.get("model") or baseline.get("model")
    if model:
        opt_config["model"] = model
    if baseline.get("instructions"):
        opt_config["system_prompt"] = baseline["instructions"]
    if "skills" not in opt_config:
        opt_config["skills"] = []
    if baseline.get("tools"):
        opt_config["tools"] = baseline["tools"]

    request: dict[str, Any] = {
        "agent": {
            "agent_name": resolved_agent,
            "agent_version": agent_version or agent.get("version") or "",
        },
        "evaluators": _evaluator_refs(eval_config.get("evaluators") or []),
        "options": {
            "eval_model": resolved_eval_model,
            "optimization_model": resolved_opt_model,
            "optimization_config": opt_config,
        },
    }
    if resolved_max is not None:
        request["options"]["max_candidates"] = int(resolved_max)

    for key, wire_key in (
        ("evaluation_level", "evaluation_level"),
        ("max_stalls", "max_stalls"),
        ("max_concurrent_agent_runs", "max_concurrent_agent_runs"),
    ):
        if key in options:
            request["options"][wire_key] = options[key]

    train = _dataset_payload(
        base,
        eval_config.get("dataset"),
        eval_config.get("dataset_file"),
    )
    if train is None:
        raise ValueError("dataset.local_uri, dataset.name, or dataset_file is required")
    request["train_dataset"] = train

    validation = _dataset_payload(base, eval_config.get("validation_dataset"), None)
    if validation is not None:
        request["validation_dataset"] = validation

    return request


class OptimizerClient:
    """Small REST client for the Foundry Agent Optimizer API."""

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
        """Submit an optimizer job and return the service response."""

        return self._send_json(
            "POST",
            "/agent_optimization_jobs",
            json_body={"inputs": request},
        )

    def status(self, job_id: str) -> dict[str, Any]:
        """Fetch optimizer job status."""

        return self._send_json("GET", f"/agent_optimization_jobs/{job_id}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel an optimizer job."""

        return self._send_json("POST", f"/agent_optimization_jobs/{job_id}:cancel")

    def candidate_config(self, job_id: str, candidate_id: str) -> dict[str, Any]:
        """Fetch a candidate config payload."""

        return self._send_json(
            "GET",
            f"/agent_optimization_jobs/{job_id}/candidates/{candidate_id}/config",
        )

    def _send_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = _with_api_version(f"{self.project_endpoint}{path}")
        request = HttpRequest(method, url)
        request.headers["Foundry-Features"] = FOUNDRY_FEATURES
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
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError(f"{method} {url} returned non-object JSON")
        return data


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
    """Return whether an optimizer status is terminal."""

    return str(status or "").lower() in {
        "succeeded",
        "failed",
        "cancelled",
        "canceled",
        "completed",
    }


def best_candidate_id(status: dict[str, Any]) -> str | None:
    """Extract the best candidate id from a status payload."""

    result = status.get("result")
    if isinstance(result, dict) and result.get("best"):
        return str(result["best"])
    if isinstance(result, dict):
        for key in ("best_candidate_id", "bestCandidateId"):
            if result.get(key):
                return str(result[key])
        candidates = result.get("candidates")
        if isinstance(candidates, list):
            scored = [c for c in candidates if isinstance(c, dict)]
            if scored:
                best = max(
                    scored,
                    key=lambda c: float(
                        c.get("avg_score")
                        or c.get("average_score")
                        or c.get("score")
                        or 0
                    ),
                )
                candidate_id = best.get("candidate_id") or best.get("candidateId")
                if candidate_id:
                    return str(candidate_id)
    return None


def job_id(payload: dict[str, Any]) -> str | None:
    """Extract an optimizer job id from response/status payloads."""

    for key in ("operation_id", "operationId", "id", "job_id", "jobId"):
        if payload.get(key):
            return str(payload[key])
    return None


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


def _dataset_payload(
    base: Path,
    block: object,
    legacy_file: object,
) -> dict[str, Any] | None:
    if legacy_file:
        return {"type": "inline", "items": _load_jsonl(base / str(legacy_file))}
    if not isinstance(block, dict):
        return None
    if block.get("local_uri"):
        return {"type": "inline", "items": _load_jsonl(base / str(block["local_uri"]))}
    if block.get("name"):
        payload = {"type": "reference", "name": block["name"]}
        if block.get("version") is not None:
            payload["version"] = str(block["version"])
        return payload
    return None


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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: JSONL file is empty")
    return rows


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


def _with_api_version(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({'api-version': API_VERSION})}"


class StaticTokenCredential(TokenCredential):
    """Tiny test helper credential used by CLI/client unit tests."""

    def __init__(self, token: str = "token") -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken(self._token, int(time.time()) + 3600)

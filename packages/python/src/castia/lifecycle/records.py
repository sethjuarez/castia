"""Versioned, immutable local evidence. No credentials or environment discovery.

Identifiers are SHA-256 over canonical UTF-8 JSON, including the record kind and
schema version. Configuration is explicit and must be non-secret; the credential
checks are defense in depth, not a replacement for a caller's data review.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, ClassVar

SCHEMA_VERSION = 1
REFERENCE_ORIGINS = ("human", "authoritative", "deterministic")
_DIAGNOSTICS = {
    "reference_origin": (
        "reference_origin must be one of: human, authoritative, deterministic; "
        "model-generated and unknown origins are not accepted"
    ),
    "review_approval": "approved must be literal true and every trace requires explicit review approval",
    "staged_payload": "staged payload is not bound to the evaluated snapshot; capture files before evaluation",
    "candidate_identity": "candidate agent identity does not match its evaluated snapshot",
}


class LifecycleValidationError(ValueError):
    """A field-specific diagnostic with a fixed, safe public message."""

    def __init__(self, code: str) -> None:
        if code not in _DIAGNOSTICS:
            raise ValueError("unknown lifecycle diagnostic code")
        self.code = code
        super().__init__(_DIAGNOSTICS[code])


_HASH = re.compile(r"[0-9a-f]{64}")
_SECRET_KEY = re.compile(
    r"(^|[_-])(password|passwd|secret|token|api[_-]?key|authorization|credential)"
    r"($|[_-])", re.IGNORECASE
)
_SECRET_VALUE = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|Bearer\s+\S+|"
    r"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})|"
    r"https?://[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"""(?im)(?=(?:^|[,{;#\n])\s*["']?([A-Za-z][A-Za-z0-9_. -]*?)["']?\s*[:=]\s*([^\n,]+))"""
)
_PLACEHOLDER = re.compile(r"(?:\$\{[A-Z_][A-Z0-9_]*\}|<redacted>|\[redacted\])", re.IGNORECASE)


def _secret_key(key: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return bool(_SECRET_KEY.search(separated.replace(".", "_").replace(" ", "_"))) or normalized in {
        "apikey", "accesstoken", "clientsecret", "connectionstring", "accountkey",
        "sharedaccesssignature", "credentials",
    }


def _placeholder(value: object) -> bool:
    return value is None or isinstance(value, str) and (
        value == "" or _PLACEHOLDER.fullmatch(value) is not None
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate structured configuration key")
        result[key] = value
    return result


def text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def digest(value: object, label: str = "id") -> None:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def number(value: object, label: str, *, minimum: float | None = None) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")


def integer(value: object, label: str, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")


def safe_path(value: str) -> str:
    text(value, "relative path")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        path.is_absolute() or windows.drive or windows.root
        or any(p in ("", ".", "..") for p in normalized.split("/"))
        or ":" in normalized or "\x00" in normalized
        or any(p.endswith((" ", ".")) for p in path.parts)
        or any(p.split(".")[0].upper() in (
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        ) for p in path.parts)
    ):
        raise ValueError("path must be a normalized relative file path")
    return normalized


def check_public(value: object) -> None:
    """Reject common credential shapes without including their values in errors."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            if _secret_key(key) and not _placeholder(item):
                raise ValueError("secret-bearing configuration key is not evidence")
            check_public(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            check_public(item)
    elif isinstance(value, str):
        if _SECRET_VALUE.search(value):
            raise ValueError("credential-shaped content is not evidence")
        for match in _ASSIGNMENT.finditer(value):
            key, item = match.groups()
            if _secret_key(key):
                # Plain text cannot safely disambiguate nested/flow syntax.
                # Structured formats are parsed again below; only whole-value
                # environment/redaction placeholders are accepted here.
                literal = item.strip().rstrip("}").strip().strip("\"'")
                if (
                    literal.lower() not in ("null", "none", "~")
                    and not _placeholder(literal)
                    and not _placeholder(item.strip().strip("\"'"))
                ):
                    raise ValueError("secret-bearing configuration assignment is not evidence")


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("JSON keys must be strings")
        return MappingProxyType({k: freeze(v) for k, v in sorted(value.items())})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("evidence requires finite JSON values")


def plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        result = {f.name: plain(getattr(value, f.name)) for f in fields(value)}
        if isinstance(value, Record):
            result["kind"] = value.kind
        return result
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        plain(freeze(value) if not is_dataclass(value) else value),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sequence(value: object, name: str) -> tuple:
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} must be an array")
    return tuple(value)


def _items(obj: object, name: str, cls: type) -> tuple:
    values = _sequence(getattr(obj, name), name)
    if any(not isinstance(v, cls) for v in values):
        raise ValueError(f"{name} contains an invalid record")
    object.__setattr__(obj, name, values)
    return values


def _mapping(obj: object, name: str) -> None:
    value = getattr(obj, name)
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    check_public(value)
    object.__setattr__(obj, name, freeze(value))


@dataclass(frozen=True, kw_only=True)
class Record:
    schema_version: int = SCHEMA_VERSION
    kind: ClassVar[str]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported evidence schema version")
        check_public(self.to_dict())

    @property
    def id(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        return plain(self)


@dataclass(frozen=True)
class FileDigest:
    path: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", safe_path(self.path))
        digest(self.sha256)
        integer(self.size, "file size")


@dataclass(frozen=True, kw_only=True)
class AgentSnapshot(Record):
    kind: ClassVar[str] = "agent"
    source_files: tuple[FileDigest, ...]
    dependencies: Mapping[str, str]
    model: Mapping[str, Any]
    instructions: str
    tools: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        files = _items(self, "source_files", FileDigest)
        if len({f.path.casefold() for f in files}) != len(files):
            raise ValueError("duplicate source paths")
        object.__setattr__(self, "source_files", tuple(sorted(files, key=lambda f: f.path)))
        _mapping(self, "dependencies")
        for key, value in self.dependencies.items():
            text(key, "dependency")
            text(value, "dependency version")
        _mapping(self, "model")
        if not self.model:
            raise ValueError("model configuration is required")
        text(self.instructions, "instructions")
        check_public(self.instructions)
        tools = _sequence(self.tools, "tools")
        if any(not isinstance(t, Mapping) for t in tools):
            raise ValueError("tools must contain JSON objects")
        check_public(tools)
        object.__setattr__(self, "tools", freeze(tools))


@dataclass(frozen=True)
class Example:
    input: str
    reference: str
    group: str
    provenance: tuple[str, ...]
    reviewers: tuple[str, ...]
    reference_origins: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("input", "reference", "group"):
            text(getattr(self, name), name)
            check_public(getattr(self, name))
        for name in ("provenance", "reviewers"):
            values = tuple(sorted(set(_sequence(getattr(self, name), name))))
            if not values:
                raise ValueError(f"{name} is required")
            for value in values:
                text(value, name)
                check_public(value)
            object.__setattr__(self, name, values)
        origins = tuple(sorted(set(_sequence(self.reference_origins, "reference_origins"))))
        if not origins or any(origin not in REFERENCE_ORIGINS for origin in origins):
            raise LifecycleValidationError("reference_origin")
        object.__setattr__(self, "reference_origins", origins)

    @property
    def id(self) -> str:
        return content_hash({"input": self.input})


@dataclass(frozen=True, kw_only=True)
class DatasetSnapshot(Record):
    kind: ClassVar[str] = "dataset"
    examples: tuple[Example, ...]
    train_ids: tuple[str, ...]
    heldout_ids: tuple[str, ...]
    seed: str
    redaction_version: str

    def __post_init__(self) -> None:
        super().__post_init__()
        examples = _items(self, "examples", Example)
        ids = {e.id for e in examples}
        if not examples or len(ids) != len(examples):
            raise ValueError("dataset must have unique nonempty examples")
        object.__setattr__(self, "examples", tuple(sorted(examples, key=lambda e: e.id)))
        for name in ("train_ids", "heldout_ids"):
            values = tuple(sorted(_sequence(getattr(self, name), name)))
            for value in values:
                digest(value)
            if not values or len(set(values)) != len(values):
                raise ValueError("both splits must be nonempty and unique")
            object.__setattr__(self, name, values)
        train, heldout = set(self.train_ids), set(self.heldout_ids)
        if train & heldout or train | heldout != ids:
            raise ValueError("splits must partition the dataset without leakage")
        if {e.group for e in examples if e.id in train} & {
            e.group for e in examples if e.id in heldout
        }:
            raise ValueError("a provenance group cannot cross splits")
        text(self.seed, "seed")
        text(self.redaction_version, "redaction_version")


@dataclass(frozen=True)
class Evaluator:
    name: str
    version: str
    configuration: Mapping[str, Any]

    def __post_init__(self) -> None:
        text(self.name, "evaluator name")
        text(self.version, "evaluator version")
        _mapping(self, "configuration")

    @property
    def id(self) -> str:
        return content_hash(self)


@dataclass(frozen=True)
class EvaluationResult:
    example_id: str
    repetition: int
    metrics: Mapping[str, float]
    error: str | None = None

    def __post_init__(self) -> None:
        digest(self.example_id)
        integer(self.repetition, "repetition")
        _mapping(self, "metrics")
        for key, value in self.metrics.items():
            text(key, "metric")
            number(value, key, minimum=0 if key in ("latency_seconds", "cost") else None)
        if self.error is not None:
            if self.error not in ("timeout", "callback_error", "invalid_metrics"):
                raise ValueError("unsupported error code")
            if self.metrics:
                raise ValueError("failed evaluations cannot carry successful metrics")


@dataclass(frozen=True, kw_only=True)
class Run(Record):
    kind: ClassVar[str] = "run"
    agent_id: str
    dataset_id: str
    evaluator: Evaluator
    split: str
    expected_ids: tuple[str, ...]
    repeats: int
    results: tuple[EvaluationResult, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        digest(self.agent_id)
        digest(self.dataset_id)
        if not isinstance(self.evaluator, Evaluator):
            raise TypeError("a versioned evaluator is required")
        if self.split not in ("train", "heldout"):
            raise ValueError("split must be train or heldout")
        integer(self.repeats, "repeats", minimum=1)
        ids = tuple(sorted(_sequence(self.expected_ids, "expected_ids")))
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("expected coverage must be unique and nonempty")
        for value in ids:
            digest(value)
        object.__setattr__(self, "expected_ids", ids)
        results = _items(self, "results", EvaluationResult)
        keys = {(r.example_id, r.repetition) for r in results}
        if len(keys) != len(results) or any(
            r.example_id not in ids or r.repetition >= self.repeats for r in results
        ):
            raise ValueError("unexpected or duplicate evaluation result")
        object.__setattr__(
            self, "results", tuple(sorted(results, key=lambda r: (r.example_id, r.repetition)))
        )


@dataclass(frozen=True)
class ConfigFile:
    path: str
    content: str
    sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", safe_path(self.path))
        if not isinstance(self.content, str):
            raise TypeError("config content must be UTF-8 text")
        check_public(self.content)
        suffix = PurePosixPath(self.path).suffix.lower()
        if suffix == ".json":
            parsed = json.loads(self.content, object_pairs_hook=_unique_keys)
            check_public(parsed)
            freeze(parsed)
        elif suffix in (".yaml", ".yml"):
            try:
                from ruamel.yaml import YAML, YAMLError
            except ModuleNotFoundError as exc:
                raise ValueError("YAML capture requires castia[deploy]; use JSON otherwise") from exc
            yaml = YAML(typ="safe", pure=True)
            yaml.allow_duplicate_keys = False
            try:
                parsed = yaml.load(self.content)
            except YAMLError as exc:
                raise ValueError("invalid safe YAML configuration") from exc
            check_public(parsed)
            freeze(parsed)
        elif suffix == ".toml":
            import tomllib

            parsed = tomllib.loads(self.content)
            check_public(parsed)
            freeze(parsed)
        elif suffix not in (".md", ".txt"):
            raise ValueError("unsupported config format; use JSON, YAML, TOML, Markdown, or text")
        checksum = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.sha256 != "" and self.sha256 != checksum:
            raise ValueError("configuration file digest mismatch")
        object.__setattr__(self, "sha256", checksum)


@dataclass(frozen=True, kw_only=True)
class Candidate(Record):
    kind: ClassVar[str] = "candidate"
    baseline_id: str
    agent_id: str
    files: tuple[ConfigFile, ...]
    agent_snapshot: AgentSnapshot

    def __post_init__(self) -> None:
        super().__post_init__()
        digest(self.baseline_id)
        digest(self.agent_id)
        if not isinstance(self.agent_snapshot, AgentSnapshot):
            raise TypeError("candidate requires its evaluated agent snapshot")
        if self.agent_snapshot.id != self.agent_id:
            raise LifecycleValidationError("candidate_identity")
        items = _items(self, "files", ConfigFile)
        if not items or len({f.path.casefold() for f in items}) != len(items):
            raise ValueError("candidate needs unique explicit configuration files")
        captured = {f.path: f for f in self.agent_snapshot.source_files}
        for item in items:
            source = captured.get(item.path)
            if (
                source is None or source.sha256 != item.sha256
                or source.size != len(item.content.encode("utf-8"))
            ):
                raise LifecycleValidationError("staged_payload")
        object.__setattr__(self, "files", tuple(sorted(items, key=lambda f: f.path)))


@dataclass(frozen=True, kw_only=True)
class Decision(Record):
    kind: ClassVar[str] = "decision"
    baseline_run_id: str
    candidate_run_id: str
    baseline_agent_id: str
    candidate_agent_id: str
    accepted: bool
    reasons: tuple[str, ...]
    aggregates: Mapping[str, Any]
    regressions: tuple[str, ...]
    gate: Mapping[str, Any]

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in (
            "baseline_run_id", "candidate_run_id", "baseline_agent_id", "candidate_agent_id"
        ):
            digest(getattr(self, name), name)
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a boolean")
        reasons = _sequence(self.reasons, "reasons")
        if self.accepted == bool(reasons):
            raise ValueError("accepted decisions have no rejection reasons")
        for reason in reasons:
            text(reason, "reason")
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(
            self, "regressions", tuple(sorted(_sequence(self.regressions, "regressions")))
        )
        for value in self.regressions:
            digest(value, "regression example")
        _mapping(self, "aggregates")
        _mapping(self, "gate")


Evidence = AgentSnapshot | DatasetSnapshot | Run | Candidate | Decision


def record_from_dict(value: Mapping[str, Any]) -> Evidence:
    """Strictly decode schema-v1 records, rejecting unknown fields and kinds."""
    data = dict(value)
    kind = data.pop("kind", None)
    if "schema_version" not in data:
        raise ValueError("schema_version is required")
    try:
        if kind == "agent":
            data["source_files"] = tuple(FileDigest(**f) for f in data["source_files"])
            return AgentSnapshot(**data)
        if kind == "dataset":
            data["examples"] = tuple(Example(**e) for e in data["examples"])
            return DatasetSnapshot(**data)
        if kind == "run":
            data["evaluator"] = Evaluator(**data["evaluator"])
            data["results"] = tuple(EvaluationResult(**r) for r in data["results"])
            return Run(**data)
        if kind == "candidate":
            data["files"] = tuple(ConfigFile(**f) for f in data["files"])
            data["agent_snapshot"] = record_from_dict(data["agent_snapshot"])
            return Candidate(**data)
        if kind == "decision":
            return Decision(**data)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError("malformed evidence record") from exc
    raise ValueError("unknown evidence kind")

"""Evaluator export/import with content-hash identity and version pinning.

Foundry evaluator versions are auto-numbered per project, so ``version: 3``
in one project says nothing about ``version: 3`` in another, and an unpinned
reference silently follows whatever was uploaded last. Castia identifies an
evaluator by a **content hash** of the fields that change how it scores, and
moves it between projects as a bundle::

    {"name": ..., "version": ..., "content_hash": "sha256:...", "body": {...}}

Import is idempotent: it reuses an existing version with the same hash (or a
``castia.content_hash`` tag) and only creates a new version when the content
is new. ``--pin-version`` then writes that version into ``eval.yaml``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from castia.evaluation.suite import EvalSuite

#: Fields that decide how an evaluator scores. Display names, descriptions,
#: tags and server metadata are deliberately excluded.
IDENTITY_FIELDS = ("evaluator_type", "categories", "supported_evaluation_levels", "definition")

#: Fields copied into a bundle body (everything a POST needs, nothing server-owned).
PORTABLE_FIELDS = (
    "display_name",
    "description",
    "evaluator_type",
    "categories",
    "supported_evaluation_levels",
    "definition",
    "metadata",
    "tags",
)

HASH_TAG = "castia.content_hash"
BUNDLE_SUFFIX = ".json"


def content_hash(body: dict[str, Any]) -> str:
    """Return ``"sha256:<hex>"`` over the canonical JSON of the identity fields."""
    identity = {k: body[k] for k in IDENTITY_FIELDS if body.get(k) is not None}
    if "definition" not in identity:
        raise ValueError("evaluator body has no 'definition'; cannot compute its identity")
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def portable_body(body: dict[str, Any]) -> dict[str, Any]:
    """Strip server-owned fields (id, version, timestamps) from an evaluator."""
    return {k: body[k] for k in PORTABLE_FIELDS if body.get(k) is not None}


@dataclass(frozen=True)
class EvaluatorBundle:
    """A portable, hash-identified evaluator version."""

    name: str
    version: str | None
    content_hash: str
    body: dict[str, Any]

    @classmethod
    def from_service(cls, name: str, data: dict[str, Any]) -> EvaluatorBundle:
        body = portable_body(data)
        version = data.get("version")
        return cls(
            name=name,
            version=None if version is None else str(version),
            content_hash=content_hash(body),
            body=body,
        )

    @classmethod
    def load(cls, path: str | os.PathLike) -> EvaluatorBundle:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        bundle = cls(
            name=str(data["name"]),
            version=None if data.get("version") is None else str(data["version"]),
            content_hash=str(data["content_hash"]),
            body=dict(data["body"]),
        )
        actual = content_hash(bundle.body)
        if actual != bundle.content_hash:
            raise ValueError(
                f"{path}: content_hash {bundle.content_hash} does not match body ({actual}); "
                "the bundle was edited after export -- re-export it"
            )
        return bundle

    @property
    def file_name(self) -> str:
        return f"{self.name}@{self.content_hash.removeprefix('sha256:')[:12]}{BUNDLE_SUFFIX}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "content_hash": self.content_hash,
            "body": self.body,
        }

    def write(self, out_dir: str | os.PathLike) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / self.file_name
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _version_key(entry: dict[str, Any]):
    v = str(entry.get("version", ""))
    return (0, int(v), v) if v.isdigit() else (1, 0, v)


def export_evaluator(client, name: str, version: str | None = None) -> EvaluatorBundle:
    """Fetch ``name`` at ``version`` (default: the latest) as a bundle."""
    if version is None:
        versions = client.list_evaluator_versions(name)
        if not versions:
            raise LookupError(f"evaluator {name!r} has no versions in this project")
        data = max(versions, key=_version_key)
    else:
        data = client.get_evaluator_version(name, version)
    return EvaluatorBundle.from_service(name, data)


@dataclass(frozen=True)
class ImportResult:
    name: str
    version: str
    content_hash: str
    created: bool


def _entry_hash(entry: dict[str, Any]) -> str | None:
    # Prefer the real content: a tag goes stale if someone edits the version.
    # Fall back to the tag only when a list response omits the definition.
    if entry.get("definition") is not None:
        return content_hash(entry)
    tags = entry.get("tags") or {}
    if isinstance(tags, dict) and tags.get(HASH_TAG):
        return str(tags[HASH_TAG])
    return None


def import_evaluator(client, bundle: EvaluatorBundle, *, name: str | None = None) -> ImportResult:
    """Make ``bundle`` exist in the client's project; reuse a matching version."""
    target = name or bundle.name
    for entry in client.list_evaluator_versions(target):
        if _entry_hash(entry) == bundle.content_hash and entry.get("version") is not None:
            return ImportResult(target, str(entry["version"]), bundle.content_hash, False)

    body = dict(bundle.body)
    body["tags"] = {**(body.get("tags") or {}), HASH_TAG: bundle.content_hash}
    created = client.create_evaluator_version(target, body)
    version = created.get("version")
    if version is None:
        raise RuntimeError(f"creating evaluator {target!r} returned no version: {created}")
    if created.get("definition") is not None:
        actual = content_hash(created)
        if actual != bundle.content_hash:
            raise RuntimeError(
                f"evaluator {target!r} v{version} was stored with hash {actual}, "
                f"expected {bundle.content_hash}; the service rewrote the definition"
            )
    return ImportResult(target, str(version), bundle.content_hash, True)


def find_unpinned_evaluators(suite: EvalSuite) -> list[str]:
    """Names of non-builtin evaluators in ``eval.yaml`` that carry no version."""
    return [e.name or "(unnamed)" for e in suite.evaluators if not e.builtin and not e.version]


def pin_evaluator_version(config: str | os.PathLike, name: str, version: str) -> bool:
    """Write ``version`` for evaluator ``name`` into ``eval.yaml`` (round-trip).

    A bare-string reference is promoted to ``{name, version}``. Returns False
    when the config has no evaluator with that name.
    """
    from castia.evaluation.suite import _yaml

    path = Path(config)
    yaml = _yaml()
    data = yaml.load(path.read_text(encoding="utf-8")) or {}
    value: int | str = int(version) if str(version).isdigit() else str(version)
    evaluators = data.get("evaluators") or []
    found = False
    for i, entry in enumerate(evaluators):
        if isinstance(entry, str) and entry == name:
            evaluators[i] = {"name": name, "version": value}
            found = True
        elif isinstance(entry, dict) and (entry.get("name") or entry.get("evaluator_name")) == name:
            entry["version"] = value
            found = True
    if found:
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh)
    return found

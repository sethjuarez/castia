"""Code-first deploy: derive ``azure.yaml`` protocols from the Agent.

The decorators are the single source of truth for which wire protocols this
worker speaks. This module reads the composed :class:`~castia.Agent`, keeps
only the Foundry-*publishable* protocols (``PUBLISHABLE_PROTOCOLS`` --
``activity`` / ``responses`` / ``invocations``; the local-only ``chat`` /
Chat Completions protocol is never emitted), and rewrites the ``protocols:``
lists in ``azure.yaml`` in place -- preserving every hand-authored field,
value, and comment.

``ruamel.yaml`` (the ``[deploy]`` optional dependency) is imported lazily so the
hosted runtime never needs it. Run it via ``python -m castia deploy``.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .application import PUBLISHABLE_PROTOCOLS, Agent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ruamel.yaml import YAML

DEFAULT_MANIFEST = "azure.yaml"
DEFAULT_APP = "main:app"

# Every publishable protocol currently pins to contract version 2.0.0. Kept as a
# table so a future protocol can diverge without touching the emit logic.
_PROTOCOL_VERSIONS = {
    "activity": "2.0.0",
    "responses": "2.0.0",
    "invocations": "2.0.0",
}


def _protocol_version(protocol: str) -> str:
    return _PROTOCOL_VERSIONS.get(protocol, "2.0.0")


def load_app(target: str = DEFAULT_APP) -> Agent:
    """Import ``module:attr`` (uvicorn-style, default ``main:app``).

    Importing the entrypoint runs only decorator registration -- ``Agent.run``
    is guarded behind ``if __name__ == "__main__"`` -- so this is a cheap,
    side-effect-free way to observe the composed protocol set.
    """
    module_name, sep, attr = target.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"app target must be 'module:attr', got {target!r}")

    # `python -m castia` runs from the project dir; make sure it's importable.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    module = importlib.import_module(module_name)
    app = getattr(module, attr, None)
    if app is None:
        raise AttributeError(f"{module_name!r} has no attribute {attr!r}")
    if not isinstance(app, Agent):
        raise TypeError(f"{target!r} is a {type(app).__name__}, not an Agent")
    return app


def publishable_protocols(app: Agent) -> list[str]:
    """Registered protocols Foundry can declare, in canonical wire order."""
    return [p for p in app.registered_protocols() if p in PUBLISHABLE_PROTOCOLS]


def skipped_protocols(app: Agent) -> list[str]:
    """Registered protocols that stay local-only (e.g. ``chat``)."""
    return [p for p in app.registered_protocols() if p not in PUBLISHABLE_PROTOCOLS]


@dataclass
class Plan:
    """The outcome of reconciling ``azure.yaml`` with the Agent's decorators."""

    manifest: Path
    service: str
    desired: list[str]
    skipped: list[str]
    before_service: list[str]
    after_service: list[str]
    before_endpoint: list[str] | None
    after_endpoint: list[str] | None
    changed: bool
    written: bool = False
    notes: list[str] = field(default_factory=list)


def _yaml() -> YAML:
    try:
        from ruamel.yaml import YAML
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ModuleNotFoundError(
            "The deploy generator needs ruamel.yaml. Install the build-time "
            "extra with:  pip install 'castia[deploy]'   (or: pip install "
            "ruamel.yaml)."
        ) from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # never wrap the long FOUNDRY_PROJECT_RESOURCE_ID value
    # Match azure.yaml's block style exactly so untouched nodes round-trip
    # byte-for-byte (dash indented two past its key).
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _pick_service(services: dict, app: Agent) -> str:
    if not services:
        raise ValueError("azure.yaml has no services")
    if app.name in services:
        return app.name
    if len(services) == 1:
        return next(iter(services))
    raise ValueError(
        f"agent name {app.name!r} not among services {list(services)!r}; "
        "cannot pick which service to update"
    )


def _service_protocol_items(protocols: list[str]) -> list[dict]:
    return [
        {"protocol": p, "version": _protocol_version(p)} for p in protocols
    ]


def generate_manifest(
    app: Agent,
    manifest_path: str | os.PathLike = DEFAULT_MANIFEST,
    *,
    check: bool = False,
) -> Plan:
    """Reconcile ``manifest_path`` with ``app``'s publishable protocols.

    With ``check=True`` nothing is written; the returned :class:`Plan` reports
    whether a write *would* change the file.
    """
    path = Path(manifest_path)
    yaml = _yaml()
    doc = yaml.load(path.read_text(encoding="utf-8"))

    services = doc.get("services") or {}
    service_name = _pick_service(services, app)
    service = services[service_name]

    desired = publishable_protocols(app)
    skipped = skipped_protocols(app)
    notes: list[str] = []
    if skipped:
        notes.append(
            "local-only protocol(s) not published to Foundry: "
            + ", ".join(skipped)
        )

    before_service = [
        item.get("protocol") for item in (service.get("protocols") or [])
    ]

    endpoint = service.get("agentEndpoint")
    before_endpoint: list[str] | None = None
    after_endpoint: list[str] | None = None
    if isinstance(endpoint, dict) and "protocols" in endpoint:
        before_endpoint = list(endpoint.get("protocols") or [])
        after_endpoint = list(desired)
        if "activity" not in desired and before_endpoint:
            notes.append(
                "agentEndpoint present but 'activity' is not registered; "
                "review whether the Bot Service endpoint should remain"
            )

    changed = before_service != desired or (
        before_endpoint is not None and before_endpoint != after_endpoint
    )

    plan = Plan(
        manifest=path,
        service=service_name,
        desired=desired,
        skipped=skipped,
        before_service=before_service,
        after_service=list(desired),
        before_endpoint=before_endpoint,
        after_endpoint=after_endpoint,
        changed=changed,
        notes=notes,
    )

    if check or not changed:
        return plan

    service["protocols"] = _service_protocol_items(desired)
    if after_endpoint is not None:
        endpoint["protocols"] = list(desired)
        if any(p != "activity" for p in desired):
            notes.append(
                "authorizationSchemes left as authored; a non-activity "
                "protocol may need its own scheme -- review before deploy"
            )

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(doc, handle)
    plan.written = True
    return plan

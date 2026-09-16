"""Keep capability ownership and the pre-reorganization import surface intact."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import pickle
import subprocess
import sys
from pathlib import Path

import pytest

import castia

MODULE_ALIASES = {
    "activity": "protocols.activity",
    "activity_routing": "messaging.routing",
    "application": "runtime.application",
    "auth": "hosting.auth",
    "cards": "messaging.cards",
    "connector": "messaging.connector",
    "context": "runtime.context",
    "credentials": "hosting.credentials",
    "dependencies": "runtime.dependencies",
    "deploy": "delivery.manifest",
    "dispatch": "runtime.dispatch",
    "drive": "integrations.graph.drive",
    "entities": "messaging.entities",
    "evalsuite": "evaluation.suite",
    "finetune": "finetuning.rft",
    "identity": "hosting.identity",
    "invokes": "messaging.invokes",
    "mail": "integrations.graph.mail",
    "mailbox": "integrations.graph.mailbox",
    "messages": "messaging.messages",
    "model": "inference.model",
    "observability": "observe.configuration",
    "optimization": "optimizing.config",
    "optimize": "optimizing.baseline",
    "optimizer": "optimizing.jobs",
    "server": "hosting.server",
    "streaming": "messaging.streaming",
    "surfaces": "messaging.surfaces",
    "toolbox": "integrations.toolbox",
    "tools": "inference.tools",
    "tracing": "observe.tracing",
}

PUBLIC_EXPORTS = {
    "runtime.application": ("PUBLISHABLE_PROTOCOLS", "Agent", "Router"),
    "runtime.context": ("Turn", "current_turn", "current_turn_or_none"),
    "runtime.dependencies": ("Depends",),
    "hosting.identity": (
        "AgenticIdentityError", "agentic_user_id", "require_agentic_user",
    ),
    "messaging.cards": (
        "Reaction", "action_chips", "adaptive_card", "decision_card",
        "suggested_actions",
    ),
    "messaging.entities": ("citation", "mention_entity", "sensitivity_label"),
    "messaging.invokes": (
        "InvokeNames", "card_action", "card_invoke_response",
        "feedback_payload", "message_invoke_response",
    ),
    "messaging.messages": ("Message",),
    "messaging.streaming": ("Streamer",),
    "messaging.surfaces": ("Teams",),
    "inference.model": ("Model", "get_model", "use_model"),
    "optimizing.config": (
        "AgentConfig", "apply_optimized_tools", "configured_model",
        "load_agent_config", "tools_json",
    ),
    "integrations.toolbox": (
        "apply_optimized_toolbox_tools", "compose_toolbox_endpoint",
        "knowledge_base_mcp_tool", "platform_endpoint_env",
        "resolve_toolbox_endpoint", "toolbox_mcp_tool", "toolbox_token",
    ),
    "observe.tracing": ("OperationName", "execute_tool", "invoke_agent"),
}

PACKAGE_ROOT = Path(castia.__file__).resolve().parent


def _python(code: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.parametrize(("legacy", "canonical"), MODULE_ALIASES.items())
def test_legacy_modules_are_live_aliases(legacy, canonical, monkeypatch):
    old = importlib.import_module(f"castia.{legacy}")
    new = importlib.import_module(f"castia.{canonical}")
    assert old is new
    assert getattr(castia, legacy) is new
    assert Path(new.__file__).resolve() == PACKAGE_ROOT.joinpath(
        *canonical.split(".")
    ).with_suffix(".py")

    marker = object()
    monkeypatch.setattr(f"castia.{legacy}._architecture_probe", marker, raising=False)
    assert new._architecture_probe is marker
    replacement = object()
    monkeypatch.setattr(new, "_architecture_probe", replacement)
    assert old._architecture_probe is replacement


@pytest.mark.parametrize("canonical_first", [False, True])
def test_module_aliases_work_in_either_import_order(canonical_first):
    _python(
        "import importlib, json, sys\n"
        f"aliases = json.loads({json.dumps(MODULE_ALIASES)!r})\n"
        "for legacy, canonical in aliases.items():\n"
        "    old_name, new_name = 'castia.' + legacy, 'castia.' + canonical\n"
        f"    names = (new_name, old_name) if {canonical_first!r} else (old_name, new_name)\n"
        "    first, second = (importlib.import_module(name) for name in names)\n"
        "    assert first is second, names\n"
        "    assert sys.modules[old_name] is sys.modules[new_name], names\n"
        "    assert getattr(sys.modules['castia'], legacy) is second, names\n"
    )


def test_public_exports_keep_their_identity():
    expected = {name for names in PUBLIC_EXPORTS.values() for name in names}
    assert set(castia.__all__) == expected
    for module, names in PUBLIC_EXPORTS.items():
        implementation = importlib.import_module(f"castia.{module}")
        for name in names:
            assert getattr(castia, name) is getattr(implementation, name)


@pytest.mark.parametrize(
    ("legacy", "name"),
    [
        ("activity", "Activity"),
        ("application", "Agent"),
        ("dependencies", "Depends"),
        ("model", "Model"),
        ("optimization", "AgentConfig"),
    ],
)
def test_old_pickled_global_references_still_resolve(legacy, name):
    reference = f"ccastia.{legacy}\n{name}\n.".encode("ascii")
    resolved = pickle.loads(reference)
    canonical = importlib.import_module(f"castia.{MODULE_ALIASES[legacy]}")
    assert resolved is getattr(canonical, name)


def test_root_import_does_not_load_host_clients_or_cli():
    result = _python(
        "import castia, json, sys\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    loaded = json.loads(result.stdout)
    forbidden = (
        "azure", "openai", "httpx", "fastapi", "uvicorn",
        "ruamel", "opentelemetry",
        "castia.building", "castia.delivery", "castia.evaluation",
        "castia.finetuning", "castia.lifecycle",
        "castia.optimizing.jobs", "castia.optimizing.baseline",
        "castia.observe.cli", "castia.observe.live", "castia.observe.telemetry",
    )
    assert not [
        name for name in loaded
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]


def test_root_modules_are_only_public_entrypoints_and_compatibility():
    allowed = set(MODULE_ALIASES) | {"__init__", "__main__", "_compat"}
    unexpected = {path.stem for path in PACKAGE_ROOT.glob("*.py")} - allowed
    assert not unexpected, f"Place implementation in a capability package: {unexpected}"
    for legacy in MODULE_ALIASES:
        tree = ast.parse((PACKAGE_ROOT / f"{legacy}.py").read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(tree)
        ), f"Compatibility module {legacy} contains implementation"


def test_implementation_imports_use_capability_paths():
    legacy_modules = {f"castia.{name}" for name in MODULE_ALIASES}
    violations = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if len(relative.parts) == 1 and path.stem in MODULE_ALIASES:
            continue
        parts = relative.with_suffix("").parts
        package = ".".join(("castia", *parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                origin = node.module or ""
                if node.level:
                    origin = importlib.util.resolve_name(
                        "." * node.level + origin, package
                    )
                candidates = [origin]
                if origin == "castia":
                    candidates.extend(f"{origin}.{alias.name}" for alias in node.names)
            for target in candidates:
                if target in legacy_modules:
                    violations.append(f"{relative}:{node.lineno}: {target}")
    assert not violations, "\n".join(violations)


def test_protocol_models_do_not_depend_on_host_implementations():
    forbidden = {"azure", "openai", "httpx", "fastapi", "uvicorn", "opentelemetry"}
    for path in (PACKAGE_ROOT / "protocols").rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
        package = ".".join(("castia", *relative.parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                origin = node.module or ""
                if node.level:
                    origin = importlib.util.resolve_name(
                        "." * node.level + origin, package
                    )
                targets = [origin]
            for target in targets:
                assert target.split(".")[0] not in forbidden, (relative, target)
                if target == "castia" or target.startswith("castia."):
                    assert target.startswith("castia.protocols"), (relative, target)

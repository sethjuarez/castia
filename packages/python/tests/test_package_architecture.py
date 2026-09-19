"""Keep the package root clean and enforce canonical capability imports."""

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

MODULE_PATHS = {
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


@pytest.mark.parametrize(("removed", "canonical"), MODULE_PATHS.items())
def test_only_canonical_modules_exist(removed, canonical):
    new = importlib.import_module(f"castia.{canonical}")
    assert Path(new.__file__).resolve() == PACKAGE_ROOT.joinpath(
        *canonical.split(".")
    ).with_suffix(".py")
    assert importlib.util.find_spec(f"castia.{removed}") is None
    assert removed not in vars(castia)
    with pytest.raises(ModuleNotFoundError) as error:
        importlib.import_module(f"castia.{removed}")
    assert error.value.name == f"castia.{removed}"


def test_fresh_process_has_no_legacy_module_aliases():
    _python(
        "import importlib, json, sys\n"
        f"paths = json.loads({json.dumps(MODULE_PATHS)!r})\n"
        "for legacy, canonical in paths.items():\n"
        "    old_name, new_name = 'castia.' + legacy, 'castia.' + canonical\n"
        "    importlib.import_module(new_name)\n"
        "    assert old_name not in sys.modules, old_name\n"
        "    assert importlib.util.find_spec(old_name) is None, old_name\n"
        "    assert legacy not in vars(sys.modules['castia']), old_name\n"
    )


def test_public_exports_keep_their_identity():
    expected = {name for names in PUBLIC_EXPORTS.values() for name in names}
    assert set(castia.__all__) == expected
    for module, names in PUBLIC_EXPORTS.items():
        implementation = importlib.import_module(f"castia.{module}")
        for name in names:
            assert getattr(castia, name) is getattr(implementation, name)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("protocols.activity", "Activity"),
        ("runtime.application", "Agent"),
        ("runtime.dependencies", "Depends"),
        ("inference.model", "Model"),
        ("optimizing.config", "AgentConfig"),
    ],
)
def test_pickled_globals_use_canonical_modules(module, name):
    canonical = importlib.import_module(f"castia.{module}")
    value = getattr(canonical, name)
    assert value.__module__ == f"castia.{module}"
    assert pickle.loads(pickle.dumps(value)) is value


def test_root_import_does_not_load_host_clients_or_cli():
    result = _python(
        "import castia, json, sys\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    loaded = json.loads(result.stdout)
    forbidden = (
        "azure", "openai", "httpx", "fastapi", "uvicorn",
        "prompty", "ruamel", "opentelemetry",
        "castia.building", "castia.delivery", "castia.evaluation",
        "castia.finetuning", "castia.lifecycle",
        "castia.optimizing.jobs", "castia.optimizing.baseline",
        "castia.observe.cli", "castia.observe.live", "castia.observe.telemetry",
    )
    assert not [
        name for name in loaded
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]


def test_root_contains_only_entrypoints_and_type_marker():
    assert {path.name for path in PACKAGE_ROOT.iterdir() if path.is_file()} == {
        "__init__.py", "__main__.py", "py.typed",
    }


def test_implementation_imports_use_capability_paths():
    legacy_modules = {f"castia.{name}" for name in MODULE_PATHS}
    violations = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
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
                    assert target == "castia.protocols" or target.startswith(
                        "castia.protocols."
                    ), (relative, target)

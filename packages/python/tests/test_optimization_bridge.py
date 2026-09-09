"""Unit tests for the optimizer config bridge (``castia.optimization``).

``load_agent_config`` is best-effort and non-fatal: it prefers the optimizer's
``load_config()`` resolution but degrades to environment defaults whenever the
package is absent, resolution raises, or no config is found -- so an agent runs
identically with or without the optimizer installed. ``configured_model`` threads
the resolved ``model`` + ``instructions`` into the built :class:`~castia.Model`.

Everything here is hermetic: the ``azure.ai.agentserver.optimization`` module is
faked via ``sys.modules`` and ``Model`` is stubbed, so no Azure/network is used.
"""

from __future__ import annotations

import sys
import types

from castia.optimization import AgentConfig, configured_model, load_agent_config

_OPT_MODULE = "azure.ai.agentserver.optimization"


def _install_load_config(monkeypatch, fn) -> None:
    module = types.ModuleType(_OPT_MODULE)
    module.load_config = fn
    monkeypatch.setitem(sys.modules, _OPT_MODULE, module)


class _FakeConfig:
    def __init__(self, instructions, model, source, tool_definitions=()):
        self._instructions = instructions
        self.model = model
        self.source = source
        self.tool_definitions = tool_definitions

    def compose_instructions(self):
        return self._instructions


# --------------------------------------------------------------------------- #
# load_agent_config -- resolution + graceful fallback                          #
# --------------------------------------------------------------------------- #


def test_package_absent_degrades_to_env_default(monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env")
    # Force the import to fail even if the extra happens to be installed.
    monkeypatch.setitem(sys.modules, _OPT_MODULE, None)
    assert load_agent_config() == AgentConfig("gpt-env", None, "default")


def test_load_config_none_degrades_to_default(monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env")
    _install_load_config(monkeypatch, lambda config_dir=None: None)
    assert load_agent_config() == AgentConfig("gpt-env", None, "default")


def test_load_config_raises_degrades_to_default(monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env")

    def _boom(config_dir=None):
        raise RuntimeError("resolver exploded")

    _install_load_config(monkeypatch, _boom)
    assert load_agent_config() == AgentConfig("gpt-env", None, "default")


def test_config_present_maps_fields(monkeypatch):
    cfg = _FakeConfig(
        instructions="be terse",
        model="gpt-candidate",
        source="env:OPTIMIZATION_CONFIG",
        tool_definitions=[{"function": {"name": "a"}}],
    )
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)
    resolved = load_agent_config()
    assert resolved.model == "gpt-candidate"
    assert resolved.instructions == "be terse"
    assert resolved.source == "env:OPTIMIZATION_CONFIG"
    assert resolved.tool_definitions == ({"function": {"name": "a"}},)


def test_empty_instructions_become_none(monkeypatch):
    cfg = _FakeConfig(instructions="", model="gpt-x", source="local")
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)
    assert load_agent_config().instructions is None


def test_missing_model_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env")
    cfg = _FakeConfig(instructions="x", model=None, source="local")
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)
    assert load_agent_config().model == "gpt-env"


def test_config_dir_is_forwarded(monkeypatch):
    seen = {}

    def _capture(config_dir=None):
        seen["config_dir"] = config_dir

    _install_load_config(monkeypatch, _capture)
    load_agent_config("/anchored/.agent_configs")
    assert seen["config_dir"] == "/anchored/.agent_configs"


# --------------------------------------------------------------------------- #
# configured_model -- threads model + instructions into Model                  #
# --------------------------------------------------------------------------- #


def test_configured_model_threads_model_and_instructions(monkeypatch):
    captured = {}

    class _FakeModel:
        def __init__(self, deployment=None, *, endpoint=None, instructions=None):
            captured["deployment"] = deployment
            captured["instructions"] = instructions

    monkeypatch.setattr("castia.model.Model", _FakeModel)
    provider = configured_model(AgentConfig("gpt-4o", "be nice", "local"))
    model = provider()
    assert isinstance(model, _FakeModel)
    assert captured == {"deployment": "gpt-4o", "instructions": "be nice"}

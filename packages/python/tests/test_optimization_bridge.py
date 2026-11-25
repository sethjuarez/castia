"""Unit tests for the optimizer config bridge (``castia.optimizing.config``).

``load_agent_config`` prefers the optimizer's ``load_config()`` resolution and
degrades to environment defaults when the package is absent or no config is
found. A config source that is present but unusable raises
``CandidateConfigError`` unless lenient mode is selected. ``configured_model`` threads
the resolved ``model`` + ``instructions`` into the built :class:`~castia.Model`.

Everything here is hermetic: the ``azure.ai.agentserver.optimization`` module is
faked via ``sys.modules`` and ``Model`` is stubbed, so no Azure/network is used.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from castia.optimizing.config import (
    AgentConfig,
    CandidateConfigError,
    configured_model,
    load_agent_config,
)

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


def test_load_config_raises_is_strict_unless_lenient(monkeypatch):
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-env")

    def _boom(config_dir=None):
        raise ValueError("Invalid metadata file")

    _install_load_config(monkeypatch, _boom)
    with pytest.raises(CandidateConfigError, match="Invalid metadata file"):
        load_agent_config()
    assert load_agent_config(strict=False) == AgentConfig("gpt-env", None, "default")
    monkeypatch.setenv("CASTIA_OPTIMIZATION_LENIENT", "1")
    assert load_agent_config() == AgentConfig("gpt-env", None, "default")


@pytest.mark.parametrize(
    ("raw", "message"),
    [("{not json", "not valid JSON"), ("{}", "non-empty JSON object"), ("  ", "blank"),
     ('{"model": "gpt-4o"}', "non-empty instructions")],
)
def test_malformed_inline_config_is_rejected(monkeypatch, raw, message):
    monkeypatch.setenv("OPTIMIZATION_CONFIG", raw)
    _install_load_config(monkeypatch, lambda config_dir=None: None)
    with pytest.raises(CandidateConfigError, match=message):
        load_agent_config()


def test_unreadable_tools_file_is_rejected(monkeypatch, tmp_path):
    (tmp_path / "tools.json").write_text("{broken", encoding="utf-8")
    cfg = _FakeConfig("x", "gpt-4o", f"local:{tmp_path}")
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)
    with pytest.raises(CandidateConfigError, match="invalid tools file"):
        load_agent_config()


def test_candidate_fallback_warns_with_hash_and_fails_when_strict(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("OPTIMIZATION_CANDIDATE_ID", "cand_1")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    cfg = _FakeConfig("base prompt", "gpt-4o", f"local:{baseline}")
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)

    with caplog.at_level(logging.WARNING, logger="castia.optimizing.config"):
        resolved = load_agent_config()
    assert resolved.source == f"local:{baseline}"
    assert len(resolved.config_hash) == 12
    assert "'cand_1' did not resolve" in caplog.text and resolved.config_hash in caplog.text

    with pytest.raises(CandidateConfigError, match="did not resolve"):
        load_agent_config(strict=True)
    monkeypatch.setenv("CASTIA_OPTIMIZATION_STRICT", "1")
    with pytest.raises(CandidateConfigError):
        load_agent_config()


def test_resolved_candidate_without_instructions_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIMIZATION_CANDIDATE_ID", "cand_1")
    candidate = tmp_path / "cand_1"
    candidate.mkdir()
    cfg = _FakeConfig("", "gpt-4o", f"local:{candidate}")
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)
    with pytest.raises(CandidateConfigError, match="has no instructions"):
        load_agent_config(strict=True)
    with pytest.raises(CandidateConfigError, match="has no instructions"):
        load_agent_config()


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


def test_azd_tools_file_metadata_is_loaded_when_package_ignores_it(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "metadata.yaml").write_text(
        "model: gpt-4o\ninstruction_file: instructions.md\ntools_file: tools.json\n",
        encoding="utf-8",
    )
    (baseline / "tools.json").write_text(
        '[{"type":"function","function":{"name":"web","description":"Search."}}]',
        encoding="utf-8",
    )
    cfg = _FakeConfig(
        instructions="be terse",
        model="gpt-candidate",
        source=f"local:{baseline}",
        tool_definitions=[],
    )
    _install_load_config(monkeypatch, lambda config_dir=None: cfg)

    assert load_agent_config(tmp_path).tool_definitions == (
        {"type": "function", "function": {"name": "web", "description": "Search."}},
    )


def test_config_dir_is_forwarded(monkeypatch):
    seen = {}

    def _capture(config_dir=None):
        seen["config_dir"] = config_dir

    _install_load_config(monkeypatch, _capture)
    load_agent_config("/anchored/.agent_configs")
    assert seen["config_dir"] == "/anchored/.agent_configs"


# --------------------------------------------------------------------------- #
# configured_model -- threads optimizer config into Model                      #
# --------------------------------------------------------------------------- #


def test_configured_model_threads_model_instructions_and_tools(monkeypatch):
    captured = {}

    class _FakeModel:
        def __init__(
            self,
            deployment=None,
            *,
            endpoint=None,
            instructions=None,
            tool_definitions=(),
        ):
            captured["deployment"] = deployment
            captured["instructions"] = instructions
            captured["tool_definitions"] = tool_definitions

    monkeypatch.setattr("castia.inference.model.Model", _FakeModel)
    defs = ({"function": {"name": "search", "description": "Search."}},)
    provider = configured_model(AgentConfig("gpt-4o", "be nice", "local", defs))
    model = provider()
    assert isinstance(model, _FakeModel)
    assert captured == {
        "deployment": "gpt-4o",
        "instructions": "be nice",
        "tool_definitions": defs,
    }


def test_injected_candidate_overrides_earlier_resolution(monkeypatch):
    """No process-level cache: a candidate injected after a baseline load wins."""
    import json
    import os

    def _env_aware(config_dir=None):
        raw = os.environ.get("OPTIMIZATION_CONFIG")
        if raw:
            data = json.loads(raw)
            return _FakeConfig(data["instructions"], data["model"], "env:OPTIMIZATION_CONFIG")
        return _FakeConfig("baseline prompt", "gpt-4o", "local:.agent_configs/baseline")

    _install_load_config(monkeypatch, _env_aware)
    startup = load_agent_config()
    assert startup.instructions == "baseline prompt"

    monkeypatch.setenv(
        "OPTIMIZATION_CONFIG", json.dumps({"instructions": "candidate prompt", "model": "gpt-5-mini"})
    )
    injected = load_agent_config()
    assert (injected.instructions, injected.model, injected.source) == (
        "candidate prompt", "gpt-5-mini", "env:OPTIMIZATION_CONFIG"
    )
    assert injected.config_hash != startup.config_hash

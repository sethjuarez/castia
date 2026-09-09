"""Conformance test: the optimizer candidate-injection precedence fixture.

`spec/conformance/optimization/candidate_precedence.json` is the language-neutral
golden for how a hosted agent resolves its active configuration (baseline vs.
optimizer-injected candidate). The resolution itself lives in Foundry's runtime,
not in castia, so this test locks the *documented contract* -- the first-wins
ordering and the `config_dir`-affects-only-local rule -- so it cannot silently
rot as other SDKs are added.
"""

from __future__ import annotations

import json
from pathlib import Path

# The language-neutral golden lives in the monorepo's spec/ tree; the SDK docs
# and this test read the same fixture so the contract cannot drift.
SPEC_PRECEDENCE = (
    Path(__file__).resolve().parents[3]
    / "spec"
    / "conformance"
    / "optimization"
    / "candidate_precedence.json"
)

# The cross-SDK contract: first-wins order of configuration sources.
EXPECTED_ORDER = ["inline_config", "resolver_api", "local_agent_configs", "none"]


def _load() -> dict:
    return json.loads(SPEC_PRECEDENCE.read_text(encoding="utf-8"))


def test_fixture_exists_and_parses():
    data = _load()
    assert data["contract"] == "foundry-agent-optimizer-candidate-injection"
    assert isinstance(data["precedence"], list)


def test_precedence_is_first_wins_in_documented_order():
    ids = [entry["id"] for entry in _load()["precedence"]]
    assert ids == EXPECTED_ORDER


def test_only_local_source_is_affected_by_config_dir():
    affected = {
        entry["id"]
        for entry in _load()["precedence"]
        if entry.get("affected_by_config_dir")
    }
    assert affected == {"local_agent_configs"}


def test_terminal_source_degrades_to_defaults():
    terminal = _load()["precedence"][-1]
    assert terminal["id"] == "none"
    assert terminal["source"] is None


def test_resolver_api_requires_both_env_vars():
    resolver = next(
        e for e in _load()["precedence"] if e["id"] == "resolver_api"
    )
    assert set(resolver["env"]) == {
        "OPTIMIZATION_CANDIDATE_ID",
        "OPTIMIZATION_RESOLVE_ENDPOINT",
    }

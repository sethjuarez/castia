"""Unit tests for the optimizer-baseline generator (``castia.optimize``).

Hermetic: builds an Agent in-memory and reconciles a temp ``.agent_configs``
directory. Proves drift detection (missing/stale ``tools.json`` and the
``tool_file`` pointer), that a write makes ``--check`` clean, and that an agent
with no declared tools reports tool optimization as inactive without churning.
"""

from __future__ import annotations

import json

from castia import Agent
from castia.optimize import generate_optimizer_config
from castia.tools import Tool


def _tool(name: str) -> Tool:
    async def _impl(activity, **kwargs):  # pragma: no cover
        return {}

    return Tool(name=name, description=f"desc for {name}", parameters={"type": "object"}, impl=_impl)


def _agent_with_tools() -> Agent:
    app = Agent(name="t")
    app.tools(lambda: [_tool("send_email"), _tool("read_inbox")])
    return app


def _write_metadata(config_dir):
    baseline = config_dir / "baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    (baseline / "metadata.yaml").write_text(
        "model: gpt-4o\ninstruction_file: instructions.md\n", encoding="utf-8"
    )
    (baseline / "instructions.md").write_text("You are helpful.\n", encoding="utf-8")


def test_check_reports_drift_when_tools_json_missing(tmp_path):
    _write_metadata(tmp_path)
    plan = generate_optimizer_config(_agent_with_tools(), tmp_path, check=True)
    assert plan.changed
    assert plan.tools_changed
    assert plan.metadata_changed  # tool_file pointer not yet present
    assert not plan.tools_path.exists()  # check writes nothing


def test_write_then_check_is_clean(tmp_path):
    _write_metadata(tmp_path)
    written = generate_optimizer_config(_agent_with_tools(), tmp_path)
    assert written.written

    data = json.loads(written.tools_path.read_text(encoding="utf-8"))
    assert [d["function"]["name"] for d in data] == ["send_email", "read_inbox"]

    meta = (tmp_path / "baseline" / "metadata.yaml").read_text(encoding="utf-8")
    assert "tool_file: tools.json" in meta

    again = generate_optimizer_config(_agent_with_tools(), tmp_path, check=True)
    assert not again.changed


def test_no_tools_reports_inactive_without_drift(tmp_path):
    _write_metadata(tmp_path)
    plan = generate_optimizer_config(Agent(name="t"), tmp_path, check=True)
    assert not plan.changed
    assert any("no tools declared" in n for n in plan.notes)


def test_missing_metadata_is_noted(tmp_path):
    plan = generate_optimizer_config(_agent_with_tools(), tmp_path, check=True)
    assert any("metadata.yaml" in n for n in plan.notes)

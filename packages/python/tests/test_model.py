from __future__ import annotations

from castia.inference.model import _public_tool_spec


def test_public_tool_spec_strips_castia_private_metadata() -> None:
    spec = {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
        "x-castia-optimizer-tool-definitions": [{"function": {"name": "x"}}],
    }

    assert _public_tool_spec(spec) == {
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
    }


def test_deployment_mismatch_warns_once(monkeypatch, caplog) -> None:
    from castia.inference import model

    monkeypatch.setattr(model, "_warned_deployment_mismatches", set())
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    with caplog.at_level("WARNING", logger=model._logger.name):
        model._warn_deployment_mismatch("gpt-4.1-mini")
        assert caplog.text == ""
        model._warn_deployment_mismatch("gpt-5")
        model._warn_deployment_mismatch("gpt-5")
    assert caplog.text.count("differs from AZURE_AI_MODEL_DEPLOYMENT_NAME") == 1

    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    caplog.clear()
    model._warn_deployment_mismatch("other")
    assert caplog.text == ""

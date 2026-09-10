from __future__ import annotations

from castia.model import _public_tool_spec


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

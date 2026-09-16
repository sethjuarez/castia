"""Execute the consumer guide's application and tests without cloud access."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from castia.__main__ import main
from castia.building import scaffold_project
from castia.inference.model import Model

GUIDE = Path(__file__).resolve().parents[1] / "AGENTS.md"


def example(name: str) -> str:
    pattern = rf"<!-- example: {re.escape(name)} -->\n```python\n(.*?)\n```"
    match = re.search(pattern, GUIDE.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"Missing executable guide example {name}"
    return match.group(1) + "\n"


@pytest.fixture
def consumer(monkeypatch):
    original = Path.cwd()
    root = original / f".agent-guide-test-{uuid4().hex}"
    for name in tuple(os.environ):
        if name.startswith(("OPTIMIZATION_", "TOOLBOX_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/example",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    monkeypatch.setenv("TOOLBOX_ENDPOINT", "https://toolbox.invalid/mcp")
    scaffold_project(root)
    try:
        (root / "main.py").write_text(example("responses-agent"), encoding="utf-8")
        (root / "tests" / "test_agent.py").write_text(
            example("responses-test"), encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("main", root / "main.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "main", module)
        spec.loader.exec_module(module)
        yield root, module
    finally:
        monkeypatch.chdir(original)
        shutil.rmtree(root)


def test_documented_offline_project_commands(consumer, monkeypatch):
    root, module = consumer
    monkeypatch.chdir(root)
    assert module.app.registered_protocols() == ["responses"]
    for argv in (
        ["deploy", "--app", "main:app"],
        ["optimize", "--app", "main:app"],
        ["build", "check", "."],
        ["build", "test", ".", "--timeout", "60"],
        ["eval", "check", "--config", "eval.yaml"],
    ):
        assert main(argv) == 0, argv
    tools = json.loads(
        (root / ".agent_configs" / "baseline" / "tools.json").read_text(encoding="utf-8")
    )
    assert tools[0]["function"]["name"] == "web"
    assert set(tools[0]["function"]["parameters"]["properties"]) == {"search_query"}
    assert "Authorization" not in json.dumps(tools)


@pytest.mark.parametrize("with_toolbox", [False, True])
def test_documented_handler_keeps_native_mcp(consumer, monkeypatch, with_toolbox):
    _, module = consumer
    if not with_toolbox:
        monkeypatch.delenv("TOOLBOX_ENDPOINT")
    token = AsyncMock(return_value="offline-test-token")
    monkeypatch.setattr(module, "toolbox_token", token)
    create = AsyncMock(return_value=SimpleNamespace(
        output=[SimpleNamespace(type="mcp_call")], output_text="verified",
    ))
    model = Model.__new__(Model)
    model._client = SimpleNamespace(
        get_openai_client=lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    model._deployment = "gpt-4o"
    model._instructions = None
    model._tool_definitions = ()
    model._reasoning = {}

    assert asyncio.run(module.reply("hello", model=model)) == "verified"
    create.assert_awaited_once()
    specs = create.call_args.kwargs["tools"]
    if with_toolbox:
        token.assert_awaited_once()
        assert len(specs) == 1
        assert specs[0]["type"] == "mcp"
        assert specs[0]["allowed_tools"] == ["web"]
        assert "search_query" in specs[0]["server_description"]
        assert not any(key.startswith("x-castia-") for key in specs[0])
        assert specs[0]["headers"]["Authorization"] == "Bearer offline-test-token"
    else:
        token.assert_not_awaited()
        assert specs == []


def test_documented_provider_anchors_baseline(consumer, monkeypatch):
    root, module = consumer
    captured = {}

    def model(deployment, **kwargs):
        captured.update(deployment=deployment, **kwargs)
        return captured

    monkeypatch.setattr("castia.inference.model.Model", model)
    assert module.model_provider() is captured
    assert captured["deployment"] == "gpt-4o"
    assert captured["instructions"] == (
        root / ".agent_configs" / "baseline" / "instructions.md"
    ).read_text(encoding="utf-8").strip()

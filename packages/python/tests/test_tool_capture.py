from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from castia import Agent
from castia.__main__ import main
from castia.building import AgentTestHarness
from castia.inference.model import Model
from castia.observe import tool_capture
from castia.runtime.request_context import FOUNDRY_CALL_ID_HEADER

BIG = "x" * 6000  # larger than dev diagnostics' 500-char summaries


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for name in (
        tool_capture.MODE_ENV,
        tool_capture.DIR_ENV,
        "CASTIA_TOOL_CAPTURE_MAX_RESULT_BYTES",
        "CASTIA_TOOL_CAPTURE_MAX_CALLS",
    ):
        monkeypatch.delenv(name, raising=False)
    tool_capture.reset_tool_capture()
    yield
    tool_capture.reset_tool_capture()


class _Tool:
    def __init__(self, name="lookup_policy", *, fail=False):
        self.name = name
        self.fail = fail

    def spec(self):
        return {"type": "function", "function": {"name": self.name}}

    async def run(self, activity, **kwargs):
        if self.fail:
            raise RuntimeError("backend down")
        return {"ok": True, "content": BIG, "policy": kwargs.get("policy")}


def _agent(tools, *, server_items=()):
    class Responses:
        calls = 0

        async def create(self, **kwargs):
            Responses.calls += 1
            if Responses.calls == 1:
                return SimpleNamespace(
                    output=[
                        *server_items,
                        *(
                            SimpleNamespace(
                                type="function_call",
                                call_id=f"call-{i}",
                                name=tool.name,
                                arguments='{"policy":"travel"}',
                            )
                            for i, tool in enumerate(tools)
                        ),
                    ],
                    output_text="",
                )
            return SimpleNamespace(output=[SimpleNamespace(type="message")], output_text="done")

    async def reply(text: str) -> str:
        model = Model.__new__(Model)
        model._deployment = "gpt-4o"
        model._instructions = None
        model._tool_definitions = ()
        model._reasoning = {}
        responses = Responses()
        model._client = SimpleNamespace(
            get_openai_client=lambda: SimpleNamespace(responses=responses)
        )
        tool_capture.annotate_tool_capture(eval_row="row-7")
        return await model.respond_with_tools(text, tools=tools, activity=None)

    app = Agent()
    app.responses()(reply)
    return app


def _post(app, *, stream=False):
    async def run():
        async with AgentTestHarness(app) as test:
            response = await test.client.post(
                "/responses",
                json={"input": "Can I travel?", "stream": stream, "metadata": {"eval_id": "e1"}},
                headers={FOUNDRY_CALL_ID_HEADER: "platform-1"},
            )
            assert response.status_code == 200
            return response

    return asyncio.run(run())


def test_disabled_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    body = _post(_agent([_Tool()])).json()
    assert body["output_text"] == "done"
    assert not (tmp_path / ".castia").exists()
    assert tool_capture.tool_capture_health()["enabled"] is False


def test_file_capture_records_full_result_and_join_keys(tmp_path, monkeypatch):
    monkeypatch.setenv(tool_capture.MODE_ENV, "file")
    monkeypatch.setenv(tool_capture.DIR_ENV, str(tmp_path))
    body = _post(_agent([_Tool()])).json()

    documents, skipped = tool_capture.load_tool_captures(tmp_path)
    assert skipped == 0
    [doc] = tool_capture.match_tool_captures(documents, response_id=body["id"], metadata={"eval_id": "e1"})
    assert doc["schema"] == "castia.tool_capture/v1"
    assert doc["platform_call_id"] == "platform-1"
    assert doc["provenance"] == {"eval_row": "row-7"}
    assert doc["complete"] is True and doc["status"] == "ok"
    [call] = doc["tool_calls"]
    assert call["call_id"] == "call-0" and call["source"] == "local"
    assert call["arguments"] == {"policy": "travel"}
    assert call["result"]["content"] == BIG
    assert call["tool_reported_ok"] is True
    assert tool_capture.verify_tool_capture(doc)["status"] == "verified"
    assert tool_capture.tool_capture_health()["persisted"] == 1


def test_streaming_turn_is_captured(tmp_path):
    seen = []
    tool_capture.configure_tool_capture(sink=seen.append)
    response = _post(_agent([_Tool()]), stream=True)
    completed = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {") and '"response.completed"' in line
    ]
    [doc] = seen
    assert doc["response_id"] == completed[0]["response"]["id"]
    assert doc["tool_calls"][0]["result"]["content"] == BIG


def test_tool_error_is_captured_and_model_still_gets_error(tmp_path):
    seen = []
    tool_capture.configure_tool_capture(sink=seen.append)
    assert _post(_agent([_Tool(fail=True)])).json()["output_text"] == "done"
    [call] = seen[0]["tool_calls"]
    assert call["status"] == "error"
    assert call["error"]["type"] == "RuntimeError"
    assert "backend down" in call["error"]["message"]


def test_sink_failure_never_breaks_response():
    def broken(document):
        raise OSError("disk full")

    tool_capture.configure_tool_capture(sink=broken)
    assert _post(_agent([_Tool()])).json()["output_text"] == "done"
    health = tool_capture.tool_capture_health()
    assert health["failed"] == 1 and health["last_error_type"] == "OSError"


def test_limits_mark_capture_incomplete(monkeypatch):
    monkeypatch.setenv("CASTIA_TOOL_CAPTURE_MAX_RESULT_BYTES", "100")
    monkeypatch.setenv("CASTIA_TOOL_CAPTURE_MAX_CALLS", "1")
    seen = []
    tool_capture.configure_tool_capture(sink=seen.append)
    _post(_agent([_Tool("a"), _Tool("b")]))
    [doc] = seen
    assert doc["complete"] is False
    assert doc["omitted_call_count"] == 1
    [call] = doc["tool_calls"]
    assert call["result_truncated"] is True and "result" not in call
    assert call["result_bytes"] > 100
    verification = tool_capture.verify_tool_capture(doc)
    assert verification["status"] == "verified"
    assert verification["tool_calls"][0]["result"] == "unavailable"


def test_server_mcp_call_items_are_captured():
    seen = []
    tool_capture.configure_tool_capture(sink=seen.append)
    mcp = SimpleNamespace(
        type="mcp_call", id="mcp-1", name="knowledge_base_retrieve",
        server_label="toolbox", arguments='{"q":"travel"}', output=BIG, error=None,
    )
    _post(_agent([], server_items=[mcp]))
    [call] = seen[0]["tool_calls"]
    assert call["source"] == "server" and call["tool_type"] == "mcp_call"
    assert call["arguments"] == {"q": "travel"} and call["result"] == BIG


def test_file_reader_keeps_unicode_line_separators_in_payloads(tmp_path):
    sink = tool_capture.file_capture_sink(tmp_path)

    async def run():
        for text in ("a\u2028b\u0085c", "second"):
            async with tool_capture.tool_capture_scope(sink=sink):
                handle = tool_capture.begin_tool_call(name="scrape", arguments={})
                tool_capture.finish_tool_call(handle, result={"text": text})

    asyncio.run(run())
    documents, skipped = tool_capture.load_tool_captures(tmp_path)
    assert skipped == 0
    assert [d["tool_calls"][0]["result"]["text"] for d in documents] == ["a\u2028b\u0085c", "second"]


def test_tamper_is_detected_and_cli_reports_it(tmp_path, capsys):
    tool_capture.configure_tool_capture(sink=tool_capture.file_capture_sink(tmp_path))
    body = _post(_agent([_Tool()])).json()
    [path] = list(Path(tmp_path).glob("*.jsonl"))

    assert main(["observe", "tool-captures", str(path), "--response-id", body["id"], "--summary-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["record_count"] == 1 and "records" not in report

    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["tool_calls"][0]["result"]["content"] = "forged"
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    assert tool_capture.verify_tool_capture(doc)["tool_calls"][0]["result"] == "failed"
    assert main(["observe", "tool-captures", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["integrity_failed"] == 1

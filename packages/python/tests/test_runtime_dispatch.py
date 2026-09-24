import asyncio
from contextlib import contextmanager

from castia import Depends
from castia.observe.tracing import clear_trace_sinks, register_trace_sink
from castia.runtime import dispatch as runtime_dispatch


class FakeSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


def test_return_dispatch_records_input_output_when_content_recording_enabled(monkeypatch):
    span = FakeSpan()
    ambient_span = FakeSpan()

    @contextmanager
    def fake_invoke_agent():
        yield span

    async def reply(text: str) -> str:
        return f"answer: {text}"

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(runtime_dispatch, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(runtime_dispatch, "_ambient_span", lambda: ambient_span)
    monkeypatch.setattr(runtime_dispatch, "flush_telemetry", lambda: None)

    result = asyncio.run(runtime_dispatch.make_return_dispatch(reply)("hello"))

    assert result == "answer: hello"
    assert span.attributes["gen_ai.input.messages"] == '[{"role": "user", "content": "hello"}]'
    assert span.attributes["gen_ai.output.messages"] == '[{"role": "assistant", "content": "answer: hello"}]'
    assert ambient_span.attributes["gen_ai.input.messages"] == '[{"role": "user", "content": "hello"}]'
    assert ambient_span.attributes["gen_ai.output.messages"] == '[{"role": "assistant", "content": "answer: hello"}]'


def test_return_dispatch_omits_content_by_default(monkeypatch):
    span = FakeSpan()

    @contextmanager
    def fake_invoke_agent():
        yield span

    async def reply(text: str) -> str:
        return f"answer: {text}"

    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)
    monkeypatch.setattr(runtime_dispatch, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(runtime_dispatch, "flush_telemetry", lambda: None)

    assert asyncio.run(runtime_dispatch.make_return_dispatch(reply)("hello")) == "answer: hello"
    assert "gen_ai.input.messages" not in span.attributes
    assert "gen_ai.output.messages" not in span.attributes


def test_stream_dispatch_records_joined_output(monkeypatch):
    span = FakeSpan()

    @contextmanager
    def fake_invoke_agent():
        yield span

    async def reply(text: str):
        yield text
        yield " done"

    async def run():
        return [chunk async for chunk in runtime_dispatch.make_stream_dispatch(reply)("hello")]

    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")
    monkeypatch.setattr(runtime_dispatch, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(runtime_dispatch, "flush_telemetry", lambda: None)

    assert asyncio.run(run()) == ["hello", " done"]
    assert span.attributes["gen_ai.input.messages"] == '[{"role": "user", "content": "hello"}]'
    assert span.attributes["gen_ai.output.messages"] == '[{"role": "assistant", "content": "hello done"}]'


def test_return_dispatch_emits_local_trace_steps(monkeypatch):
    records = []

    @contextmanager
    def fake_invoke_agent():
        yield FakeSpan()

    async def suffix():
        return "!"

    async def reply(text: str, marker=Depends(suffix)) -> str:
        return f"{text}{marker}"

    monkeypatch.setattr(runtime_dispatch, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(runtime_dispatch, "_ambient_span", lambda: FakeSpan())
    monkeypatch.setattr(runtime_dispatch, "flush_telemetry", lambda: None)
    register_trace_sink("memory", records.append)
    try:
        result = asyncio.run(runtime_dispatch.make_return_dispatch(reply)("hello"))
    finally:
        clear_trace_sinks()

    assert result == "hello!"
    assert [record.name for record in records] == [
        "castia.dependency.resolve",
        "castia.handler.invoke",
        "castia.turn.dispatch",
    ]
    dependency, handler, turn = records
    assert dependency.parent_id == turn.span_id
    assert handler.parent_id == turn.span_id
    assert turn.attributes["castia.protocol"] == "wire.return"
    assert turn.attributes["castia.turn.input_length"] == 5
    assert turn.attributes["castia.turn.output_length"] == 6
    assert dependency.attributes["castia.dependency.parameter"] == "marker"
    assert dependency.attributes["castia.dependency.name"].endswith("suffix")
    assert handler.attributes["castia.handler"].endswith("reply")

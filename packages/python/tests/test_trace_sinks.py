from __future__ import annotations

import asyncio
import json
import math

import pytest

from castia.observe.tracing import (
    TraceRecord,
    clear_trace_sinks,
    jsonl_trace_sink,
    register_trace_sink,
    registered_trace_sinks,
    remove_trace_sink,
    trace_attribute,
    trace_step,
)


@pytest.fixture(autouse=True)
def trace_sinks_are_isolated():
    clear_trace_sinks()
    yield
    clear_trace_sinks()


def test_trace_step_emits_nested_records_in_completion_order():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)

    with trace_step("turn", kind="agent", attributes={"safe": {"count": 1}}):
        assert trace_attribute("phase", "start") is True
        with trace_step("tool", kind="tool"):
            assert trace_attribute("tool.name", "lookup") is True

    assert [record.name for record in records] == ["tool", "turn"]
    tool, turn = records
    assert turn.trace_id == tool.trace_id
    assert tool.parent_id == turn.span_id
    assert turn.parent_id is None
    assert turn.kind == "agent"
    assert turn.status == "ok"
    assert turn.attributes["safe"] == {"count": 1}
    assert turn.attributes["phase"] == "start"
    assert tool.attributes["tool.name"] == "lookup"
    assert turn.duration_ms >= 0
    assert not trace_attribute("outside", "ignored")


def test_trace_step_decorator_records_function_metadata(monkeypatch):
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true")

    @trace_step("compute", kind="decision", include_args=True, include_result=True)
    def compute(value: int) -> dict[str, int]:
        return {"value": value}

    assert compute(3) == {"value": 3}

    assert len(records) == 1
    record = records[0]
    assert record.name == "compute"
    assert record.kind == "decision"
    assert record.attributes["code.function"].endswith("compute")
    assert record.attributes["args"] == [3]
    assert record.attributes["kwargs"] == {}
    assert record.attributes["result"] == {"value": 3}


def test_trace_step_async_decorator_records_errors():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)

    @trace_step("async step")
    async def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(fail())

    assert len(records) == 1
    record = records[0]
    assert record.status == "error"
    assert record.error_type == "RuntimeError"
    assert record.error_message == "boom"


def test_trace_step_can_suppress_error_messages():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)

    with pytest.raises(RuntimeError, match="private payload"), trace_step(
        "private-error", record_error_message=False
    ):
        raise RuntimeError("private payload")

    assert records[0].status == "error"
    assert records[0].error_type == "RuntimeError"
    assert records[0].error_message is None


def test_trace_step_treats_generator_exit_as_cancelled():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    step = trace_step("stream")

    step.__enter__()
    assert step.__exit__(GeneratorExit, GeneratorExit(), None) is False

    assert records[0].status == "cancelled"
    assert records[0].error_type is None
    assert records[0].error_message is None


def test_trace_step_rejects_generator_functions():
    with pytest.raises(TypeError, match="generator"):

        @trace_step("generator")
        def generate():
            yield "not traced"


def test_trace_step_context_manager_rejects_reentry():
    step = trace_step("single-use")

    def reenter() -> None:
        with step:
            step.__enter__()

    with pytest.raises(RuntimeError, match="not re-entrant"):
        reenter()

    with step:
        assert trace_attribute("after", True)


def test_trace_content_is_omitted_without_content_opt_in(monkeypatch):
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    monkeypatch.delenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", raising=False)

    @trace_step("private", include_args=True, include_result=True)
    def private(value: str) -> str:
        assert not trace_attribute("secret", value, content=True)
        return value

    assert private("classified") == "classified"

    assert "args" not in records[0].attributes
    assert "result" not in records[0].attributes
    assert "secret" not in records[0].attributes


def test_jsonl_trace_sink_writes_completed_records(tmp_path):
    path = tmp_path / "traces" / "live.jsonl"
    register_trace_sink("jsonl", jsonl_trace_sink(path))

    with trace_step("write-jsonl", attributes={"items": (1, 2)}):
        trace_attribute("object", object())

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["name"] == "write-jsonl"
    assert rows[0]["attributes"]["items"] == [1, 2]
    assert rows[0]["attributes"]["object"].startswith("<object object at ")


def test_trace_records_freeze_nested_attributes():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    nested = {"items": [1]}

    with trace_step("freeze", attributes={"nested": nested}):
        nested["items"].append(2)

    nested["items"].append(3)
    assert records[0].attributes["nested"] == {"items": [1]}


def test_sink_registry_manages_names_and_isolates_failures():
    records: list[TraceRecord] = []

    def broken(_record: TraceRecord) -> None:
        raise RuntimeError("sink failed")

    register_trace_sink("broken", broken)
    register_trace_sink("memory", records.append)
    assert registered_trace_sinks() == ("broken", "memory")

    with trace_step("survives-broken-sink"):
        pass

    assert [record.name for record in records] == ["survives-broken-sink"]
    assert remove_trace_sink("broken") is True
    assert remove_trace_sink("missing") is False
    assert registered_trace_sinks() == ("memory",)


def test_trace_attribute_handles_cyclic_and_unrepresentable_values():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    cycle: dict[str, object] = {}
    cycle["self"] = cycle

    class BrokenRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    with trace_step("safe-values"):
        assert trace_attribute("cycle", cycle)
        assert trace_attribute("broken", BrokenRepr())

    assert records[0].attributes["cycle"] == {"self": "<recursion>"}
    assert records[0].attributes["broken"] == "<unrepresentable BrokenRepr>"


def test_trace_attribute_preserves_shared_noncyclic_values_and_nonfinite_floats():
    records: list[TraceRecord] = []
    register_trace_sink("memory", records.append)
    shared = {"value": 1}

    with trace_step("shared"):
        assert trace_attribute("dag", {"a": shared, "b": shared})
        assert trace_attribute("not-a-number", math.nan)
        assert trace_attribute("infinity", math.inf)

    assert records[0].attributes["dag"] == {
        "a": {"value": 1},
        "b": {"value": 1},
    }
    assert records[0].attributes["not-a-number"] == "nan"
    assert records[0].attributes["infinity"] == "inf"


def test_sink_tracing_does_not_recurse():
    records: list[TraceRecord] = []

    def tracing_sink(record: TraceRecord) -> None:
        records.append(record)
        with trace_step("sink-step"):
            trace_attribute("sink", True)

    register_trace_sink("tracing", tracing_sink)

    with trace_step("source"):
        pass

    assert [record.name for record in records] == ["source"]


def test_sink_mutation_does_not_affect_later_sinks():
    seen: list[TraceRecord] = []

    def mutating_sink(record: TraceRecord) -> None:
        record.attributes["mutated"] = True

    register_trace_sink("mutating", mutating_sink)
    register_trace_sink("memory", seen.append)

    with trace_step("immutable-for-sinks"):
        trace_attribute("value", 1)

    assert seen[0].attributes == {"value": 1}


def test_register_trace_sink_validates_inputs():
    with pytest.raises(ValueError, match="non-empty"):
        register_trace_sink("", lambda _record: None)
    with pytest.raises(TypeError, match="callable"):
        register_trace_sink("not-callable", object())

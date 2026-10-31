"""SSE keep-alive comments on streamed Responses (agentserver-core contract)."""

from __future__ import annotations

import asyncio
import contextvars
import gc

import pytest

from castia import Agent
from castia.building import AgentTestHarness
from castia.hosting import server

FRAME = ": keep-alive\n\n"


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 15), ("30", 30), ("0", 0), ("-5", 0)]
)
def test_interval_follows_env_with_agentserver_defaults(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(server.SSE_KEEPALIVE_ENV, raising=False)
    else:
        monkeypatch.setenv(server.SSE_KEEPALIVE_ENV, raw)
    assert server._sse_keepalive_interval() == expected


def test_invalid_interval_fails_at_startup(monkeypatch):
    monkeypatch.setenv(server.SSE_KEEPALIVE_ENV, "soon")
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        return text

    with pytest.raises(ValueError, match="SSE_KEEPALIVE_INTERVAL"):
        server.build_app(app._routes, app._wire, app._invokes, app._commands)


def _collect(source, interval):
    async def run():
        return [item async for item in server._with_sse_keepalive(source, interval)]

    return asyncio.run(run())


def test_idle_gaps_get_keepalive_frames_without_reordering():
    async def run():
        release = asyncio.Event()

        async def source():
            yield "a"
            await release.wait()
            yield "b"

        stream = server._with_sse_keepalive(source(), 0.01)
        assert await stream.__anext__() == "a"
        assert await stream.__anext__() == FRAME
        assert await stream.__anext__() == FRAME
        release.set()
        rest = [item async for item in stream]
        assert [item for item in rest if item != FRAME] == ["b"]

    asyncio.run(run())


def test_zero_interval_passes_the_stream_through():
    async def source():
        yield "a"
        await asyncio.sleep(0.05)
        yield "b"

    assert _collect(source(), 0) == ["a", "b"]


def test_source_stays_at_most_one_item_ahead():
    produced = []

    async def run():
        async def source():
            for n in range(100):
                produced.append(n)
                yield n

        stream = server._with_sse_keepalive(source(), 5)
        assert await stream.__anext__() == 0
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(produced) <= 3
        await stream.aclose()

    asyncio.run(run())


def test_context_variables_persist_across_the_stream():
    var = contextvars.ContextVar("v", default="unset")

    async def run():
        release = asyncio.Event()

        async def source():
            token = var.set("bound")
            try:
                yield var.get()
                await release.wait()
                yield var.get()
            finally:
                var.reset(token)

        stream = server._with_sse_keepalive(source(), 0.01)
        assert await stream.__anext__() == "bound"
        assert await stream.__anext__() == FRAME
        release.set()
        assert [item async for item in stream if item != FRAME] == ["bound"]

    asyncio.run(run())


def test_source_errors_propagate():
    async def source():
        yield "a"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _collect(source(), 0.05)


@pytest.mark.parametrize("error", [asyncio.CancelledError, KeyboardInterrupt])
def test_source_base_exceptions_are_not_a_clean_end(error):
    async def source():
        yield "a"
        raise error

    async def run():
        stream = server._with_sse_keepalive(source(), 5)
        assert await stream.__anext__() == "a"
        with pytest.raises(error):
            await asyncio.wait_for(stream.__anext__(), 1)

    asyncio.run(run())


def test_closing_with_source_paused_at_yield_resets_context_in_pump():
    var = contextvars.ContextVar("castia_test_scope")
    outcome = {}

    async def source():
        token = var.set("bound")
        try:
            for item in ("a", "b", "c", "d"):
                yield item
        finally:
            try:
                var.reset(token)
                outcome["reset"] = "ok"
            except ValueError as exc:
                outcome["reset"] = exc

    async def run():
        loop_errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: loop_errors.append(context)
        )
        stream = server._with_sse_keepalive(source(), 5)
        assert await stream.__anext__() == "a"
        # Let pump fill the queue with "b" and park the source at yield "c".
        for _ in range(5):
            await asyncio.sleep(0)
        await stream.aclose()
        gc.collect()
        await asyncio.sleep(0)
        return loop_errors

    loop_errors = asyncio.run(run())
    gc.collect()
    assert outcome == {"reset": "ok"}
    assert loop_errors == []


def test_closing_the_stream_cancels_the_source():
    cleaned = asyncio.Event()

    async def source():
        try:
            yield "a"
            await asyncio.sleep(10)
            yield "never"
        finally:
            cleaned.set()

    async def run():
        stream = server._with_sse_keepalive(source(), 0.02)
        assert await stream.__anext__() == "a"
        assert await stream.__anext__() == FRAME
        await stream.aclose()
        await asyncio.wait_for(cleaned.wait(), 1)

    asyncio.run(run())


def test_slow_streamed_response_carries_keepalive_frames(monkeypatch):
    monkeypatch.setattr(server, "_sse_keepalive_interval", lambda: 0.03)
    app = Agent()

    @app.responses()
    async def reply(text: str) -> str:
        await asyncio.sleep(0.15)
        return f"echo:{text}"

    async def run():
        async with AgentTestHarness(app) as test:
            streamed = await test.client.post(
                "/responses", json={"input": "hi", "stream": True}
            )
            assert streamed.status_code == 200
            return streamed.text

    text = asyncio.run(run())
    assert FRAME in text
    assert "echo:hi" in text
    assert text.rstrip().endswith("data: [DONE]")

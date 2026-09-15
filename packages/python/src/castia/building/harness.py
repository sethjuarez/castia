"""Exercise the real Castia ASGI endpoints without a listening socket.

Example::

    async with AgentTestHarness(app, dependency_overrides={provider: fake}) as test:
        response = await test.client.post("/responses", json={"input": "Hello"})
        assert response.json()["output_text"] == "Hello"

Every dependency must have an explicit override, even if it is already cached
by the production dispatcher. Overrides are zero-argument callables, optionally
async or (async) generator fixtures. Generator teardown runs on context exit.
The runtime cache is never touched. Connector events retain payloads but never
authorization headers; do not use real secrets in test activities.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, nullcontext
from dataclasses import asdict, dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import patch

from ._offline import offline_scope

if TYPE_CHECKING:
    import httpx

    from ..application import Agent


@dataclass(frozen=True)
class CapturedEgress:
    """One connector verb, after real envelope/decorations were applied."""

    method: str
    url: str
    body: dict[str, Any] | None
    status_code: int
    activity_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentTestHarness:
    """Exclusive async test scope around ``server.build_app``.

    ``client`` is a standard HTTPX AsyncClient: all registered native endpoints,
    readiness and Activity invokes use the production route/parser/dispatcher.
    ``egress`` records messages, reactions, typing, updates, deletes and streaming
    emitted through Castia's connector. ``connector_status`` simulates failures.
    This context closes its client and fixtures even on failure or cancellation.
    It never starts ``Agent.run``, a server, authentication, or telemetry.
    """

    def __init__(
        self,
        app: Agent,
        *,
        dependency_overrides: Mapping[Callable[..., Any], Callable[..., Any]] | None = None,
        connector_status: int = 200,
        raise_app_exceptions: bool = True,
    ) -> None:
        from ..application import Agent

        if not isinstance(app, Agent):
            raise TypeError("app must be a castia.Agent")
        if not 100 <= connector_status <= 599:
            raise ValueError("connector_status must be an HTTP status code")
        self.app = app
        self.dependency_overrides = dict(dependency_overrides or {})
        if not all(callable(k) and callable(v) for k, v in self.dependency_overrides.items()):
            raise TypeError("dependency_overrides must map providers to callable fixtures")
        self.connector_status = connector_status
        self.raise_app_exceptions = raise_app_exceptions
        self.egress: list[CapturedEgress] = []
        self._stack: AsyncExitStack | None = None
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[Callable[..., Any], Any] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Use the harness inside 'async with'.")
        return self._client

    async def _resolve(self, dependency: Callable[..., Any], context: Any) -> Any:
        if dependency in self._cache:
            return self._cache[dependency]
        provider = self.dependency_overrides.get(dependency)
        if provider is None:
            raise RuntimeError("Missing dependency override; real providers are disabled.")
        assert self._stack is not None
        if inspect.isasyncgenfunction(provider):
            result = await self._stack.enter_async_context(asynccontextmanager(provider)())
        elif inspect.isgeneratorfunction(provider):
            result = self._stack.enter_context(contextmanager(provider)())
        else:
            result = provider()
            if inspect.isawaitable(result):
                result = await result
        self._cache[dependency] = result
        return result

    async def _send(
        self,
        activity: Any,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        import httpx

        created = f"captured-{len(self.egress) + 1}" if method == "POST" else None
        if self.connector_status >= 400:
            created = None
        self.egress.append(
            CapturedEgress(method, url, copy.deepcopy(json), self.connector_status, created)
        )
        return httpx.Response(
            self.connector_status,
            json={"id": created} if created else {},
            request=httpx.Request(method, url),
        )

    async def __aenter__(self) -> Self:
        if self._stack is not None:
            raise RuntimeError("This harness is already active.")
        stack = AsyncExitStack()
        try:
            stack.enter_context(offline_scope())
            import httpx

            from .. import connector, dispatch, server

            async def anonymous(*args: Any, **kwargs: Any) -> dict[str, str]:
                return {}

            for module, name, replacement in (
                (connector, "_send", self._send),
                (connector, "_authorization", anonymous),
                (connector, "authorization", anonymous),
                (dispatch, "resolve", self._resolve),
                (dispatch, "flush_telemetry", lambda: None),
                (dispatch, "invoke_agent", nullcontext),
            ):
                stack.enter_context(patch.object(module, name, replacement))
            asgi = server.build_app(self.app._routes, self.app._wire, self.app._invokes)
            transport = httpx.ASGITransport(
                app=asgi, raise_app_exceptions=self.raise_app_exceptions
            )
            self._client = await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://castia.test")
            )
            self.egress.clear()
            self._cache.clear()
            self._stack = stack
            return self
        except BaseException:
            self._client = None
            await stack.aclose()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        assert self._stack is not None
        try:
            return await self._stack.__aexit__(exc_type, exc, tb)
        finally:
            self._stack = None
            self._client = None
            self._cache.clear()

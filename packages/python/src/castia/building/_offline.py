"""Scoped offline guards, not a sandbox for untrusted Python code."""

from __future__ import annotations

import logging
import platform
import socket
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from threading import Lock
from unittest.mock import patch

_ACTIVE = Lock()
_CONNECT = socket.socket.connect
_SOCKETPAIR_CODE = getattr(socket.socketpair, "__code__", None)


class OfflineOperationError(RuntimeError):
    """A test attempted outbound I/O instead of using an override."""


def _blocked(*args: object, **kwargs: object) -> None:
    raise OfflineOperationError("Outbound I/O is disabled; provide a test override.")


def _connect(sock: socket.socket, address: object) -> None:
    # Windows implements socketpair with one private loopback connection.
    # asyncio needs it for its wakeup pipe. Do not permit general loopback I/O.
    if (
        _SOCKETPAIR_CODE is not None
        and sys._getframe(1).f_code is _SOCKETPAIR_CODE
        and isinstance(address, tuple)
        and address[0] in ("127.0.0.1", "::1")
    ):
        return _CONNECT(sock, address)
    _blocked()


@contextmanager
def offline_scope(*, exclusive: bool = True) -> Iterator[ExitStack]:
    """Deny common network/process paths and silence Castia's telemetry.

    Patches are process-global and overlapping scopes fail fast. Existing
    background threads/exporters must be stopped by the caller before entry.
    User code is trusted: this is test isolation, not a Python security boundary.
    """
    if exclusive and not _ACTIVE.acquire(blocking=False):
        raise RuntimeError("An offline Castia scope is already active in this process.")
    try:
        # Python 3.11 Windows uses the local `ver` command on first platform
        # lookup. Warm stdlib caches before guards, not SDK imports or user code.
        # pytest also needs platform()'s own cache; uname() alone is insufficient.
        platform.uname()
        platform.platform()
        with ExitStack() as stack:
            for target in (
                "socket.socket.connect_ex",
                "socket.create_connection",
                "socket.getaddrinfo",
                "socket.socket.sendto",
                "subprocess.Popen",
                "os.system",
            ):
                stack.enter_context(patch(target, _blocked))
            stack.enter_context(patch.object(socket.socket, "connect", _connect))
            stack.enter_context(patch.object(logging.Logger, "handle", lambda *a: None))
            # Lazy imports happen only inside an explicitly requested offline scope.
            import httpx
            from opentelemetry import trace

            from castia.observe import configuration as observability
            from castia.runtime import dispatch

            # Import dispatch before patching the functions it binds by name.
            # Otherwise its initial import would retain a no-op after scope exit.
            del dispatch
            stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", _blocked))
            stack.enter_context(
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", _blocked)
            )

            stack.enter_context(
                patch.object(trace, "get_tracer", trace.NoOpTracerProvider().get_tracer)
            )
            stack.enter_context(
                patch.object(observability, "configure_observability", lambda *a, **k: None)
            )
            stack.enter_context(
                patch.object(observability, "flush_telemetry", lambda: None)
            )
            yield stack
    finally:
        if exclusive:
            _ACTIVE.release()

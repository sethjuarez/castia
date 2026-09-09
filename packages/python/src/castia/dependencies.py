"""Minimal FastAPI-style dependency injection.

``Depends(callable)`` is an inert marker -- exactly like FastAPI's, it does not
call the callable. The dispatcher sees it in a handler's signature and resolves
it at call time. Results are cached per callable for the life of the process, so
expensive singletons (a model client, an auth handle) are built once instead of
per message. This is the seam that fixes the "construct the client every turn"
bug in the original sample.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

_CACHE: dict[Callable[..., Any], Any] = {}


class _Depends:
    """Marker recording that a parameter is supplied by ``dependency``."""

    def __init__(
        self, dependency: Callable[..., Any], *, use_cache: bool = True
    ) -> None:
        self.dependency = dependency
        self.use_cache = use_cache


def Depends(dependency: Callable[..., Any], *, use_cache: bool = True) -> Any:
    """Declare that a parameter is provided by ``dependency``.

    Returns ``Any`` -- like FastAPI's ``Depends`` -- so that using it as a
    parameter default (``model: Model = Depends(gpt4o)``) type-checks cleanly
    even though a marker, not a ``Model``, is what actually sits there. The
    dispatcher replaces it with the resolved value before the handler runs.
    """
    return _Depends(dependency, use_cache=use_cache)


async def resolve(dependency: Callable[..., Any], context: Any) -> Any:
    """Resolve a dependency callable, caching the result process-wide."""
    if dependency in _CACHE:
        return _CACHE[dependency]

    result = dependency()
    if inspect.isawaitable(result):
        result = await result

    _CACHE[dependency] = result
    return result

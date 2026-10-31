"""Consumer-owned storage for Responses lifecycle records.

Castia serves ``background=true`` Responses requests, but it does not own
their storage. A hosted agent can run on several replicas, so a poll
(``GET /responses/{id}``) or cancel can land on a different replica than the
one running the turn. The consumer injects a :class:`ResponseStore` backed by
storage every replica can see (Cosmos DB, Redis, SQL, Foundry storage, ...)::

    app = Agent(name="my-agent", response_store=MyCosmosResponseStore(...))

Castia ships no production store. Without one, ``background=true`` is rejected
with ``400 unsupported_parameter`` and foreground responses keep using a small
process-local cache that is not shared across replicas.

Execution is still process-bound: the store makes *state* visible across
replicas, but a replica restart abandons in-flight work. Retention, expiry, and
sweeping stale ``queued``/``in_progress`` records are the store owner's call.
"""

from __future__ import annotations

import inspect
from collections import OrderedDict
from typing import NotRequired, Protocol, TypedDict, runtime_checkable

__all__ = [
    "ResponseHistoryStore",
    "ResponseStore",
    "StoredResponse",
    "validate_response_store",
]

NON_TERMINAL_STATUSES = frozenset({"queued", "in_progress"})
_STORE_METHODS = ("create", "get", "update", "delete")


class StoredResponse(TypedDict):
    """One stored Responses record.

    ``response`` is the public Responses wire object (its ``id`` is the record
    key and its ``status`` is the compare-and-set field). ``input_items`` are
    the normalized items served by ``GET /responses/{id}/input_items``.
    ``background`` marks records created by ``background=true``; only those
    can be cancelled. ``user_id`` is the Foundry ``x-agent-user-id`` of the
    caller that created the record (``None`` without one); Castia answers
    ``404`` when a later caller's user id differs. It is ownership metadata,
    not a lookup key: records stay keyed by response id.
    """

    response: dict
    input_items: list[dict]
    background: bool
    user_id: NotRequired[str | None]


@runtime_checkable
class ResponseStore(Protocol):
    """Async storage contract for Responses records.

    Every method is a coroutine. Implementations must be safe for concurrent
    calls on the app's event loop and must raise (not return ``None``/``False``)
    when the backend is unavailable, so Castia can answer ``503`` rather than
    pretend a record is missing.
    """

    async def create(self, record: StoredResponse) -> None:
        """Insert ``record`` keyed by ``record["response"]["id"]``."""

    async def get(self, response_id: str) -> StoredResponse | None:
        """Return the record, or ``None`` when it does not exist."""

    async def update(self, record: StoredResponse, *, expected_status: str) -> bool:
        """Atomically replace a record only if its current status matches.

        Replace the stored record keyed by ``record["response"]["id"]`` only
        when its current ``response.status`` equals ``expected_status``, and
        return ``True``. Return ``False`` when the status differs or the record
        does not exist. Never upsert: a deleted record must stay deleted.
        """

    async def delete(self, response_id: str) -> bool:
        """Delete a record; return ``False`` when it did not exist."""


@runtime_checkable
class ResponseHistoryStore(Protocol):
    """Optional history lookup a :class:`ResponseStore` may also implement.

    Castia reconstructs ``previous_response_id`` chains through
    :meth:`ResponseStore.get` on its own. Implement this to serve history from
    an index instead, and to enable ``conversation``: Castia never stores a
    conversation itself, it records the id on each response
    (``response["conversation"]["id"]``) so the store can index it.

    Exactly one of ``previous_response_id`` / ``conversation_id`` is set.
    Return one list per prior response, oldest first; each list is that
    response's ``input_items`` followed by its ``response["output"]``. Keeping
    responses separate lets Castia drop the oldest whole responses when the
    history passes ``limit`` items. Only include records whose ``user_id``
    equals ``user_id`` -- for ``conversation`` lookups, tenant isolation is the
    store's job. Castia has already checked that ``previous_response_id``
    exists and belongs to the caller. Raise when the backend is unavailable.
    """

    async def get_history(
        self,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        user_id: str | None,
        limit: int,
    ) -> list[list[dict]]:
        """Return prior responses' items, one list per response, oldest first."""


def validate_response_store(store: object) -> ResponseStore:
    """Fail fast when ``store`` does not implement the async contract."""
    missing = [name for name in _STORE_METHODS if not callable(getattr(store, name, None))]
    if missing:
        raise TypeError(
            "response_store must implement castia.ResponseStore; missing "
            + ", ".join(missing)
        )
    sync = [
        name
        for name in _STORE_METHODS
        if not inspect.iscoroutinefunction(getattr(store, name))
    ]
    if sync:
        raise TypeError(
            "response_store methods must be async (coroutine functions): "
            + ", ".join(sync)
        )
    return store  # type: ignore[return-value]


class _ProcessLocalResponseCache:
    """Bounded per-process cache used only when no consumer store is configured.

    Keeps the most recent terminal foreground responses so a client can read
    them back from the same process. It is never used for ``background=true``.
    """

    def __init__(self, max_records: int) -> None:
        self._max_records = max_records
        self._records: OrderedDict[str, StoredResponse] = OrderedDict()

    async def create(self, record: StoredResponse) -> None:
        response_id = record["response"]["id"]
        self._records[response_id] = record
        self._records.move_to_end(response_id)
        while len(self._records) > self._max_records:
            self._records.popitem(last=False)

    async def get(self, response_id: str) -> StoredResponse | None:
        return self._records.get(response_id)

    async def update(self, record: StoredResponse, *, expected_status: str) -> bool:
        response_id = record["response"]["id"]
        current = self._records.get(response_id)
        if current is None or current["response"].get("status") != expected_status:
            return False
        self._records[response_id] = record
        return True

    async def delete(self, response_id: str) -> bool:
        return self._records.pop(response_id, None) is not None

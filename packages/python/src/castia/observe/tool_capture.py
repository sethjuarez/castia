"""Opt-in, evaluation-only capture of full tool-call payloads.

Evaluators sometimes need to verify what a tool actually returned instead of
trusting the model's citations. Telemetry spans deliberately omit payloads and
local dev diagnostics truncate them, so this module records each tool call's
arguments and complete result into a private, operator-chosen sink.

Capture is **off by default** and never changes model input, tool specs, tool
results, or wire output. Failures inside capture or the sink are logged and
swallowed; they cannot turn a tool success into a model-visible error.

Enable it only on deployments used for evaluation::

    CASTIA_TOOL_CAPTURE=file  CASTIA_TOOL_CAPTURE_DIR=.castia/tool-captures
    CASTIA_TOOL_CAPTURE=blob  CASTIA_TOOL_CAPTURE_BLOB_ACCOUNT_URL=https://<acct>.blob.core.windows.net

Each completed turn becomes one ``castia.tool_capture/v1`` JSON document with
per-payload SHA-256 hashes and a whole-record hash. Hashes prove a record is
internally consistent, not that it is authentic or that no record is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "castia.tool_capture/v1"

MODE_ENV = "CASTIA_TOOL_CAPTURE"
DIR_ENV = "CASTIA_TOOL_CAPTURE_DIR"
BLOB_ACCOUNT_URL_ENV = "CASTIA_TOOL_CAPTURE_BLOB_ACCOUNT_URL"
BLOB_CONTAINER_ENV = "CASTIA_TOOL_CAPTURE_BLOB_CONTAINER"
BLOB_PREFIX_ENV = "CASTIA_TOOL_CAPTURE_BLOB_PREFIX"
MAX_RESULT_BYTES_ENV = "CASTIA_TOOL_CAPTURE_MAX_RESULT_BYTES"
MAX_TURN_BYTES_ENV = "CASTIA_TOOL_CAPTURE_MAX_TURN_BYTES"
MAX_CALLS_ENV = "CASTIA_TOOL_CAPTURE_MAX_CALLS"
TIMEOUT_ENV = "CASTIA_TOOL_CAPTURE_TIMEOUT_SECONDS"

DEFAULT_DIR = ".castia/tool-captures"
DEFAULT_CONTAINER = "castia-tool-captures"
DEFAULT_MAX_RESULT_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_TURN_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_CALLS = 128
DEFAULT_TIMEOUT_SECONDS = 15.0
_MAX_METADATA_BYTES = 16 * 1024
_MAX_ERROR_MESSAGE = 2000

ToolCaptureSink = Callable[[dict[str, Any]], "Awaitable[None] | None"]
Redactor = Callable[[str, Any], Any]
"""``redact(field, value)``; ``field`` is ``arguments``, ``result``, ``error``,
``metadata``, or ``provenance``. Return the value to store."""

_logger = logging.getLogger("castia.observe.tool_capture")
_CURRENT: ContextVar[_Turn | None] = ContextVar("castia_tool_capture_turn", default=None)
_LOCK = threading.Lock()
_configured_sink: ToolCaptureSink | None = None
_configured_redact: Redactor | None = None
_env_sink_cache: tuple[tuple[str, ...], ToolCaptureSink | None] | None = None
_health: dict[str, Any] = {"persisted": 0, "failed": 0, "last_error_type": None}
_MISSING = object()


# --------------------------------------------------------------------------- config


def enabled() -> bool:
    """Return whether capture is configured (env or :func:`configure_tool_capture`)."""
    return _resolve_sink() is not None


def configure_tool_capture(
    *, sink: ToolCaptureSink | None = None, redact: Redactor | None = None
) -> None:
    """Install a process-wide sink and/or redactor.

    A configured ``sink`` enables capture even when ``CASTIA_TOOL_CAPTURE`` is
    unset; it receives each finished document and may be sync or async.
    ``redact`` is applied to every stored content field before hashing of the
    stored copy (original-content hashes are always computed first).
    """
    global _configured_sink, _configured_redact
    if sink is not None and not callable(sink):
        raise TypeError("tool capture sink must be callable")
    if redact is not None and not callable(redact):
        raise TypeError("tool capture redactor must be callable")
    with _LOCK:
        _configured_sink = sink
        _configured_redact = redact


def reset_tool_capture() -> None:
    """Remove programmatic configuration, cached env sinks, and health counters."""
    global _configured_sink, _configured_redact, _env_sink_cache
    with _LOCK:
        _configured_sink = None
        _configured_redact = None
        _env_sink_cache = None
        _health.update(persisted=0, failed=0, last_error_type=None)


def tool_capture_health() -> dict[str, Any]:
    """Payload-free persistence counters; a failed count means evidence is missing."""
    active = enabled()
    with _LOCK:
        return {"enabled": active, **_health}


def _env_key() -> tuple[str, ...]:
    return tuple(
        os.environ.get(name, "")
        for name in (MODE_ENV, DIR_ENV, BLOB_ACCOUNT_URL_ENV, BLOB_CONTAINER_ENV, BLOB_PREFIX_ENV)
    )


def _resolve_sink() -> ToolCaptureSink | None:
    global _env_sink_cache
    if _configured_sink is not None:
        return _configured_sink
    key = _env_key()
    with _LOCK:
        if _env_sink_cache is not None and _env_sink_cache[0] == key:
            return _env_sink_cache[1]
        sink = _sink_from_env(key)
        _env_sink_cache = (key, sink)
        return sink


def _sink_from_env(key: tuple[str, ...]) -> ToolCaptureSink | None:
    mode, directory, account_url, container, prefix = (value.strip() for value in key)
    mode = mode.lower()
    if mode in {"", "0", "off", "false", "no", "none"}:
        return None
    if mode == "file":
        return file_capture_sink(directory or DEFAULT_DIR)
    if mode == "blob":
        if not account_url:
            _logger.warning(
                "%s=blob requires %s; tool capture is disabled.", MODE_ENV, BLOB_ACCOUNT_URL_ENV
            )
            return None
        return blob_capture_sink(account_url, container or DEFAULT_CONTAINER, prefix=prefix)
    _logger.warning("Unsupported %s value %r; tool capture is disabled.", MODE_ENV, mode)
    return None


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


# --------------------------------------------------------------------------- sinks


def file_capture_sink(directory: str | os.PathLike[str]) -> ToolCaptureSink:
    """Append each document as one line to ``<directory>/tool-captures-YYYYMMDD.jsonl``.

    One ``write`` per document under a process lock; readers skip a partial
    trailing line. Use one writer process per directory.
    """
    root = Path(directory)
    lock = threading.Lock()

    def sink(document: dict[str, Any]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        _restrict(root)
        day = str(document.get("started_at") or _now())[:10].replace("-", "")
        path = root / f"tool-captures-{day}.jsonl"
        line = json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n"
        with lock:
            created = not path.exists()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            if created:
                _restrict(path)

    return sink


def _restrict(path: Path) -> None:
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


def blob_capture_sink(
    account_url: str,
    container: str = DEFAULT_CONTAINER,
    *,
    prefix: str = "",
    credential: Any = None,
) -> ToolCaptureSink:
    """Create-only upload of one blob per document (``overwrite=False``).

    Requires ``azure-storage-blob`` (``castia[capture]``). The identity needs
    *Storage Blob Data Contributor* on a private container. A name collision is
    accepted only when the existing blob has identical content.
    """
    state: dict[str, Any] = {}

    def upload(document: dict[str, Any]) -> None:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import BlobServiceClient

        if "client" not in state:
            if credential is None:
                from azure.identity import DefaultAzureCredential

                cred = DefaultAzureCredential()
            else:
                cred = credential
            state["client"] = BlobServiceClient(account_url, credential=cred).get_container_client(
                container
            )
        client = state["client"]
        started = str(document.get("started_at") or _now())
        key = document.get("response_id") or document["capture_id"]
        name = "/".join(
            part
            for part in (
                prefix.strip("/"),
                started[:4], started[5:7], started[8:10],
                f"{_safe_name(str(key))}-{document['capture_id']}.json",
            )
            if part
        )
        data = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            client.upload_blob(name=name, data=data, overwrite=False)
        except ResourceExistsError:
            existing = client.download_blob(name).readall()
            if existing != data:
                raise

    async def sink(document: dict[str, Any]) -> None:
        await asyncio.to_thread(upload, document)

    return sink


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)[:120]


# --------------------------------------------------------------------------- encoding


def canonical_json(value: Any) -> tuple[str, bool]:
    """Return canonical JSON for ``value`` and whether it had to be stringified."""
    try:
        return (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False),
            False,
        )
    except (TypeError, ValueError):
        return (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       default=_lossy),
            True,
        )


def _lossy(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # noqa: BLE001, S110 - fall back to repr
            pass
    return repr(value)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- turn


class _Turn:
    def __init__(
        self,
        *,
        protocol: str,
        sink: ToolCaptureSink,
        redact: Redactor | None,
        input_text: str | None,
        response_id: str | None,
        metadata: Mapping[str, Any] | None,
        provenance: Mapping[str, Any] | None,
    ) -> None:
        from castia.runtime.request_context import current_request_context

        self.sink = sink
        self.redact = redact
        self.closed = False
        self.lock = threading.Lock()
        self.max_result_bytes = _int_env(MAX_RESULT_BYTES_ENV, DEFAULT_MAX_RESULT_BYTES)
        self.max_turn_bytes = _int_env(MAX_TURN_BYTES_ENV, DEFAULT_MAX_TURN_BYTES)
        self.max_calls = _int_env(MAX_CALLS_ENV, DEFAULT_MAX_CALLS)
        self.stored_bytes = 0
        self.incomplete: set[str] = set()
        self.started = time.time()
        context = current_request_context()
        trace_id, span_id = _span_ids()
        self.doc: dict[str, Any] = {
            "schema": SCHEMA,
            "capture_id": uuid.uuid4().hex,
            "protocol": protocol,
            "response_id": response_id,
            "platform_call_id": context.call_id,
            "session_id": context.session_id,
            "trace_id": trace_id,
            "span_id": span_id,
            "started_at": _now(),
            "completed_at": None,
            "duration_ms": None,
            "input_sha256": _sha256(input_text) if input_text is not None else None,
            "output_sha256": None,
            "request_metadata": self._bounded("metadata", metadata),
            "provenance": self._bounded("provenance", provenance),
            "status": "running",
            "error": None,
            "complete": True,
            "incomplete_reasons": [],
            "omitted_call_count": 0,
            "tool_calls": [],
        }
        _link_span(self.doc["capture_id"])

    def _bounded(self, field: str, value: Mapping[str, Any] | None) -> dict[str, Any]:
        if not value:
            return {}
        stored = self._redact(field, dict(value))
        text, _ = canonical_json(stored)
        if len(text.encode("utf-8")) > _MAX_METADATA_BYTES:
            self.incomplete.add(f"{field}_too_large")
            return {"omitted": True, "sha256": _sha256(text)}
        return json.loads(text)

    def _redact(self, field: str, value: Any) -> Any:
        if self.redact is None:
            return value
        try:
            return self.redact(field, value)
        except Exception as exc:  # noqa: BLE001 - redaction must fail closed, not crash
            self.incomplete.add("redaction_failed")
            return {"redaction_failed": type(exc).__name__}

    def encode(self, field: str, value: Any) -> dict[str, Any]:
        """Encode one payload with original/stored hashes and size limits."""
        original, lossy = canonical_json(value)
        entry: dict[str, Any] = {
            f"{field}_sha256": _sha256(original),
            f"{field}_bytes": len(original.encode("utf-8")),
        }
        if lossy:
            entry[f"{field}_lossy"] = True
        stored_value = self._redact(field, json.loads(original))
        stored, _ = canonical_json(stored_value)
        if stored != original:
            entry[f"{field}_redacted"] = True
        size = len(stored.encode("utf-8"))
        with self.lock:
            remaining = self.max_turn_bytes - self.stored_bytes
            if remaining <= 0:
                self.incomplete.add("turn_byte_limit")
                entry[f"{field}_omitted"] = True
                return entry
            limit = min(self.max_result_bytes, remaining)
            if size <= limit:
                self.stored_bytes += size
                entry[field] = json.loads(stored)
                return entry
            prefix = stored.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
            self.stored_bytes += len(prefix.encode("utf-8"))
            self.incomplete.add(
                "result_byte_limit" if limit == self.max_result_bytes else "turn_byte_limit"
            )
        entry[f"{field}_truncated"] = True
        entry[f"{field}_prefix"] = prefix
        return entry

    def begin(
        self,
        *,
        name: str,
        call_id: str | None,
        tool_type: str | None,
        source: str,
        arguments: Any,
        iteration: int | None,
    ) -> dict[str, Any] | None:
        with self.lock:
            if self.closed:
                return None
            calls = self.doc["tool_calls"]
            if len(calls) >= self.max_calls:
                self.doc["omitted_call_count"] += 1
                self.incomplete.add("call_count_limit")
                return None
            call: dict[str, Any] = {
                "sequence": len(calls),
                "iteration": iteration,
                "call_id": call_id,
                "name": name,
                "tool_type": tool_type,
                "source": source,
                "status": "running",
                "tool_reported_ok": None,
                "error": None,
                "span_id": None,
                "started_at": _now(),
                "completed_at": None,
                "duration_ms": None,
                "_t0": time.perf_counter(),
            }
            calls.append(call)
        if arguments is not _MISSING:
            call.update(self.encode("arguments", _parse_arguments(arguments)))
        return call

    def finish(
        self,
        call: dict[str, Any],
        *,
        result: Any = _MISSING,
        error_type: str | None = None,
        error_message: str | None = None,
        span: Any = None,
    ) -> None:
        if self.closed or call.get("status") != "running":
            return
        if span is not None:
            call["span_id"] = _span_id_of(span)
        if result is not _MISSING:
            call.update(self.encode("result", result))
            call["tool_reported_ok"] = _reported_ok(result)
        if error_type:
            message = error_message or ""
            message = self._redact("error", message[:_MAX_ERROR_MESSAGE])
            call["error"] = {"type": error_type, "message": message}
        call["status"] = "error" if error_type else "ok"
        call["completed_at"] = _now()
        call["duration_ms"] = round((time.perf_counter() - call.pop("_t0")) * 1000, 3)

    def close(self, status: str, error_type: str | None) -> dict[str, Any]:
        with self.lock:
            self.closed = True
        doc = self.doc
        for call in doc["tool_calls"]:
            if call.get("status") == "running":
                call["status"] = "incomplete"
                self.incomplete.add("call_unfinished")
            call.pop("_t0", None)
        completed = time.time()
        doc["completed_at"] = _now()
        doc["duration_ms"] = round((completed - self.started) * 1000, 3)
        doc["status"] = status
        doc["error"] = {"type": error_type} if error_type else None
        if status != "ok":
            self.incomplete.add(f"turn_{status}")
        doc["incomplete_reasons"] = sorted(self.incomplete)
        doc["complete"] = not self.incomplete
        doc["record_sha256"] = record_hash(doc)
        return doc


def record_hash(document: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical document without its own ``record_sha256``."""
    body = {key: value for key, value in document.items() if key != "record_sha256"}
    return _sha256(canonical_json(body)[0])


def _parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    return arguments


def _reported_ok(result: Any) -> bool | None:
    if isinstance(result, Mapping):
        if isinstance(result.get("ok"), bool):
            return result["ok"]
        if isinstance(result.get("isError"), bool):
            return not result["isError"]
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _span_ids() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            return f"{context.trace_id:032x}", f"{context.span_id:016x}"
    except Exception:  # noqa: BLE001, S110 - telemetry is optional context
        pass
    return None, None


def _span_id_of(span: Any) -> str | None:
    try:
        context = span.get_span_context()
        return f"{context.span_id:016x}" if context.is_valid else None
    except Exception:  # noqa: BLE001
        return None


def _link_span(capture_id: str) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("castia.tool_capture.id", capture_id)
    except Exception:  # noqa: BLE001, S110
        pass


# --------------------------------------------------------------------------- public scope


@asynccontextmanager
async def tool_capture_scope(
    protocol: str = "sdk",
    *,
    input_text: str | None = None,
    response_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    sink: ToolCaptureSink | None = None,
):
    """Capture every tool call made inside this scope as one document.

    Castia's Responses host opens this scope automatically when capture is
    enabled; offline evaluators may open it around direct SDK calls. Yields the
    capture id, or ``None`` when capture is disabled. The document is persisted
    on exit, including on error or cancellation.
    """
    chosen = sink or _resolve_sink()
    if chosen is None:
        yield None
        return
    try:
        turn = _Turn(
            protocol=protocol,
            sink=chosen,
            redact=_configured_redact,
            input_text=input_text,
            response_id=response_id,
            metadata=metadata,
            provenance=provenance,
        )
    except Exception:  # noqa: BLE001 - capture must never fail the turn
        _logger.warning("Castia tool capture could not start.", exc_info=False)
        yield None
        return
    token = _CURRENT.set(turn)
    status, error_type = "ok", None
    try:
        yield turn.doc["capture_id"]
    except (asyncio.CancelledError, GeneratorExit):
        status, error_type = "cancelled", "CancelledError"
        raise
    except BaseException as exc:
        status, error_type = "error", type(exc).__name__
        raise
    finally:
        _CURRENT.reset(token)
        document = turn.close(status, error_type)
        await asyncio.shield(_persist(chosen, document))


async def _persist(sink: ToolCaptureSink, document: dict[str, Any]) -> None:
    timeout = _float_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
    try:
        if inspect.iscoroutinefunction(sink):
            await asyncio.wait_for(sink(document), timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(sink, document), timeout)
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout)
    except Exception as exc:  # noqa: BLE001 - never fail the turn on sink errors
        with _LOCK:
            _health["failed"] += 1
            _health["last_error_type"] = type(exc).__name__
        _logger.warning(
            "Castia tool capture %s could not be persisted (%s).",
            document.get("capture_id"),
            type(exc).__name__,
        )
        return
    with _LOCK:
        _health["persisted"] += 1


def current_capture_id() -> str | None:
    turn = _CURRENT.get()
    return turn.doc["capture_id"] if turn is not None and not turn.closed else None


def annotate_tool_capture(**provenance: Any) -> None:
    """Attach bounded app provenance (release digests, eval row id) to the capture."""
    turn = _CURRENT.get()
    if turn is None or turn.closed or not provenance:
        return
    try:
        merged = {**turn.doc["provenance"], **provenance}
        turn.doc["provenance"] = turn._bounded("provenance", merged)
    except Exception:  # noqa: BLE001
        turn.incomplete.add("provenance_failed")


def set_response_id(response_id: str | None) -> None:
    turn = _CURRENT.get()
    if turn is not None and not turn.closed and response_id:
        turn.doc["response_id"] = response_id


def record_output(text: object) -> None:
    turn = _CURRENT.get()
    if turn is not None and not turn.closed:
        turn.doc["output_sha256"] = _sha256("" if text is None else str(text))


# --------------------------------------------------------------------------- recording hooks


def begin_tool_call(
    *,
    name: str,
    call_id: str | None = None,
    tool_type: str | None = None,
    source: str = "local",
    arguments: Any = _MISSING,
    iteration: int | None = None,
) -> dict[str, Any] | None:
    """Start recording one tool call; returns a handle for :func:`finish_tool_call`."""
    turn = _CURRENT.get()
    if turn is None:
        return None
    try:
        call = turn.begin(
            name=name, call_id=call_id, tool_type=tool_type, source=source,
            arguments=arguments, iteration=iteration,
        )
        return None if call is None else {"turn": turn, "call": call}
    except Exception:  # noqa: BLE001
        turn.incomplete.add("capture_failed")
        return None


def finish_tool_call(
    handle: dict[str, Any] | None,
    *,
    result: Any = _MISSING,
    error: BaseException | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    span: Any = None,
) -> None:
    """Finish a call started by :func:`begin_tool_call`. Never raises."""
    if handle is None:
        return
    turn: _Turn = handle["turn"]
    try:
        if error is not None:
            error_type = error_type or type(error).__name__
            error_message = error_message or str(error)
        turn.finish(
            handle["call"], result=result, error_type=error_type,
            error_message=error_message, span=span,
        )
    except Exception:  # noqa: BLE001
        turn.incomplete.add("capture_failed")


def record_server_tool_outputs(
    output: Iterable[Any] | None,
    *,
    iteration: int | None = None,
    spans: Sequence[Any] | None = None,
) -> None:
    """Record provider-executed tool items from one Responses ``output`` list.

    The stored result is whatever the provider returned in the item (for
    ``mcp_call`` that is ``output``); providers may not expose full payloads.
    ``spans`` are the synthesized ``execute_tool`` spans, in server-tool order,
    from :func:`castia.observe.server_tools.record_server_tools`.
    """
    if _CURRENT.get() is None:
        return
    from castia.observe.genai import SERVER_TOOL_TYPES, server_tool_calls

    items = [
        item for item in output or []
        if str(_field(item, "type") or "") in SERVER_TOOL_TYPES
    ]
    for index, item in enumerate(items):
        try:
            (call,) = server_tool_calls([item])
            item_type = call["item_type"]
            handle = begin_tool_call(
                name=call["name"],
                call_id=call["call_id"],
                tool_type=item_type,
                source="server",
                arguments=call["arguments"] if item_type == "mcp_call" else _MISSING,
                iteration=iteration,
            )
            if item_type == "mcp_call":
                result = call["result"]
            else:
                result = _lossy(item) if not isinstance(item, Mapping) else dict(item)
            finish_tool_call(
                handle,
                result=result,
                error_type=call["error_type"],
                error_message=call["error_message"] or call["error_type"],
                span=spans[index] if spans and index < len(spans) else None,
            )
        except Exception:  # noqa: BLE001
            turn = _CURRENT.get()
            if turn is not None:
                turn.incomplete.add("capture_failed")


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


# --------------------------------------------------------------------------- readers


def load_tool_captures(path: str | os.PathLike[str]) -> tuple[list[dict[str, Any]], int]:
    """Load documents from a ``.jsonl``/``.json`` file or a directory of them.

    Returns ``(documents, skipped_partial_lines)``; only a final unterminated
    JSONL line is tolerated as partial.
    """
    root = Path(path)
    files = (
        sorted(p for p in root.rglob("*") if p.suffix in {".jsonl", ".json"} and p.is_file())
        if root.is_dir()
        else [root]
    )
    documents: list[dict[str, Any]] = []
    skipped = 0
    for file in files:
        text = file.read_text(encoding="utf-8")
        if file.suffix == ".json":
            value = json.loads(text)
            documents.extend(value if isinstance(value, list) else [value])
            continue
        # Not splitlines(): payloads may hold raw U+2028/U+0085, which it treats as breaks.
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError:
                if index == len(lines) - 1 and not text.endswith("\n"):
                    skipped += 1
                    continue
                raise
    return [d for d in documents if isinstance(d, dict) and d.get("schema") == SCHEMA], skipped


def verify_tool_capture(document: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute hashes. Payload status is ``verified``, ``unavailable`` (truncated,
    redacted, omitted, or lossy), or ``failed``; the record is ``verified`` or ``failed``."""
    record_ok = document.get("record_sha256") == record_hash(document)
    calls = []
    for call in document.get("tool_calls") or []:
        entry: dict[str, Any] = {"sequence": call.get("sequence"), "name": call.get("name")}
        for field in ("arguments", "result"):
            if f"{field}_sha256" not in call:
                continue
            if (
                field not in call
                or call.get(f"{field}_redacted")
                or call.get(f"{field}_lossy")
            ):
                entry[field] = "unavailable"
            else:
                digest = _sha256(canonical_json(call[field])[0])
                entry[field] = "verified" if digest == call[f"{field}_sha256"] else "failed"
        calls.append(entry)
    failed = not record_ok or any("failed" in entry.values() for entry in calls)
    return {
        "capture_id": document.get("capture_id"),
        "status": "failed" if failed else "verified",
        "record": "verified" if record_ok else "failed",
        "complete": bool(document.get("complete")),
        "tool_calls": calls,
    }


def match_tool_captures(
    documents: Iterable[Mapping[str, Any]],
    *,
    response_id: str | None = None,
    capture_id: str | None = None,
    platform_call_id: str | None = None,
    trace_id: str | None = None,
    input_sha256: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> list[Mapping[str, Any]]:
    """Select documents whose join keys all match the given values."""
    keys = {
        "response_id": response_id,
        "capture_id": capture_id,
        "platform_call_id": platform_call_id,
        "trace_id": trace_id,
        "input_sha256": input_sha256,
    }
    selected = []
    for document in documents:
        if any(value is not None and document.get(key) != value for key, value in keys.items()):
            continue
        request_metadata = document.get("request_metadata") or {}
        if metadata and any(
            str(request_metadata.get(key)) != str(value) for key, value in metadata.items()
        ):
            continue
        selected.append(document)
    return selected


__all__ = [
    "SCHEMA",
    "ToolCaptureSink",
    "annotate_tool_capture",
    "begin_tool_call",
    "blob_capture_sink",
    "configure_tool_capture",
    "current_capture_id",
    "enabled",
    "file_capture_sink",
    "finish_tool_call",
    "load_tool_captures",
    "match_tool_captures",
    "record_hash",
    "record_output",
    "record_server_tool_outputs",
    "reset_tool_capture",
    "set_response_id",
    "tool_capture_health",
    "tool_capture_scope",
    "verify_tool_capture",
]

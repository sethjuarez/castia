"""Unit tests for GenAI content-recording gating in observability.

Pure precedence logic -- no cloud, creds, or network. The Foundry GenAI
instrumentor is mocked so we can assert exactly what ``enable_content_recording``
value ``_enable_genai_tracing`` forwards to
``AIProjectInstrumentor().instrument(...)`` under each arg/env/default case.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

import pytest

from castia import observability

_CONTENT_ENV = "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"
_GENAI_ENV = "AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"


@contextmanager
def _patched_instrumentor():
    """Patch the instrumentor + guard so only resolution logic runs."""
    instrumentor = mock.MagicMock()
    fake_module = mock.MagicMock(AIProjectInstrumentor=instrumentor)
    with mock.patch.dict(
        "sys.modules", {"azure.ai.projects.telemetry": fake_module}
    ), mock.patch.object(observability, "_guard_instrumentor_recording"):
        yield instrumentor


def _recorded_content_flag(instrumentor: mock.MagicMock) -> bool:
    instrumentor.return_value.instrument.assert_called_once()
    return instrumentor.return_value.instrument.call_args.kwargs[
        "enable_content_recording"
    ]


# -- _resolve_flag: arg > env > default -------------------------------------


def test_resolve_flag_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    assert observability._resolve_flag(False, _CONTENT_ENV, True) is False
    assert observability._resolve_flag(True, _CONTENT_ENV, False) is True


def test_resolve_flag_env_used_when_arg_none(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "TRUE")  # case-insensitive
    assert observability._resolve_flag(None, _CONTENT_ENV, False) is True
    monkeypatch.setenv(_CONTENT_ENV, "false")
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is False
    monkeypatch.setenv(_CONTENT_ENV, "nonsense")  # any non-"true" is False
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is False


def test_resolve_flag_default_when_arg_and_env_absent(monkeypatch):
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    assert observability._resolve_flag(None, _CONTENT_ENV, False) is False
    assert observability._resolve_flag(None, _CONTENT_ENV, True) is True


# -- content recording forwarded to the instrumentor ------------------------


def test_content_recording_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing(enable_content_recording=False)
    assert _recorded_content_flag(instrumentor) is False


def test_content_recording_from_env_when_arg_none(monkeypatch):
    monkeypatch.setenv(_CONTENT_ENV, "true")
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    assert _recorded_content_flag(instrumentor) is True


def test_content_recording_defaults_off(monkeypatch):
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    assert _recorded_content_flag(instrumentor) is False


# -- genai tracing gate -----------------------------------------------------


def test_genai_tracing_disabled_skips_instrumentation(monkeypatch):
    monkeypatch.delenv(_GENAI_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing(enable_genai_tracing=False)
    instrumentor.return_value.instrument.assert_not_called()
    assert observability.os.environ[_GENAI_ENV] == "false"


def test_genai_tracing_default_instruments_and_sets_env(monkeypatch):
    monkeypatch.delenv(_GENAI_ENV, raising=False)
    monkeypatch.delenv(_CONTENT_ENV, raising=False)
    with _patched_instrumentor() as instrumentor:
        observability._enable_genai_tracing()
    instrumentor.return_value.instrument.assert_called_once()
    assert observability.os.environ[_GENAI_ENV] == "true"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

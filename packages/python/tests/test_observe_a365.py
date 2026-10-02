"""Agent 365 S2S export: token resolver, agent-id mapping, and wiring."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

import pytest
from opentelemetry.sdk.trace import TracerProvider

from castia.observe import a365
from castia.observe import configuration as observability

_HOSTED_ENV = {
    "FOUNDRY_AGENT_TENANT_ID": "tenant-1",
    "FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID": "blueprint-1",
    "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID": "instance-default",
    "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "instance-turn",
}
_ALL_ENV = (
    *_HOSTED_ENV,
    a365.A365_EXPORT_ENV,
    a365.A365_AGENT_INSTANCE_ID_ENV,
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "AZURE_MONITOR_CONNECTION_STRING",
)
_IDENTITY = a365.A365S2SIdentity("tenant-1", "blueprint-1", "instance-default")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ALL_ENV:
        monkeypatch.delenv(key, raising=False)


def _hosted(monkeypatch):
    for key, value in _HOSTED_ENV.items():
        monkeypatch.setenv(key, value)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _resolver(*, status=200, body=None, clock=None):
    credential = mock.MagicMock()
    credential.get_token.return_value = SimpleNamespace(token="assertion")
    factory = mock.MagicMock(return_value=credential)
    response = mock.MagicMock(status_code=status, text="denied")
    response.json.return_value = (
        body if body is not None else {"access_token": "s2s", "expires_in": 3600}
    )
    post = mock.MagicMock(return_value=response)
    resolver = a365.A365S2STokenResolver(
        _IDENTITY, credential_factory=factory, post=post, clock=clock or _Clock()
    )
    return resolver, factory, credential, post


# -- identity ---------------------------------------------------------------


def test_identity_absent_locally():
    assert a365.A365S2SIdentity.from_env() is None


def test_identity_prefers_default_instance(monkeypatch):
    _hosted(monkeypatch)
    assert a365.A365S2SIdentity.from_env() == _IDENTITY


def test_identity_falls_back_to_instance_and_honors_override(monkeypatch):
    _hosted(monkeypatch)
    monkeypatch.delenv("FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID")
    assert a365.A365S2SIdentity.from_env().instance_client_id == "instance-turn"
    monkeypatch.setenv(a365.A365_AGENT_INSTANCE_ID_ENV, "override")
    assert a365.A365S2SIdentity.from_env().instance_client_id == "override"


# -- resolver ---------------------------------------------------------------


def test_resolver_runs_blueprint_assertion_then_instance_grant():
    resolver, factory, credential, post = _resolver()

    assert resolver("instance-default", "tenant-1") == "s2s"

    factory.assert_called_once_with("blueprint-1")
    credential.get_token.assert_called_once_with("api://AzureAdTokenExchange/.default")
    url = post.call_args.args[0]
    data = post.call_args.kwargs["data"]
    assert url == "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    assert data == {
        "client_id": "instance-default",
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": "assertion",
        "scope": "api://9b975845-388f-4429-889e-eab1ef63949c/.default",
    }


def test_resolver_caches_until_near_expiry():
    clock = _Clock()
    resolver, _, _, post = _resolver(clock=clock)

    resolver("instance-default", "tenant-1")
    clock.now += 3600 - 301
    resolver("instance-default", "tenant-1")
    assert post.call_count == 1

    clock.now += 2
    resolver("instance-default", "tenant-1")
    assert post.call_count == 2


def test_permanent_failure_returns_none_and_backs_off():
    clock = _Clock()
    resolver, _, _, post = _resolver(status=403, clock=clock)

    assert resolver("instance-default", "tenant-1") is None
    assert resolver("instance-default", "tenant-1") is None
    assert post.call_count == 1

    clock.now += 31
    assert resolver("instance-default", "tenant-1") is None
    assert post.call_count == 2
    clock.now += 59
    resolver("instance-default", "tenant-1")
    assert post.call_count == 2  # backoff doubled to 60s


def test_missing_access_token_is_permanent():
    resolver, *_ = _resolver(body={})
    assert resolver("instance-default", "tenant-1") is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_failure_raises_for_replay_and_backs_off(status):
    clock = _Clock()
    resolver, _, _, post = _resolver(status=status, clock=clock)

    for _ in range(3):
        with pytest.raises(RuntimeError, match=str(status)):
            resolver("instance-default", "tenant-1")
    assert post.call_count == 1


def test_recovers_after_backoff_and_resets():
    clock = _Clock()
    resolver, _, _, post = _resolver(status=500, clock=clock)
    with pytest.raises(RuntimeError):
        resolver("instance-default", "tenant-1")

    post.return_value.status_code = 200
    clock.now += 31
    assert resolver("instance-default", "tenant-1") == "s2s"
    assert resolver._backoff == 0.0


def test_msi_unavailable_is_permanent():
    class CredentialUnavailableError(Exception):
        pass

    resolver, _, credential, post = _resolver()
    credential.get_token.side_effect = CredentialUnavailableError("no MSI")
    assert resolver("instance-default", "tenant-1") is None
    post.assert_not_called()


def test_msi_error_is_transient():
    resolver, _, credential, _ = _resolver()
    credential.get_token.side_effect = ValueError("imds timeout")
    with pytest.raises(RuntimeError, match="imds timeout"):
        resolver("instance-default", "tenant-1")


def test_resolver_skips_foreign_identity():
    resolver, factory, _, post = _resolver()
    assert resolver("contracts:17", "tenant-1") is None
    assert resolver("instance-default", "other-tenant") is None
    factory.assert_not_called()
    post.assert_not_called()


# -- agent-id enricher -----------------------------------------------------


def _ended_span(attributes):
    provider = TracerProvider()
    span = provider.get_tracer("t").start_span("chat gpt", attributes=attributes)
    span.end()
    return span


def test_enricher_maps_castia_spans_to_instance_id():
    enricher = a365.build_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
    span = _ended_span(
        {"gen_ai.agent.id": "contracts:17", "microsoft.tenant.id": "tenant-1"}
    )

    enriched = enricher(span)

    assert enriched.attributes["gen_ai.agent.id"] == "instance-default"
    assert enriched.attributes["microsoft.tenant.id"] == "tenant-1"
    assert span.attributes["gen_ai.agent.id"] == "contracts:17"


def test_enricher_fills_tenant_for_castia_source_spans():
    enricher = a365.build_agent_id_enricher(_IDENTITY, foundry_agent_id=None)
    enriched = enricher(_ended_span({"castia.telemetry.source": "castia"}))
    assert enriched.attributes["gen_ai.agent.id"] == "instance-default"
    assert enriched.attributes["microsoft.tenant.id"] == "tenant-1"


def test_enricher_leaves_foreign_spans_and_chains_previous():
    previous = mock.MagicMock(side_effect=lambda span: span)
    enricher = a365.build_agent_id_enricher(
        _IDENTITY, foundry_agent_id="contracts:17", previous=previous
    )
    span = _ended_span({"gen_ai.agent.id": "someone-else"})

    assert enricher(span) is span
    previous.assert_called_once_with(span)


def test_real_exporter_posts_to_s2s_url_with_instance_id():
    from microsoft.opentelemetry.a365.core.exporters.agent365_exporter import (
        _Agent365Exporter,
    )

    resolver, *_ = _resolver()
    exporter = _Agent365Exporter(
        token_resolver=resolver, use_s2s_endpoint=True, enable_durable_delivery=False
    )
    enricher = a365.build_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
    span = enricher(
        _ended_span(
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.agent.id": "contracts:17",
                "microsoft.tenant.id": "tenant-1",
            }
        )
    )
    with mock.patch.object(exporter._session, "post") as post:
        post.return_value = mock.MagicMock(status_code=200, headers={}, text="")
        assert exporter.export([span]).name == "SUCCESS"

    url = post.call_args.args[0]
    assert url == (
        "https://agent365.svc.cloud.microsoft/observabilityService/tenants/tenant-1"
        "/otlp/agents/instance-default/traces?api-version=1"
    )
    assert post.call_args.kwargs["headers"]["authorization"] == "Bearer " + "s2s"
    exporter.shutdown()


def _chat_span():
    return _ended_span(
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.agent.id": "contracts:17",
            "microsoft.tenant.id": "tenant-1",
        }
    )


def test_permanent_token_failure_does_not_persist_or_storm(tmp_path):
    from microsoft.opentelemetry.a365.core.exporters.agent365_exporter import (
        _Agent365Exporter,
    )

    resolver, _, _, token_post = _resolver(status=403)
    exporter = _Agent365Exporter(
        token_resolver=resolver, use_s2s_endpoint=True, storage_directory=tmp_path
    )
    enricher = a365.build_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
    try:
        with mock.patch.object(exporter._session, "post") as ingest:
            for _ in range(5):
                exporter.export([enricher(_chat_span())])
            exporter._replay.run_once()
        ingest.assert_not_called()
        assert token_post.call_count == 1
        assert exporter._storage.claim(100, 1) == []
    finally:
        exporter.shutdown()


def test_transient_token_failure_persists_without_storm(tmp_path):
    from microsoft.opentelemetry.a365.core.exporters.agent365_exporter import (
        _Agent365Exporter,
    )

    resolver, _, _, token_post = _resolver(status=503)
    exporter = _Agent365Exporter(
        token_resolver=resolver, use_s2s_endpoint=True, storage_directory=tmp_path
    )
    enricher = a365.build_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
    try:
        with mock.patch.object(exporter._session, "post") as ingest:
            for _ in range(5):
                exporter.export([enricher(_chat_span())])
            exporter._replay.run_once()
        ingest.assert_not_called()
        assert token_post.call_count == 1
        assert len(exporter._storage.claim(100, 1)) == 5
    finally:
        exporter.shutdown()


def test_install_enricher_chains_existing_and_is_idempotent():
    from microsoft.opentelemetry.a365.core.exporters import (
        enriching_span_processor as esp,
    )

    def existing(span):
        return span

    esp.unregister_span_enricher()
    esp.register_span_enricher(existing)
    try:
        a365.install_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
        installed = esp.get_span_enricher()
        assert installed.__name__ == "castia_a365_agent_id_enricher"
        a365.install_agent_id_enricher(_IDENTITY, foundry_agent_id="contracts:17")
        assert esp.get_span_enricher() is installed
    finally:
        esp.unregister_span_enricher()


# -- configure_observability wiring -----------------------------------------


def _configure():
    with (
        mock.patch.object(observability, "use_microsoft_opentelemetry") as use_otel,
        mock.patch.object(observability, "_build_agent_identity_processors", return_value=[]),
        mock.patch.object(observability, "_enable_genai_tracing"),
        mock.patch.object(a365, "install_agent_id_enricher") as install,
    ):
        observability.configure_observability()
    return use_otel.call_args.kwargs, install


def test_hosted_configures_s2s_export(monkeypatch):
    _hosted(monkeypatch)
    monkeypatch.setenv("FOUNDRY_AGENT_NAME", "contracts")
    monkeypatch.setenv("FOUNDRY_AGENT_VERSION", "17")

    kwargs, install = _configure()

    assert kwargs["enable_a365"] is True
    assert kwargs["a365_enable_observability_exporter"] is True
    assert kwargs["a365_use_s2s_endpoint"] is True
    assert isinstance(kwargs["a365_token_resolver"], a365.A365S2STokenResolver)
    install.assert_called_once_with(_IDENTITY, foundry_agent_id="contracts:17")


def test_local_run_disables_exporter_without_resolver():
    kwargs, install = _configure()

    assert kwargs["enable_a365"] is True
    assert kwargs["a365_enable_observability_exporter"] is False
    assert "a365_token_resolver" not in kwargs
    assert "a365_use_s2s_endpoint" not in kwargs
    install.assert_not_called()


def test_local_run_can_opt_into_distro_default(monkeypatch):
    monkeypatch.setenv(a365.A365_EXPORT_ENV, "true")
    kwargs, _ = _configure()
    assert kwargs["a365_enable_observability_exporter"] is True
    assert "a365_token_resolver" not in kwargs


def test_hosted_export_can_be_disabled(monkeypatch):
    _hosted(monkeypatch)
    monkeypatch.setenv(a365.A365_EXPORT_ENV, "false")
    kwargs, install = _configure()
    assert kwargs["a365_enable_observability_exporter"] is False
    assert "a365_token_resolver" not in kwargs
    install.assert_not_called()


def test_explicit_disable_hides_distro_env_during_setup(monkeypatch):
    _hosted(monkeypatch)
    monkeypatch.setenv(a365.A365_EXPORT_ENV, "false")
    monkeypatch.setenv("ENABLE_A365_OBSERVABILITY_EXPORTER", "true")
    seen = []
    with (
        mock.patch.object(
            observability,
            "use_microsoft_opentelemetry",
            side_effect=lambda **_: seen.append(os.environ.get("ENABLE_A365_OBSERVABILITY_EXPORTER")),
        ),
        mock.patch.object(observability, "_build_agent_identity_processors", return_value=[]),
        mock.patch.object(observability, "_enable_genai_tracing"),
    ):
        observability.configure_observability()

    assert seen == [None]
    assert os.environ["ENABLE_A365_OBSERVABILITY_EXPORTER"] == "true"

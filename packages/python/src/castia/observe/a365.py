"""Agent 365 observability export for hosted Foundry agents (S2S).

The Microsoft OpenTelemetry distro's default A365 token resolver asks
``DefaultAzureCredential`` for the *delegated* ``Agent365.Observability.OtelWrite``
scope. A hosted container's managed identity cannot mint delegated scopes, so
every export fails with ``invalid_scope`` and the exporter drops the batch.

Hosted Foundry agents are blueprint agents, so Castia uses the "Agent
365-enabled using S2S" flow instead:

1. The blueprint managed identity mints an ``api://AzureAdTokenExchange`` token
   (the federated-credential assertion).
2. A client-credentials grant **as the agent instance**, with that assertion as
   ``client_assertion``, mints an app-only token for the A365 resource.

The exporter builds its URL from each span's ``gen_ai.agent.id`` and
``microsoft.tenant.id``. For S2S the agent id must be the instance client id
carried by the token, while the Foundry Traces UI needs ``gen_ai.agent.id ==
"<name>:<version>"``. A distro span enricher rewrites those two attributes on
the A365 export path only; Azure Monitor keeps the original span.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger("agent")

#: App-only scope for the Agent 365 observability resource (S2S).
A365_S2S_SCOPE = "api://9b975845-388f-4429-889e-eab1ef63949c/.default"

#: ``true``/``false`` forces Castia's A365 export on or off; unset means auto
#: (on when the hosted agent identity is present).
A365_EXPORT_ENV = "CASTIA_A365_EXPORT"
#: Optional override for the agent instance client id used as the A365 agent id.
A365_AGENT_INSTANCE_ID_ENV = "CASTIA_A365_AGENT_INSTANCE_ID"

_TOKEN_EXCHANGE_SCOPE = "api://AzureAdTokenExchange/.default"
_JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_GEN_AI_AGENT_ID_KEY = "gen_ai.agent.id"
_TENANT_ID_KEY = "microsoft.tenant.id"
_CASTIA_SOURCE_KEY = "castia.telemetry.source"
_REFRESH_MARGIN_SECONDS = 300
_MIN_BACKOFF_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 900.0
_HTTP_TIMEOUT_SECONDS = 30


def _non_empty(key: str) -> str | None:
    value = os.environ.get(key, "").strip()
    return value or None


@dataclass(frozen=True)
class A365S2SIdentity:
    """The hosted identity needed to mint an A365 S2S token."""

    tenant_id: str
    blueprint_client_id: str
    instance_client_id: str

    @classmethod
    def from_env(cls) -> A365S2SIdentity | None:
        """Build from Foundry-injected env, or ``None`` when not hosted.

        The instance id is the agent's stable registered instance,
        ``FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID``; the per-turn
        ``FOUNDRY_AGENT_INSTANCE_CLIENT_ID`` is only a fallback.
        ``CASTIA_A365_AGENT_INSTANCE_ID`` overrides both.
        """
        tenant_id = _non_empty("FOUNDRY_AGENT_TENANT_ID")
        blueprint_client_id = _non_empty("FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID")
        instance_client_id = (
            _non_empty(A365_AGENT_INSTANCE_ID_ENV)
            or _non_empty("FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID")
            or _non_empty("FOUNDRY_AGENT_INSTANCE_CLIENT_ID")
        )
        if not (tenant_id and blueprint_client_id and instance_client_id):
            return None
        return cls(tenant_id, blueprint_client_id, instance_client_id)


class _PermanentTokenError(RuntimeError):
    """A token failure that retrying will not fix (configuration or consent)."""


class A365S2STokenResolver:
    """Synchronous ``(agent_id, tenant_id) -> token`` resolver for the exporter.

    The distro calls it from the batch exporter's worker thread, so it is
    synchronous and thread-safe. The token is cached until shortly before it
    expires.

    Failure semantics follow the exporter's contract. A transient failure
    (token endpoint 5xx/408/429, network, managed-identity hiccup) raises, so
    the exporter persists the payload for durable replay. A permanent failure
    (token endpoint 4xx, no ``access_token``, managed identity unavailable)
    returns ``None``, so the chunk is dropped rather than queued forever.
    Either way, failures are cached with exponential backoff (30 s to 15 min),
    so export and replay passes do not hammer the token endpoint, and a
    warning is logged once per backoff window.
    """

    def __init__(
        self,
        identity: A365S2SIdentity,
        *,
        credential_factory: Callable[[str], Any] | None = None,
        post: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._identity = identity
        self._credential_factory = credential_factory or _managed_identity_credential
        self._post = post or _httpx_post
        self._clock = clock
        self._credential: Any = None
        self._token: str | None = None
        self._expires_at = 0.0
        self._failure: Exception | None = None
        self._retry_at = 0.0
        self._backoff = 0.0
        self._lock = threading.Lock()

    def __call__(self, agent_id: str, tenant_id: str) -> str | None:
        identity = self._identity
        if agent_id != identity.instance_client_id or tenant_id != identity.tenant_id:
            _logger.debug(
                "A365 export skipped for foreign identity (agent %s, tenant %s)",
                agent_id,
                tenant_id,
            )
            return None
        with self._lock:
            now = self._clock()
            if self._token and now < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return self._token
            if self._failure is not None and now < self._retry_at:
                return self._fail(self._failure)
            try:
                token, expires_in = self._mint()
            except Exception as exc:  # noqa: BLE001 - token failures must never break export
                self._backoff = min(
                    max(self._backoff * 2, _MIN_BACKOFF_SECONDS), _MAX_BACKOFF_SECONDS
                )
                self._failure = exc
                self._retry_at = now + self._backoff
                _logger.warning(
                    "Agent 365 S2S token failed for agent instance %s (%s); "
                    "retrying in %.0fs: %s",
                    identity.instance_client_id,
                    "permanent" if isinstance(exc, _PermanentTokenError) else "transient",
                    self._backoff,
                    exc,
                )
                return self._fail(exc)
            self._token = token
            self._expires_at = now + expires_in
            self._failure = None
            self._backoff = 0.0
            return token

    @staticmethod
    def _fail(exc: Exception) -> None:
        if isinstance(exc, _PermanentTokenError):
            return
        raise RuntimeError(f"A365 S2S token unavailable: {exc}") from exc

    def _mint(self) -> tuple[str, float]:
        identity = self._identity
        if self._credential is None:
            self._credential = self._credential_factory(identity.blueprint_client_id)
        try:
            assertion = self._credential.get_token(_TOKEN_EXCHANGE_SCOPE).token
        except Exception as exc:
            if type(exc).__name__ == "CredentialUnavailableError":
                raise _PermanentTokenError(
                    f"blueprint managed identity unavailable: {exc}"
                ) from exc
            raise
        response = self._post(
            f"https://login.microsoftonline.com/{identity.tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": identity.instance_client_id,
                "grant_type": "client_credentials",
                "client_assertion_type": _JWT_BEARER,
                "client_assertion": assertion,
                "scope": A365_S2S_SCOPE,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        status = response.status_code
        if status != 200:
            error = _PermanentTokenError if _is_permanent_status(status) else RuntimeError
            raise error(f"token endpoint returned {status}: {response.text[:500]}")
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise _PermanentTokenError("token endpoint response had no access_token")
        return token, float(body.get("expires_in", 3600))


def _is_permanent_status(status: int) -> bool:
    return 400 <= status < 500 and status not in (408, 429)


def _managed_identity_credential(client_id: str) -> Any:
    from azure.identity import ManagedIdentityCredential

    return ManagedIdentityCredential(client_id=client_id)


def _httpx_post(url: str, **kwargs: Any) -> Any:
    import httpx

    return httpx.post(url, **kwargs)


def build_agent_id_enricher(
    identity: A365S2SIdentity,
    *,
    foundry_agent_id: str | None,
    previous: Callable[[Any], Any] | None = None,
) -> Callable[[Any], Any]:
    """Return a span enricher that maps Castia spans to the A365 S2S identity.

    Applies to spans Castia stamped (``castia.telemetry.source == "castia"``) or
    that carry Castia's Foundry agent id (``"<name>:<version>"``). Runs only in
    the A365 export pipeline, so Azure Monitor and the Foundry Traces UI still
    see ``"<name>:<version>"``. ``previous`` chains an enricher that a distro
    instrumentor registered first.
    """
    from microsoft.opentelemetry.a365.core.exporters.enriched_span import (
        EnrichedReadableSpan,
    )

    extra = {
        _GEN_AI_AGENT_ID_KEY: identity.instance_client_id,
        _TENANT_ID_KEY: identity.tenant_id,
    }

    def castia_a365_agent_id_enricher(span: Any) -> Any:
        if previous is not None:
            span = previous(span)
        attributes = span.attributes or {}
        owned = attributes.get(_CASTIA_SOURCE_KEY) == "castia" or (
            foundry_agent_id is not None
            and attributes.get(_GEN_AI_AGENT_ID_KEY) == foundry_agent_id
        )
        if not owned:
            return span
        return EnrichedReadableSpan(span, extra_attributes=extra)

    return castia_a365_agent_id_enricher


def install_agent_id_enricher(
    identity: A365S2SIdentity, *, foundry_agent_id: str | None
) -> None:
    """Register the agent-id enricher with the distro's A365 export processor.

    ``register_span_enricher`` is the distro's own extension point; it holds a
    single enricher, so an existing one (for example Agent Framework's) is
    chained rather than replaced. Castia installs this right after the distro
    registers its instrumentors. If one of them is later un-instrumented, it
    clears the slot and the mapping is lost until the next
    ``configure_observability``. Production agents do not un-instrument, so
    Castia accepts that edge case rather than patching distro internals.
    """
    from microsoft.opentelemetry.a365.core.exporters.enriching_span_processor import (
        get_span_enricher,
        register_span_enricher,
        unregister_span_enricher,
    )

    previous = get_span_enricher()
    if previous is not None:
        if getattr(previous, "__name__", "") == "castia_a365_agent_id_enricher":
            return
        unregister_span_enricher()
    register_span_enricher(
        build_agent_id_enricher(
            identity, foundry_agent_id=foundry_agent_id, previous=previous
        )
    )


@dataclass
class A365ExportPlan:
    """How ``configure_observability`` should configure A365 export."""

    options: dict[str, Any]
    identity: A365S2SIdentity | None = None
    # The distro ORs its own env var with the argument, so an explicit off
    # must hide that variable while the distro configures.
    mask_distro_env: bool = False


def plan_a365_export() -> A365ExportPlan:
    """Decide the A365 exporter options from the environment.

    * Hosted identity present: S2S export with Castia's token resolver.
    * ``CASTIA_A365_EXPORT=false``: exporter off.
    * No hosted identity (local runs): exporter off, so there are no token
      warnings. ``CASTIA_A365_EXPORT=true`` (or the distro's
      ``ENABLE_A365_OBSERVABILITY_EXPORTER``) restores the distro default.
    """
    raw = os.environ.get(A365_EXPORT_ENV)
    forced = None if raw is None or not raw.strip() else raw.strip().lower() == "true"
    if forced is False:
        return A365ExportPlan(
            {"a365_enable_observability_exporter": False}, mask_distro_env=True
        )

    identity = A365S2SIdentity.from_env()
    if identity is None:
        return A365ExportPlan({"a365_enable_observability_exporter": bool(forced)})

    return A365ExportPlan(
        {
            "a365_enable_observability_exporter": True,
            "a365_token_resolver": A365S2STokenResolver(identity),
            "a365_use_s2s_endpoint": True,
        },
        identity=identity,
    )

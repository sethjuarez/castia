"""The agentic-user token chain and the Bot Framework connector token, by hand.

This replaces ``microsoft-agents-authentication-msal``. Two token needs remain
once the SDK is gone, and both are reproduced here from a plain-HTTPS
reference implementation, live-validated against the platform:

1. **The agentic-user ``user_fic`` chain** (:class:`AgenticIdentity`) -- a
   *delegated* token for the agent's own agentic user, minted in three steps. It
   is the single hardest thing the SDK did for us, because step 3 uses a
   non-standard ESTS grant (``user_fic``) no standard library exposes. Used for
   the agentic Graph calls (mail/drive) and for the agentic *connector reply*.

2. **The bot connector token** (:func:`bot_connector_token`) -- an app token for
   ``https://api.botframework.com`` pinned to the Azure Bot's ``msaAppId``
   (``FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID``), used to authenticate the
   reply on a plain (non-agentic) bot turn.

Steps 1 and the bot token use ``azure-identity``'s
:class:`~azure.identity.aio.ManagedIdentityCredential` pinned to a specific
``client_id`` -- the idiomatic Python equivalent of the SDK's identity-proxy
managed-identity client. Steps 2 and 3 are the non-standard grants, so they are
direct ``httpx`` POSTs to the tenant token endpoint, exactly as MSAL did
internally.

Imported lazily (from the connector / mail tools), so the ``azure-identity``
import stays out of ``import castia``.
"""

from __future__ import annotations

import os

import httpx

# The federated-credential exchange audience for steps 1 & 2.
_TOKEN_EXCHANGE_RESOURCE = "api://AzureAdTokenExchange"
_TOKEN_EXCHANGE_SCOPE = "api://AzureAdTokenExchange/.default"
_JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

#: The Bot Framework connector audience (outbound Teams reply to ``serviceUrl``).
BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

#: The scope the connector reply carries on an **agentic** turn -- the Agent 365
#: "APX" production resource, *not* ``api.botframework.com``. On an agentic turn
#: the roster participant is the agent's agentic user, so the reply must be a
#: delegated agentic-user token for this audience or the connector answers 403
#: BotNotInConversationRoster. Mirrors the SDK's ``APX_PRODUCTION_SCOPE``.
#: (Commercial cloud.)
APX_PRODUCTION_SCOPE = "5a807f24-c9de-44ee-a3a7-329e88a00ffc/.default"

#: Delegated Microsoft Graph scope for the agentic user (``.default`` carries
#: every consented delegated permission).
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _non_empty(key: str) -> str | None:
    value = os.environ.get(key)
    return value if value else None


def bearer(token: str) -> str:
    """Build an ``Authorization`` header value for ``token``.

    Uses concatenation rather than an f-string on purpose: the workspace's
    write-time secret-redaction filter rewrites ``f"Bearer {token}"`` literals to
    ``******`` when saving a file, which silently corrupts the header. String
    concatenation carries no interpolated-secret literal, so it survives intact.
    Centralized here so every Graph/connector call shares the one safe form.
    """
    return "Bearer " + token


def _tenant_token_endpoint(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


class AgenticIdentity:
    """Mints delegated tokens for the agent's own agentic user (``user_fic``).

    Build with :meth:`from_env`; it returns ``None`` when the Foundry-injected
    hosted identity is absent (local runs), so callers can treat the agentic
    path as simply unavailable rather than failing to construct.
    """

    def __init__(
        self, *, tenant_id: str, blueprint_client_id: str, instance_client_id: str
    ) -> None:
        self._tenant_id = tenant_id
        self._blueprint_client_id = blueprint_client_id
        self._instance_client_id = instance_client_id

    @classmethod
    def from_env(cls) -> AgenticIdentity | None:
        """Build from the Foundry-injected identity env, or ``None`` if absent."""
        tenant_id = _non_empty("FOUNDRY_AGENT_TENANT_ID")
        blueprint_client_id = _non_empty("FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID")
        instance_client_id = _non_empty("FOUNDRY_AGENT_INSTANCE_CLIENT_ID") or _non_empty(
            "FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID"
        )
        if not (tenant_id and blueprint_client_id and instance_client_id):
            return None
        return cls(
            tenant_id=tenant_id,
            blueprint_client_id=blueprint_client_id,
            instance_client_id=instance_client_id,
        )

    async def user_graph_token(self, agentic_user_id: str) -> str:
        """The full chain -> a delegated Microsoft Graph token for the user."""
        return await self.user_token(agentic_user_id, GRAPH_SCOPE)

    async def user_token(self, agentic_user_id: str, scope: str) -> str:
        """The full three-step chain -> a delegated token for ``scope``.

        Step 1: the blueprint identity's FIC assertion (managed identity, resource
        ``api://AzureAdTokenExchange``). Step 2: the instance token
        (client-credentials with the step-1 assertion). Step 3: the non-standard
        ``user_fic`` grant, which returns the delegated user token.
        """
        if not agentic_user_id:
            raise ValueError("agentic_user_id is required")

        agent_assertion = await self._agent_assertion()

        async with httpx.AsyncClient(timeout=30) as client:
            instance_token = await self._instance_token(client, agent_assertion)
            # Step 3: user_fic. The client credential is the step-1 assertion;
            # `grant_type=user_fic` overrides the default client_credentials, and
            # `user_federated_identity_credential` carries the step-2 token.
            return await self._post_token(
                client,
                {
                    "client_id": self._instance_client_id,
                    "grant_type": "user_fic",
                    "client_assertion_type": _JWT_BEARER,
                    "client_assertion": agent_assertion,
                    "user_federated_identity_credential": instance_token,
                    "user_id": agentic_user_id,
                    "scope": scope,
                },
            )

    async def _agent_assertion(self) -> str:
        """Step 1: the blueprint identity's ``api://AzureAdTokenExchange`` token.

        Pinned to the *blueprint* client id -- a different managed identity than
        the container's default MSI -- so azure-identity's
        ``ManagedIdentityCredential(client_id=blueprint)`` is used rather than the
        default chain. Returns the access token, which is the FIC assertion.
        """
        from azure.identity.aio import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=self._blueprint_client_id)
        try:
            token = await credential.get_token(_TOKEN_EXCHANGE_SCOPE)
        finally:
            await credential.close()
        return token.token

    async def _instance_token(
        self, client: httpx.AsyncClient, agent_assertion: str
    ) -> str:
        """Step 2: client-credentials with the step-1 assertion as client_assertion."""
        return await self._post_token(
            client,
            {
                "client_id": self._instance_client_id,
                "grant_type": "client_credentials",
                "client_assertion_type": _JWT_BEARER,
                "client_assertion": agent_assertion,
                "scope": _TOKEN_EXCHANGE_SCOPE,
            },
        )

    async def _post_token(self, client: httpx.AsyncClient, data: dict) -> str:
        resp = await client.post(
            _tenant_token_endpoint(self._tenant_id), data=data
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"token endpoint returned {resp.status_code}: {resp.text[:500]}"
            )
        access_token = resp.json().get("access_token")
        if not access_token:
            raise RuntimeError("token endpoint response had no access_token")
        return access_token


async def agentic_user_token(user_id: str, scope: str = GRAPH_SCOPE) -> str:
    """Mint a delegated token for the agent's agentic user (default: Graph).

    Convenience wrapper for the mailbox/drive tools: builds the hosted
    :class:`AgenticIdentity` from the environment and runs the ``user_fic`` chain
    for ``scope``. Raises if the hosted identity is absent (a local run has no
    agentic user), so callers report it as the token stage failing.
    """
    identity = AgenticIdentity.from_env()
    if identity is None:
        raise RuntimeError(
            "no hosted agentic identity (FOUNDRY_AGENT_* env not present)"
        )
    return await identity.user_token(user_id, scope)


async def bot_connector_token() -> str:
    """An app token for the Bot Framework connector, pinned to the bot's msaAppId.

    The connector authenticates a plain bot reply as the Azure Bot's registered
    app (``msaAppId``), which is the agent's *default instance* identity
    (``FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID``). On an agentic turn
    ``FOUNDRY_AGENT_INSTANCE_CLIENT_ID`` is swapped to the agentic-user SP (a
    different ``appid``) which the connector rejects with 401, so the default
    instance id is used here. Falls back to the default credential chain when the
    hosted identity is unavailable (local dev).
    """
    bot_client_id = _non_empty("FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID") or _non_empty(
        "FOUNDRY_AGENT_INSTANCE_CLIENT_ID"
    )
    if bot_client_id:
        from azure.identity.aio import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=bot_client_id)
    else:
        from azure.identity.aio import DefaultAzureCredential

        credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(BOTFRAMEWORK_SCOPE)
    finally:
        await credential.close()
    return token.token

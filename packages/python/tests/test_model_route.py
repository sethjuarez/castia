"""``Model(route="account")``: the account ``/openai/v1/`` Responses route.

MAI deployments reject the Foundry project route, so ``Model`` can call the AI
Services account directly while keeping its tracing and tool loop. These cases
pin route precedence, strict endpoint normalization, account client
construction (no project endpoint required), and the ``castia.model.route``
span attribute on every call path. No network or Azure credential is used.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from castia.inference.model import Model, _resolve_route, account_openai_base_url
from castia.observe.tracing import clear_trace_sinks, register_trace_sink

_ENV = (
    "MODEL_ROUTE",
    "MODEL_ACCOUNT_ENDPOINT",
    "MODEL_REASONING_EFFORT",
    "FOUNDRY_PROJECT_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "MAI-DS-R1")


@pytest.fixture
def fake_openai(monkeypatch):
    built: list[dict] = []

    class AsyncOpenAI:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.kwargs = kwargs

    class DefaultAzureCredential:
        pass

    def get_bearer_token_provider(credential, scope):
        async def provider():
            return f"token-for:{scope}"

        provider.scope = scope
        provider.credential = credential
        return provider

    aio = types.ModuleType("azure.identity.aio")
    aio.DefaultAzureCredential = DefaultAzureCredential
    aio.get_bearer_token_provider = get_bearer_token_provider
    openai = types.ModuleType("openai")
    openai.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "azure.identity.aio", aio)
    monkeypatch.setitem(sys.modules, "openai", openai)
    return built


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://acct.services.ai.azure.com", "https://acct.services.ai.azure.com/openai/v1/"),
        ("https://acct.services.ai.azure.com/", "https://acct.services.ai.azure.com/openai/v1/"),
        ("https://ACCT.services.ai.azure.com/openai/v1", "https://acct.services.ai.azure.com/openai/v1/"),
        ("https://acct.openai.azure.com/openai/", "https://acct.openai.azure.com/openai/v1/"),
        ("https://acct.cognitiveservices.azure.com", "https://acct.cognitiveservices.azure.com/openai/v1/"),
        (
            " https://acct.services.ai.azure.com/api/projects/proj/ ",
            "https://acct.services.ai.azure.com/openai/v1/",
        ),
        ("https://acct.services.ai.azure.us/api/projects/p", "https://acct.services.ai.azure.us/openai/v1/"),
    ],
)
def test_account_base_url_accepts_account_and_project_endpoints(endpoint, expected):
    assert account_openai_base_url(endpoint) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://acct.services.ai.azure.com",
        "https://acct.services.ai.azure.com.evil.example",
        "https://services.ai.azure.com.example.com/openai/v1",
        "https://evil.example/acct.services.ai.azure.com",
        "https://user:pw@acct.services.ai.azure.com",
        "https://acct.services.ai.azure.com/openai/v1?api-version=x",
        "https://acct.services.ai.azure.com/#frag",
        "https://acct.services.ai.azure.com/openai/deployments/x",
        "https://acct.services.ai.azure.com/api/projects/",
        "https://acct.services.ai.azure.com/api/projects/p/extra",
        "",
    ],
)
def test_account_base_url_rejects_everything_else(endpoint):
    with pytest.raises(ValueError, match="Not a Foundry account or project endpoint"):
        account_openai_base_url(endpoint)


def test_route_precedence(monkeypatch):
    assert _resolve_route(None, None) == "project"
    assert _resolve_route(None, "https://acct.services.ai.azure.com") == "account"
    assert _resolve_route(" Account ", None) == "account"

    monkeypatch.setenv("MODEL_ACCOUNT_ENDPOINT", "https://acct.services.ai.azure.com")
    assert _resolve_route(None, None) == "account"
    # An explicit project route wins over the env account endpoint.
    assert _resolve_route("project", None) == "project"

    monkeypatch.setenv("MODEL_ROUTE", "project")
    assert _resolve_route(None, None) == "project"
    assert _resolve_route("account", None) == "account"


@pytest.mark.parametrize(
    ("route", "account_endpoint", "env_route", "match"),
    [
        ("proj", None, None, "route must be one of"),
        ("project", "https://acct.services.ai.azure.com", None, "requires route='account'"),
        (None, None, "acct", "MODEL_ROUTE must be one of"),
    ],
)
def test_route_rejects_unknown_or_contradictory(monkeypatch, route, account_endpoint, env_route, match):
    if env_route:
        monkeypatch.setenv("MODEL_ROUTE", env_route)
    with pytest.raises(ValueError, match=match):
        Model(route=route, account_endpoint=account_endpoint)


def test_explicit_account_endpoint_needs_no_project_endpoint(fake_openai):
    model = Model(account_endpoint="https://acct.services.ai.azure.com")

    assert model.route == "account"
    assert fake_openai == []  # the OpenAI client is built on first use
    client = model._client.get_openai_client()
    assert model._client.get_openai_client() is client
    assert len(fake_openai) == 1
    assert fake_openai[0]["base_url"] == "https://acct.services.ai.azure.com/openai/v1/"
    token_provider = fake_openai[0]["api_key"]
    assert callable(token_provider)
    assert token_provider.scope == "https://cognitiveservices.azure.com/.default"
    assert asyncio.run(token_provider()) == "token-for:https://cognitiveservices.azure.com/.default"


def test_account_route_derives_from_project_endpoint(monkeypatch, fake_openai):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://acct.services.ai.azure.com/api/projects/p")
    monkeypatch.setenv("MODEL_ROUTE", "account")

    model = Model()
    model._client.get_openai_client()

    assert model.route == "account"
    assert fake_openai[0]["base_url"] == "https://acct.services.ai.azure.com/openai/v1/"


def test_sovereign_cloud_uses_matching_token_audience(fake_openai):
    Model(route="account", account_endpoint="https://acct.openai.azure.us")._client.get_openai_client()
    assert fake_openai[0]["api_key"].scope == "https://cognitiveservices.azure.us/.default"


def test_account_route_without_any_endpoint_fails_fast():
    with pytest.raises(ValueError, match="account model route needs"):
        Model(route="account")


def test_use_model_and_configured_model_forward_route(fake_openai):
    from castia import configured_model, use_model
    from castia.optimizing.config import AgentConfig

    endpoint = "https://acct.services.ai.azure.com"
    assert use_model("MAI-DS-R1", route="account", account_endpoint=endpoint)().route == "account"
    config = AgentConfig(model="MAI-DS-R1", instructions="be terse", source="test")
    model = configured_model(config, route="account", account_endpoint=endpoint, reasoning_effort="low")()
    assert model.route == "account"
    assert model._reasoning == {"reasoning": {"effort": "low"}}
    assert model._instructions == "be terse"


class _Responses:
    async def create(self, **kwargs):
        if kwargs.get("stream"):
            return _empty_stream()
        return types.SimpleNamespace(output=[], output_text="ok")


async def _empty_stream():
    return
    yield  # pragma: no cover - makes this an async generator


def _routed_model(route: str | None) -> Model:
    model = Model.__new__(Model)
    model._deployment = "MAI-DS-R1"
    model._instructions = None
    model._reasoning = {}
    model._tool_definitions = ()
    if route is not None:
        model._route = route
    openai = types.SimpleNamespace(responses=_Responses())
    model._client = types.SimpleNamespace(get_openai_client=lambda: openai)
    return model


def test_route_attribute_on_every_call_path():
    records = []
    model = _routed_model("account")

    async def run():
        await model.respond("hi")
        async for _ in model.stream("hi"):  # pragma: no cover - empty stream
            pass
        await model.respond_with_tools("hi", tools=[], activity=object())

    register_trace_sink("memory", records.append)
    try:
        asyncio.run(run())
    finally:
        clear_trace_sinks()

    assert [r.attributes["castia.model.phase"] for r in records] == ["single", "stream", "tool_loop"]
    assert {r.attributes["castia.model.route"] for r in records} == {"account"}


def test_models_built_without_route_default_to_project():
    assert _routed_model(None).route == "project"

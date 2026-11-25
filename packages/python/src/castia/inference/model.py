"""The Foundry model, exposed as a single ``respond`` call, plus its dependency.

Wraps the ``AIProjectClient`` + credential so a handler never sees them, and
provides ``get_model`` for FastAPI-style injection:

    async def reply(text: str, model: Model = Depends(get_model)) -> str:
        return await model.respond(text)

``resolve`` caches the dependency, so the client + credential are built **once**
for the process instead of per message -- the latent bug in the original sample.

Azure imports are deferred into the methods so that importing this module (and
therefore ``from castia import Model``) stays cheap: telemetry must be
configured before the instrumented azure/openai libraries load.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable
from copy import deepcopy

from castia.observe.tracing import trace_attribute, trace_context_headers, trace_step
from castia.runtime.usage import record_response_usage

_logger = logging.getLogger(__name__)
_warned_deployment_mismatches: set[tuple[str, str]] = set()


def _warn_deployment_mismatch(deployment: str) -> None:
    """Warn once when code calls a model other than the labelled deployment.

    Foundry Optimizer and Monitor label an agent version with
    ``AZURE_AI_MODEL_DEPLOYMENT_NAME``, not with the model the code calls.
    """
    configured = (os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "").strip()
    pair = (deployment, configured)
    if not configured or deployment == configured or pair in _warned_deployment_mismatches:
        return
    _warned_deployment_mismatches.add(pair)
    _logger.warning(
        "Model(deployment=%r) differs from AZURE_AI_MODEL_DEPLOYMENT_NAME=%r. "
        "Foundry Optimizer and Monitor label this agent version with the env var, "
        "so usage will be attributed to the wrong model. Set the env var to the "
        "deployment the agent really calls before creating the version.",
        deployment,
        configured,
    )

# Reasoning-effort levels accepted by the Responses API for reasoning models
# (o-series, gpt-5, and their RFT-fine-tuned variants). A plain chat model
# ignores the concept; we simply omit the field for it (see _reasoning_param).
_REASONING_EFFORTS = ("minimal", "low", "medium", "high")
_PRIVATE_TOOL_PREFIX = "x-castia-"


def _reasoning_param(effort: str | None) -> dict:
    """Map a reasoning-effort level to Responses-API ``create`` kwargs.

    Returns ``{"reasoning": {"effort": <level>}}`` when ``effort`` is set, or an
    empty dict so non-reasoning models are called byte-for-byte as before. A RFT
    (reinforcement-fine-tuned) model is a reasoning model, so this is the one
    runtime knob needed to consume one well -- point :class:`Model` at the tuned
    deployment and set an effort. Raises :class:`ValueError` on an unknown level
    so a typo fails fast at construction instead of as a 400 mid-turn.
    """
    if effort is None:
        return {}
    level = str(effort).strip().lower()
    if level not in _REASONING_EFFORTS:
        raise ValueError(
            f"reasoning_effort must be one of {_REASONING_EFFORTS}, got {effort!r}"
        )
    return {"reasoning": {"effort": level}}


_ROUTES = ("project", "account")
_ROUTE_ENV = "MODEL_ROUTE"
_ACCOUNT_ENDPOINT_ENV = "MODEL_ACCOUNT_ENDPOINT"
_ACCOUNT_HOST_SUFFIXES = tuple(
    f".{service}.{cloud}"
    for service in ("services.ai", "openai", "cognitiveservices")
    for cloud in ("azure.com", "azure.us", "azure.cn")
)


def _resolve_route(route: str | None, account_endpoint: str | None) -> str:
    """Explicit ``route`` > explicit ``account_endpoint`` > env > ``project``."""
    if route is not None:
        level = str(route).strip().lower()
        if level not in _ROUTES:
            raise ValueError(f"route must be one of {_ROUTES}, got {route!r}")
        if level == "project" and account_endpoint:
            raise ValueError("account_endpoint requires route='account' (got route='project').")
        return level
    if account_endpoint:
        return "account"
    env_route = os.environ.get(_ROUTE_ENV, "").strip().lower()
    if env_route:
        if env_route not in _ROUTES:
            raise ValueError(f"{_ROUTE_ENV} must be one of {_ROUTES}, got {env_route!r}")
        return env_route
    return "account" if os.environ.get(_ACCOUNT_ENDPOINT_ENV) else "project"


def account_openai_base_url(endpoint: str) -> str:
    """Normalize an account or project endpoint to the account ``/openai/v1/`` base URL.

    Accepts an AI Services / Azure OpenAI account root (``https://<acct>.services.ai.azure.com``,
    ``.openai.azure.com``, or ``.cognitiveservices.azure.com``), the same with
    ``/openai`` or ``/openai/v1`` (trailing slash optional), or a Foundry project
    endpoint (``.../api/projects/<name>``). Returns ``https://<host>/openai/v1/``.
    Raises :class:`ValueError` for anything else, including non-HTTPS URLs,
    credentials, query strings, and unrecognized hosts or paths.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(str(endpoint).strip())
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    segments = path.split("/")
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not host.endswith(_ACCOUNT_HOST_SUFFIXES)
        or not (
            path in ("", "/openai", "/openai/v1")
            or (len(segments) == 4 and segments[1:3] == ["api", "projects"] and segments[3])
        )
    ):
        raise ValueError(
            f"Not a Foundry account or project endpoint: {endpoint!r}. Expected "
            "https://<account>.services.ai.azure.com[/openai/v1] or "
            "https://<account>.services.ai.azure.com/api/projects/<project>."
        )
    port = f":{parts.port}" if parts.port else ""
    return f"https://{host}{port}/openai/v1/"


def _account_token_scope(base_url: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(base_url).hostname or ""
    cloud = next((c for c in ("azure.us", "azure.cn") if host.endswith("." + c)), "azure.com")
    return f"https://cognitiveservices.{cloud}/.default"


class _AccountOpenAIClient:
    """Duck-types ``AIProjectClient.get_openai_client()`` for the account route.

    The ``AsyncOpenAI`` client is built on first use and reused. It holds a
    refreshing Entra token provider (never a captured token) for the
    Cognitive Services audience.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.scope = _account_token_scope(base_url)
        self._openai = None

    def get_openai_client(self):
        if self._openai is None:
            from azure.identity.aio import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )
            from openai import AsyncOpenAI

            self._openai = AsyncOpenAI(
                base_url=self.base_url,
                api_key=get_bearer_token_provider(DefaultAzureCredential(), self.scope),
            )
        return self._openai


class Model:
    """A thin ``respond(text) -> text`` wrapper over the Foundry model.

    ``route`` picks the Responses endpoint: ``"project"`` (default) uses the
    Foundry project's OpenAI client; ``"account"`` calls the AI Services
    account's ``/openai/v1/`` route directly, which some deployments (for
    example MAI models) require. ``account_endpoint`` sets the account URL
    and implies ``route="account"``; otherwise it is derived from the project
    endpoint. ``MODEL_ROUTE`` and ``MODEL_ACCOUNT_ENDPOINT`` are the
    environment equivalents; explicit arguments win.
    """

    def __init__(
        self,
        deployment: str | None = None,
        *,
        endpoint: str | None = None,
        instructions: str | None = None,
        reasoning_effort: str | None = None,
        tool_definitions: tuple[dict, ...] | list[dict] = (),
        route: str | None = None,
        account_endpoint: str | None = None,
    ) -> None:
        self._deployment = deployment or os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
        if deployment:
            _warn_deployment_mismatch(deployment)
        self._instructions = instructions
        self._tool_definitions = tuple(tool_definitions)
        # An explicit level wins; otherwise MODEL_REASONING_EFFORT lets an
        # operator switch a deployed agent onto a reasoning model with zero code.
        self._reasoning_effort = reasoning_effort or os.environ.get(
            "MODEL_REASONING_EFFORT"
        )
        # Build (and thereby validate) the reasoning kwargs once, at construction.
        self._reasoning = _reasoning_param(self._reasoning_effort)
        self._route = _resolve_route(route, account_endpoint)
        if self._route == "account":
            source = (
                account_endpoint
                or os.environ.get(_ACCOUNT_ENDPOINT_ENV)
                or endpoint
                or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
            )
            if not source:
                raise ValueError(
                    "The account model route needs account_endpoint, "
                    f"{_ACCOUNT_ENDPOINT_ENV}, or FOUNDRY_PROJECT_ENDPOINT."
                )
            self._client = _AccountOpenAIClient(account_openai_base_url(source))
            return

        from azure.ai.projects.aio import AIProjectClient
        from azure.identity.aio import DefaultAzureCredential

        self._client = AIProjectClient(
            endpoint=endpoint or os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )

    @property
    def route(self) -> str:
        """``"project"`` or ``"account"`` -- which Responses endpoint this model calls."""
        return getattr(self, "_route", "project")

    async def respond(self, text: str, *, history: list[dict] | None = None) -> str:
        """Answer ``text``; ``history`` (from :func:`castia.response_history`)
        is prior conversation items, sent before it, oldest first."""
        # The GenAI instrumentor enabled in observability._enable_genai_tracing
        # wraps this Responses API call in a `chat {model}` span carrying the
        # gen_ai.* semantic-convention attributes the Foundry Traces UI renders.
        with trace_step(
            "castia.model.respond",
            kind="model",
            attributes={
                "castia.model.phase": "single",
                "gen_ai.request.model": self._deployment,
                "castia.model.route": self.route,
            },
        ):
            _add_model_event("castia.model.request.started", phase="single")
            response = await self._client.get_openai_client().responses.create(
                model=self._deployment,
                input=_conversation_input(text, history),
                **_instructions_param(self._instructions),
                **self._reasoning,
                extra_headers=trace_context_headers(),
            )
            record_response_usage(response)
            output = response.output_text
            trace_attribute("castia.model.output_text_length", len(output or ""))
            _add_model_event("castia.model.final_response.completed", phase="single")
            return output

    async def stream(
        self, text: str, *, history: list[dict] | None = None
    ) -> AsyncIterator[str]:
        """Yield the answer to ``text`` as it is generated, delta by delta.

        Opens the Responses API in streaming mode and yields each
        ``response.output_text.delta`` as it arrives, so a handler can feed a
        :class:`~castia.messaging.streaming.Streamer` and let Teams render the reply
        being typed live::

            s = msg.stream()
            async for delta in model.stream(text):
                await s.append(delta)
            await s.finish()
        """
        with trace_step(
            "castia.model.stream",
            kind="model",
            attributes={
                "castia.model.phase": "stream",
                "gen_ai.request.model": self._deployment,
                "castia.model.route": self.route,
            },
        ):
            _add_model_event("castia.model.request.started", phase="stream")
            stream = await self._client.get_openai_client().responses.create(
                model=self._deployment,
                input=_conversation_input(text, history),
                stream=True,
                **_instructions_param(self._instructions),
                **self._reasoning,
                extra_headers=trace_context_headers(),
            )
            chunk_count = 0
            output_length = 0
            try:
                async for event in stream:
                    if getattr(event, "type", None) == "response.completed":
                        record_response_usage(getattr(event, "response", None))
                    if getattr(event, "type", None) == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        if delta:
                            chunk_count += 1
                            output_length += len(delta)
                            trace_attribute("castia.stream.chunk_count", chunk_count)
                            yield delta
            finally:
                trace_attribute("castia.stream.chunk_count", chunk_count)
                trace_attribute("castia.model.output_text_length", output_length)
                _add_model_event(
                    "castia.model.final_response.completed", phase="stream"
                )

    async def respond_with_tools(
        self,
        text: str,
        *,
        tools: list,
        activity: object,
        max_iterations: int = 4,
        extra_specs: list[dict] | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """Answer ``text`` letting the model call outbound :class:`~castia.inference.tools.Tool` s.

        Runs the Responses-API tool loop: offer the tool specs, execute any
        function calls the model emits (each wrapped in an ``execute_tool`` span,
        with the turn's identity-bearing ``activity`` injected), feed the results
        back, and repeat until the model returns text or the iteration cap is hit.
        Tool impls fail *soft* -- their diagnostic dict (or a captured exception)
        is returned to the model as the tool output rather than raising.

        ``extra_specs`` appends raw tool specs the model resolves **server-side**
        (e.g. a Foundry-toolbox ``mcp`` tool from :mod:`castia.integrations.toolbox`); they
        carry no local impl and are ignored by the ``function_call`` loop.
        """
        import json

        from castia.observe import dev_diagnostics, tool_capture
        from castia.observe.tracing import execute_tool

        function_tools = [t for t in tools if not isinstance(t, dict)]
        raw_specs = [t for t in tools if isinstance(t, dict)]
        specs = [t.spec() for t in function_tools] + [
            _public_tool_spec(spec, self._tool_definitions) for spec in raw_specs
        ] + [
            _public_tool_spec(spec, self._tool_definitions)
            for spec in list(extra_specs or [])
        ]
        by_name = {t.name: t for t in function_tools}
        client = self._client.get_openai_client()
        conversation: list = _conversation_input(text, history)

        with trace_step(
            "castia.model.respond_with_tools",
            kind="model",
            attributes={
                "castia.model.phase": "tool_loop",
                "gen_ai.request.model": self._deployment,
                "castia.model.route": self.route,
                "castia.tool.available_count": len(specs),
            },
        ):
            response = None
            for iteration in range(max_iterations):
                trace_attribute("castia.model.iteration", iteration + 1)
                _add_model_event(
                    "castia.model.request.started",
                    phase="tool_loop",
                    iteration=iteration + 1,
                )
                response = await client.responses.create(
                    model=self._deployment,
                    input=conversation,
                    tools=specs,
                    **_instructions_param(self._instructions),
                    **self._reasoning,
                    extra_headers=trace_context_headers(),
                )
                record_response_usage(response)
                tool_capture.record_server_tool_outputs(
                    response.output, iteration=iteration + 1
                )
                calls = [
                    item
                    for item in response.output
                    if getattr(item, "type", None) == "function_call"
                ]
                trace_attribute("castia.model.tool_call_count", len(calls))
                if not calls:
                    dev_diagnostics.record_response_tool_outputs(response.output)
                    output = response.output_text
                    trace_attribute("castia.model.output_text_length", len(output or ""))
                    _add_model_event(
                        "castia.model.final_response.completed",
                        phase="tool_loop",
                        iteration=iteration + 1,
                    )
                    return output

                _add_model_event(
                    "castia.model.tool_calls.requested",
                    count=len(calls),
                    names=",".join(
                        str(call.name)
                        for call in calls
                        if getattr(call, "name", None)
                    ),
                    iteration=iteration + 1,
                )
                for call in calls:
                    # Echo the model's function_call, then append our result for it.
                    conversation.append(
                        {
                            "type": "function_call",
                            "call_id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    )
                    result = await self._run_tool(
                        call, by_name, activity, execute_tool, iteration=iteration + 1
                    )
                    conversation.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": json.dumps(result),
                        }
                    )

        # Exhausted the loop still wanting tools -- return whatever text we have.
            output = (response.output_text if response else "") or (
                "I couldn't complete that in the allotted steps."
            )
            trace_attribute("castia.model.output_text_length", len(output))
            return output

    @staticmethod
    async def _run_tool(
        call, by_name: dict, activity: object, execute_tool, *, iteration: int | None = None
    ) -> dict:
        """Execute one function call, capturing every failure as a result dict."""
        import json

        from castia.observe import dev_diagnostics, tool_capture

        tool_name = str(call.name)
        tool = by_name.get(call.name)
        capture = tool_capture.begin_tool_call(
            name=tool_name,
            call_id=getattr(call, "call_id", None),
            tool_type=getattr(tool, "kind", "function") if tool is not None else None,
            arguments=getattr(call, "arguments", None),
            iteration=iteration,
        )
        with trace_step(
            "castia.tool.execute",
            kind="tool",
            attributes={"gen_ai.tool.name": tool_name},
        ):
            args: dict = {}
            diagnostic_call = None
            span = None
            if tool is None:
                trace_attribute("castia.tool.status", "error")
                trace_attribute("castia.tool.error_type", "unknown_tool")
                dev_diagnostics.record_tool_call(
                    name=tool_name,
                    arguments=getattr(call, "arguments", None),
                    status="error",
                    error_type="unknown_tool",
                    summary=f"unknown tool '{call.name}'",
                )
                _add_model_event(
                    "castia.tool.call.completed",
                    tool_name=tool_name,
                    ok=False,
                    error_type="unknown_tool",
                )
                result = {"ok": False, "detail": f"unknown tool '{call.name}'"}
                tool_capture.finish_tool_call(
                    capture, result=result, error_type="unknown_tool",
                    error_message=result["detail"],
                )
                return result
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError as exc:
                trace_attribute("castia.tool.status", "error")
                trace_attribute("castia.tool.error_type", "bad_arguments")
                dev_diagnostics.record_tool_call(
                    name=tool_name,
                    arguments=getattr(call, "arguments", None),
                    status="error",
                    error_type="bad_arguments",
                    summary=f"bad tool arguments: {exc}",
                )
                _add_model_event(
                    "castia.tool.call.completed",
                    tool_name=tool_name,
                    ok=False,
                    error_type="bad_arguments",
                )
                result = {"ok": False, "detail": f"bad tool arguments: {exc}"}
                tool_capture.finish_tool_call(
                    capture, result=result, error_type="bad_arguments",
                    error_message=result["detail"],
                )
                return result
            try:
                trace_attribute("castia.tool.argument_count", len(args))
                diagnostic_call = dev_diagnostics.record_tool_call(
                    name=tool_name,
                    arguments=args,
                    status="running",
                )
                _add_model_event("castia.tool.call.started", tool_name=tool_name)
                with execute_tool(
                    call.name,
                    tool_type=getattr(tool, "kind", "function"),
                    call_id=getattr(call, "call_id", None),
                    span_name=getattr(tool, "span_name", None),
                ) as span:
                    result = await tool.run(activity, **args)
                dev_diagnostics.update_tool_call(
                    diagnostic_call,
                    status="ok",
                    summary=result,
                )
                trace_attribute("castia.tool.status", "ok")
                _add_model_event("castia.tool.call.completed", tool_name=tool_name, ok=True)
                tool_capture.finish_tool_call(capture, result=result, span=span)
                return result
            except Exception as exc:  # noqa: BLE001 - surface to the model, don't crash the turn
                dev_diagnostics.update_tool_call(
                    diagnostic_call,
                    status="error",
                    summary=f"{type(exc).__name__}: {exc}",
                    error_type=type(exc).__name__,
                )
                trace_attribute("castia.tool.status", "error")
                trace_attribute("castia.tool.error_type", type(exc).__name__)
                _add_model_event(
                    "castia.tool.call.completed",
                    tool_name=tool_name,
                    ok=False,
                    error_type=type(exc).__name__,
                )
                result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
                tool_capture.finish_tool_call(capture, result=result, error=exc, span=span)
                return result
            except BaseException as exc:
                tool_capture.finish_tool_call(capture, error=exc, span=span)
                raise


def get_model() -> Model:
    """Dependency provider for the default Foundry model.

    Returned via ``Depends(get_model)`` and cached by the dispatcher, so the
    model client is constructed a single time rather than on every turn. The
    deployment comes from ``AZURE_AI_MODEL_DEPLOYMENT_NAME``.
    """
    return Model()


def _instructions_param(instructions: str | None) -> dict:
    """Return the Responses kwargs for optional instructions."""
    if instructions is None:
        return {}
    return {"instructions": instructions}


def _user_message_input(text: str) -> list[dict]:
    """Responses input shape whose text is visible to GenAI content recording.

    The Foundry Responses instrumentor understands explicit ``input_text`` parts.
    A structured message with ``content`` as a raw string is valid for the
    Responses API, but current telemetry records it as role-only
    (``{"role":"user"}``). This shape keeps content private unless the existing
    content-recording opt-in is enabled, because the instrumentor still owns the
    decision to include or omit the ``text`` value on exported spans.
    """
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


def _conversation_input(text: str, history: list[dict] | None) -> list[dict]:
    """Prior conversation items (oldest first) followed by the user's ``text``."""
    return [*deepcopy(list(history or [])), *_user_message_input(text)]


def _event_attributes(**attributes: object) -> dict[str, bool | int | float | str]:
    clean: dict[str, bool | int | float | str] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (bool, int, float, str)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def _add_model_event(name: str, **attributes: object) -> None:
    """Add a non-payload Castia event to the current trace span, best-effort."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span and span.is_recording():
            span.add_event(name, attributes=_event_attributes(**attributes))
    except Exception:  # noqa: BLE001 - telemetry must never break a model call
        return


def _public_tool_spec(
    spec: dict, tool_definitions: tuple[dict, ...] | list[dict] = ()
) -> dict:
    """Apply optimizer rewrites, then drop private metadata before Responses."""
    from castia.integrations.toolbox import apply_optimized_toolbox_tools
    from castia.runtime.request_context import (
        FOUNDRY_CALL_ID_HEADER,
        current_request_context,
    )

    optimized = apply_optimized_toolbox_tools(spec, tool_definitions)
    public = {
        k: v
        for k, v in optimized.items()
        if not str(k).startswith(_PRIVATE_TOOL_PREFIX)
    }
    if public.get("type") == "mcp":
        headers = {
            k: v
            for k, v in (public.get("headers") or {}).items()
            if str(k).lower() != FOUNDRY_CALL_ID_HEADER
        }
        headers.update(current_request_context().platform_headers())
        if headers:
            public["headers"] = headers
        else:
            public.pop("headers", None)
    return public


def use_model(
    deployment: str | None = None,
    *,
    endpoint: str | None = None,
    instructions: str | None = None,
    reasoning_effort: str | None = None,
    tool_definitions: tuple[dict, ...] | list[dict] = (),
    route: str | None = None,
    account_endpoint: str | None = None,
) -> Callable[[], Model]:
    """Build a model dependency bound to a specific deployment (and endpoint).

    Use this when a handler needs a particular model instead of the environment
    default::

        from castia import Agent, Depends, Model, Teams, use_model

        @app.message(Teams.direct)
        async def reply(text: str, model: Model = Depends(use_model("gpt-4o"))) -> str:
            return await model.respond(text)

    Pass ``reasoning_effort`` (``minimal|low|medium|high``) to bind a reasoning
    model -- including an RFT-fine-tuned deployment -- at a chosen effort::

        o4 = use_model("o4-mini-rft-2025", reasoning_effort="high")

    Pass ``route="account"`` for deployments that only answer on the account
    ``/openai/v1/`` route (for example MAI models)::

        mai = use_model("MAI-DS-R1", route="account")

    Each distinct provider is cached, so the client is still built once. To share
    one model across several handlers, bind it to a module-level name and reuse
    it: ``gpt4o = use_model("gpt-4o")`` then ``Depends(gpt4o)``.
    """

    def provider() -> Model:
        return Model(
            deployment=deployment,
            endpoint=endpoint,
            instructions=instructions,
            reasoning_effort=reasoning_effort,
            tool_definitions=tool_definitions,
            route=route,
            account_endpoint=account_endpoint,
        )

    return provider

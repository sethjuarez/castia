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

import os
from collections.abc import AsyncIterator, Callable

# Reasoning-effort levels accepted by the Responses API for reasoning models
# (o-series, gpt-5, and their RFT-fine-tuned variants). A plain chat model
# ignores the concept; we simply omit the field for it (see _reasoning_param).
_REASONING_EFFORTS = ("minimal", "low", "medium", "high")


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


class Model:
    """A thin ``respond(text) -> text`` wrapper over the Foundry model."""

    def __init__(
        self,
        deployment: str | None = None,
        *,
        endpoint: str | None = None,
        instructions: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        from azure.ai.projects.aio import AIProjectClient
        from azure.identity.aio import DefaultAzureCredential

        self._deployment = deployment or os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"]
        self._instructions = instructions
        # An explicit level wins; otherwise MODEL_REASONING_EFFORT lets an
        # operator switch a deployed agent onto a reasoning model with zero code.
        self._reasoning_effort = reasoning_effort or os.environ.get(
            "MODEL_REASONING_EFFORT"
        )
        # Build (and thereby validate) the reasoning kwargs once, at construction.
        self._reasoning = _reasoning_param(self._reasoning_effort)
        self._client = AIProjectClient(
            endpoint=endpoint or os.environ["FOUNDRY_PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )

    async def respond(self, text: str) -> str:
        # The GenAI instrumentor enabled in observability._enable_genai_tracing
        # wraps this Responses API call in a `chat {model}` span carrying the
        # gen_ai.* semantic-convention attributes the Foundry Traces UI renders.
        response = await self._client.get_openai_client().responses.create(
            model=self._deployment,
            input=text,
            instructions=self._instructions,
            **self._reasoning,
        )
        return response.output_text

    async def stream(self, text: str) -> AsyncIterator[str]:
        """Yield the answer to ``text`` as it is generated, delta by delta.

        Opens the Responses API in streaming mode and yields each
        ``response.output_text.delta`` as it arrives, so a handler can feed a
        :class:`~castia.streaming.Streamer` and let Teams render the reply
        being typed live::

            s = msg.stream()
            async for delta in model.stream(text):
                await s.append(delta)
            await s.finish()
        """
        stream = await self._client.get_openai_client().responses.create(
            model=self._deployment,
            input=text,
            instructions=self._instructions,
            stream=True,
            **self._reasoning,
        )
        async for event in stream:
            if getattr(event, "type", None) == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    yield delta

    async def respond_with_tools(
        self, text: str, *, tools: list, activity: object, max_iterations: int = 4
    ) -> str:
        """Answer ``text`` letting the model call outbound :class:`~castia.tools.Tool` s.

        Runs the Responses-API tool loop: offer the tool specs, execute any
        function calls the model emits (each wrapped in an ``execute_tool`` span,
        with the turn's identity-bearing ``activity`` injected), feed the results
        back, and repeat until the model returns text or the iteration cap is hit.
        Tool impls fail *soft* -- their diagnostic dict (or a captured exception)
        is returned to the model as the tool output rather than raising.
        """
        import json

        from .tracing import execute_tool

        specs = [t.spec() for t in tools]
        by_name = {t.name: t for t in tools}
        client = self._client.get_openai_client()
        conversation: list = [{"role": "user", "content": text}]

        response = None
        for _ in range(max_iterations):
            response = await client.responses.create(
                model=self._deployment,
                input=conversation,
                instructions=self._instructions,
                tools=specs,
                **self._reasoning,
            )
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                return response.output_text

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
                result = await self._run_tool(call, by_name, activity, execute_tool)
                conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )

        # Exhausted the loop still wanting tools -- return whatever text we have.
        return (response.output_text if response else "") or (
            "I couldn't complete that in the allotted steps."
        )

    @staticmethod
    async def _run_tool(call, by_name: dict, activity: object, execute_tool) -> dict:
        """Execute one function call, capturing every failure as a result dict."""
        import json

        tool = by_name.get(call.name)
        if tool is None:
            return {"ok": False, "detail": f"unknown tool '{call.name}'"}
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"ok": False, "detail": f"bad tool arguments: {exc}"}
        try:
            with execute_tool(call.name):
                return await tool.run(activity, **args)
        except Exception as exc:  # noqa: BLE001 - surface to the model, don't crash the turn
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def get_model() -> Model:
    """Dependency provider for the default Foundry model.

    Returned via ``Depends(get_model)`` and cached by the dispatcher, so the
    model client is constructed a single time rather than on every turn. The
    deployment comes from ``AZURE_AI_MODEL_DEPLOYMENT_NAME``.
    """
    return Model()


def use_model(
    deployment: str | None = None,
    *,
    endpoint: str | None = None,
    instructions: str | None = None,
    reasoning_effort: str | None = None,
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
        )

    return provider

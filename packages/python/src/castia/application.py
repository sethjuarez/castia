"""The ``Agent`` facade and the composable ``Router`` -- the surface a customer
touches.

Deliberately dependency-light: importing this module (and therefore
``from castia import Agent``) must not pull in any instrumented SDK library,
because telemetry has to be configured *before* those libraries import. So the
heavy wiring in ``server``/``model`` is imported lazily inside ``run`` and
``model``, after ``configure_observability`` has run.

Shape borrowed from FastAPI: ``@app.activity(...)`` registers a handler and hands
it back unchanged; ``app.run()`` starts the server. ``Router`` mirrors FastAPI's
``APIRouter`` so each protocol can live in its own file and be folded in with
``app.include(...)``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .surfaces import Teams

Handler = Callable[..., Awaitable[Any]]

# Protocols we can publish to Foundry -- i.e. declare in ``azure.yaml`` and expose
# on the deployed agent. ``chat`` is served locally but is *not* a
# Foundry-declarable hosted protocol, so it is deliberately absent here and is
# filtered out of any generated manifest.
PUBLISHABLE_PROTOCOLS: tuple[str, ...] = ("activity", "responses", "invocations")

# Canonical order the wire protocols are reported/emitted in.
_WIRE_ORDER: tuple[str, ...] = ("responses", "invocations", "chat")


class Router:
    """A composable collection of protocol handlers -- our ``APIRouter``.

    Exposes the same protocol decorators as :class:`Agent` (:meth:`activity` /
    :meth:`responses` / :meth:`invocations` / :meth:`chat`) but starts no server.
    Register the handlers for one protocol in their own module, then fold them
    into the app with :meth:`Agent.include`. Inspired by FastAPI's ``APIRouter`` +
    ``include_router``: each protocol can live in its own file with no
    protocol-to-protocol coupling. We *include* rather than *mount* -- our
    protocols are orthogonal wire endpoints that each answer every caller, not
    independent applications served under a URL prefix.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[tuple[Teams, ...], Handler]] = []
        self._wire: dict[str, Handler] = {}
        self._invokes: dict[str, Handler] = {}
        # Zero-arg providers each returning a list of ``castia.tools.Tool`` or
        # optimizer-aware raw specs (for example toolbox MCP specs).
        # Tools are the agent's *outbound* capabilities; declaring them here (as
        # opposed to only passing them to ``respond_with_tools`` inside a
        # handler) lets the framework see them -- so the optimizer can treat tool
        # descriptions as an optimization asset and the CLI can emit a baseline
        # ``tools.json``. Kept as providers (not materialized Tools) so any heavy
        # tool impls import lazily, exactly like the providers themselves do.
        self._tool_providers: list[Callable[[], list]] = []

    def activity(self, *surfaces: Teams) -> Callable[[Handler], Handler]:
        """Serve ``func`` over the **Activity Protocol** for the given surfaces.

        Like FastAPI's ``@app.get(path)``: it only records the route and returns
        the function unchanged. This is the one decorator with a *target* axis --
        every argument must be a :class:`Teams` surface, filtering which
        conversations reach the handler. A wire protocol has no target, so it
        lives on its own decorator (:meth:`responses` / :meth:`chat`); passing a
        non-``Teams`` value here raises rather than relying on any implicit
        Activity <-> wire mapping.
        """
        bad = [s for s in surfaces if not isinstance(s, Teams)]
        if bad:
            raise TypeError(
                "@app.activity accepts only Teams surfaces (Activity Protocol); "
                f"got {bad!r}. Serve a wire protocol with @app.responses(), "
                "@app.chat(), or @app.invocations()."
            )

        def decorator(func: Handler) -> Handler:
            self._routes.append((surfaces, func))
            return func

        return decorator

    def invoke(self, name: str) -> Callable[[Handler], Handler]:
        """Serve ``func`` over an **Activity invoke** with the given ``name``.

        The request/response sibling of :meth:`activity`. A ``message`` turn is
        fire-and-forget; an *invoke* turn (``activity.type == "invoke"``) blocks
        on a synchronous answer -- the handler's returned dict becomes the
        ``InvokeResponse`` body. Route on the invoke ``name`` (see
        :class:`castia.invokes.InvokeNames`), e.g.
        ``@app.invoke(InvokeNames.adaptive_card_action)`` for Universal Actions
        or ``@app.invoke(InvokeNames.feedback)`` for thumbs submissions. Like the
        wire decorators, one handler per name -- a duplicate raises in
        :meth:`include`. Invoke shares the ``activity`` protocol's endpoint, so it
        adds no new publishable protocol.
        """

        def decorator(func: Handler) -> Handler:
            self._invokes[name] = func
            return func

        return decorator

    def responses(self) -> Callable[[Handler], Handler]:
        """Serve ``func`` natively over the **Responses** protocol (RAPI).

        A wire-protocol sibling of :meth:`activity` with no target axis -- a
        ``/responses`` endpoint answers every caller, so there are no surfaces to
        filter on. The handler is served by us directly; there is no Activity
        translation. Use as ``@app.responses()``.
        """
        return self._wire_decorator("responses")

    def chat(self) -> Callable[[Handler], Handler]:
        """Serve ``func`` natively over the **Chat Completions** protocol (CAPI).

        The other wire-protocol sibling of :meth:`activity`; like
        :meth:`responses` it takes no target. Served on ``/chat/completions``.
        Use as ``@app.chat()``.

        Chat Completions is *local-only*: it has no target axis and, unlike
        ``responses``/``invocations``, it is not a Foundry-declarable hosted
        protocol, so the deploy generator never emits it into ``azure.yaml``.
        """
        return self._wire_decorator("chat")

    def invocations(self) -> Callable[[Handler], Handler]:
        """Serve ``func`` natively over the **Invocations** protocol.

        A wire-protocol sibling of :meth:`responses` with no target axis, served
        on ``/invocations``. Invocations is Foundry's raw pass-through protocol:
        the caller sends an arbitrary payload and we hand the text to the handler
        without imposing the OpenAI Responses/Chat envelope. It is the natural
        home for a programmatically triggered castia (schedule, webhook, or
        another agent), and -- unlike :meth:`chat` -- it *is* a publishable
        Foundry protocol. Use as ``@app.invocations()``.
        """
        return self._wire_decorator("invocations")

    def _wire_decorator(self, protocol: str) -> Callable[[Handler], Handler]:
        def decorator(func: Handler) -> Handler:
            self._wire[protocol] = func
            return func

        return decorator

    def tools(self, *providers: Callable[[], list]) -> None:
        """Declare the agent's outbound tool providers.

        Each ``provider`` is a zero-arg callable returning a list of
        :class:`~castia.tools.Tool` or optimizer-aware raw specs such as
        :func:`castia.toolbox_mcp_tool`. Declaring them makes the tool set
        *discoverable* by the framework -- distinct from merely passing tools or
        ``extra_specs`` to ``model.respond_with_tools`` inside a handler -- so the
        optimizer can treat tool descriptions as an optimization asset (see
        :func:`castia.tools_json` / :func:`castia.apply_optimized_tools`) and
        ``python -m castia optimize`` can generate a baseline ``tools.json``::

            app.tools(agent_tools)

        Providers are held (not called) until :meth:`registered_tools` runs, so
        any heavy tool impls import lazily.
        """
        self._tool_providers.extend(providers)

    def registered_tools(self) -> list:
        """The agent's declared tools, flattened across every provider.

        Calls each provider registered via :meth:`tools`; returns an empty list
        when the agent declares none. De-duplicates local function tools by name
        (first wins) so overlapping providers don't double-list a tool. Raw specs
        without a ``name`` attribute pass through for the optimizer serializer.
        """
        seen: set[str] = set()
        out: list = []
        for provider in self._tool_providers:
            for tool in provider():
                name = getattr(tool, "name", None)
                if name in seen:
                    continue
                if name is not None:
                    seen.add(name)
                out.append(tool)
        return out

    def include(self, *routers: Router) -> None:
        """Merge one or more routers' handlers into this one.

        Activity routes accumulate -- many handlers, each filtered by surface. A
        wire protocol answers every caller, so it holds exactly one handler; two
        routers registering the same wire protocol is a conflict and raises,
        rather than silently letting one shadow the other.
        """
        for router in routers:
            self._routes.extend(router._routes)
            for protocol, handler in router._wire.items():
                existing = self._wire.get(protocol)
                if existing is not None:
                    raise ValueError(
                        f"protocol {protocol!r} already has a handler "
                        f"({existing.__name__!r}); a wire protocol answers every "
                        f"caller, so it takes exactly one handler, but "
                        f"{handler.__name__!r} also registered @app.{protocol}(). "
                        "Keep a single handler per wire protocol."
                    )
                self._wire[protocol] = handler
            for name, handler in router._invokes.items():
                existing = self._invokes.get(name)
                if existing is not None:
                    raise ValueError(
                        f"invoke {name!r} already has a handler "
                        f"({existing.__name__!r}); an invoke name is answered by "
                        f"exactly one handler, but {handler.__name__!r} also "
                        f"registered @app.invoke({name!r}). Keep a single handler "
                        "per invoke name."
                    )
                self._invokes[name] = handler
            self._tool_providers.extend(router._tool_providers)

    def registered_protocols(self) -> list[str]:
        """Protocol names this router serves, in canonical order.

        ``activity`` (if any Activity routes) first, then the wire protocols in
        :data:`_WIRE_ORDER`. Includes local-only ``chat``; a caller that wants
        only publishable protocols intersects with :data:`PUBLISHABLE_PROTOCOLS`.
        """
        names: list[str] = []
        if self._routes or self._invokes:
            names.append("activity")
        names.extend(protocol for protocol in _WIRE_ORDER if protocol in self._wire)
        return names


class Agent(Router):
    """A digital-worker agent you configure with decorators, then ``run``.

    An ``Agent`` *is* a :class:`Router` (so it carries the same protocol
    decorators) plus the runtime a router lacks: a lazily-built model and a
    ``run`` that configures telemetry and serves the registered handlers.
    """

    def __init__(self, *, name: str | None = None) -> None:
        super().__init__()
        self.name = name
        self._model: Any = None

    @property
    def model(self) -> Any:
        """The Foundry model, built once on first use (not per message)."""
        if self._model is None:
            from .model import Model

            self._model = Model()
        return self._model

    def responses_only(self, *, name: str | None = None) -> Agent:
        """A responses-only projection of this agent -- the Agent Optimizer target.

        The Foundry Agent Optimizer only accepts single-protocol ``responses``
        agents; it rejects a multi-protocol agent (one that also speaks activity
        / invocations). This returns a **new** :class:`Agent` carrying just this
        agent's ``@responses`` handler and its declared :meth:`tools`, so
        optimizing the projection optimizes the *identical* handler + config the
        live agent serves -- there is no separately-maintained sibling entrypoint
        to drift. Deploy it as its own azd service, run the optimizer against it,
        apply the winning ``.agent_configs`` candidate back, then delete it.

        Raises if this agent has no ``@responses`` handler to project.
        """
        handler = self._wire.get("responses")
        if handler is None:
            raise ValueError(
                "responses_only() needs an @responses handler to project; this "
                "agent registered none. Add @app.responses() (or include a router "
                "that does) before projecting."
            )
        default = f"{self.name}-optimize" if self.name else None
        projected = Agent(name=name or default)
        projected._wire["responses"] = handler
        projected._tool_providers = list(self._tool_providers)
        return projected

    def run(self, *, host: str = "0.0.0.0", port: int = 8088) -> None:
        """Configure telemetry, then serve the registered handlers."""
        # Must happen before any instrumented SDK module is imported.
        from .observability import configure_observability

        configure_observability()

        from .server import serve

        serve(self._routes, self._wire, self._invokes, host=host, port=port)

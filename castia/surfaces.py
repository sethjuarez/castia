"""Teams surfaces a handler can subscribe to.

These are the *keys* the ``@app.message(...)`` decorator accepts, the way a URL
path is FastAPI's route key. They are a plain enum on purpose: importing this
module must stay cheap so telemetry can be configured before any instrumented
SDK module is imported (see ``application.Agent.run``). The mapping from a
surface to its activity predicate lives in ``server`` and is only resolved once
the runtime starts.
"""

from __future__ import annotations

from enum import StrEnum


class Teams(StrEnum):
    """Supported Microsoft Teams conversation surfaces.

    These are the *targets* ``@app.message(...)`` filters on -- the Activity
    Protocol has a target axis (which conversation). The native wire protocols
    have no target axis, so they are plain decorators (``@app.responses()`` /
    ``@app.chat()``) that take no surfaces, not enum members.
    """

    direct = "direct"
    group = "group"
    channel_mention = "channel_mention"

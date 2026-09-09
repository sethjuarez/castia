"""Hosted-vs-local run detection for the agent.

Once the Microsoft Agents SDK is gone, "auth" is no longer a connection manager
to construct -- the token chains live in :mod:`castia.credentials`. All that
remains here is the single environmental question every reply path asks: *is this
a hosted Foundry turn, or a local ``azd ai agent run`` (M365 Agents Playground)
turn?* Local turns are answered anonymously (the emulator expects no bearer
token), so this predicate gates whether the connector reply signs itself.
"""

from __future__ import annotations

import os


def is_local_run() -> bool:
    """True when running under ``azd ai agent run`` in anonymous local mode.

    The CLI injects ``AGENT_DIGITAL_WORKER`` and does not provide the
    Foundry-injected hosted identity, so we treat the turn as local whenever that
    flag is set or the hosted identity variables are absent. In that mode replies
    go out anonymously, matching the emulator's expectations.
    """
    if os.environ.get("AGENT_DIGITAL_WORKER"):
        return True
    return "FOUNDRY_AGENT_TENANT_ID" not in os.environ

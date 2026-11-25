from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_eval_backend(monkeypatch):
    # Without a pinned backend, `eval` commands shell out to `azd extension list`,
    # so results would depend on the developer's installed azd extensions.
    monkeypatch.setenv("CASTIA_EVAL_BACKEND", "agent")

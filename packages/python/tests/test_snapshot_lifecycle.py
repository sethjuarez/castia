import asyncio
import os

import httpx
import pytest

from castia.hosting import server, snapshot

SESSION_ENV = "FOUNDRY_AGENT_SESSION_ID"
GUID_ENV = "FOUNDRY_AGENT_SESSION_GUID"
EXTRA_ENV = "CASTIA_TEST_RESTORED_VALUE"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in (SESSION_ENV, GUID_ENV, EXTRA_ENV, "FOUNDRY_HOSTING_ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    yield
    # Endpoints write os.environ directly; monkeypatch restores what it saw.
    for name in (SESSION_ENV, GUID_ENV, EXTRA_ENV):
        os.environ.pop(name, None)


def _restore_body(session_id="s1", restore_id="r1", overrides=None):
    context = {"session_id": session_id, "restore_id": restore_id}
    if overrides is not None:
        context["session_env_overrides"] = overrides
    return {"session_context": context}


def _post_all(requests):
    async def run():
        app = server.build_app([], {}, {})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            return [await client.post(path, **kwargs) for path, kwargs in requests]

    return asyncio.run(run())


@pytest.mark.parametrize("path", snapshot.BEFORE_SNAPSHOT_PATHS)
def test_before_snapshot_is_idempotent_ok(path):
    first, second = _post_all([(path, {"json": {}}), (path, {"json": {}})])
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"status": "ok"}


@pytest.mark.parametrize("path", snapshot.BEFORE_SNAPSHOT_PATHS)
@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": b"{}", "headers": {"content-type": "text/plain"}},
        {"content": b"{not json", "headers": {"content-type": "application/json"}},
        {"json": []},
    ],
)
def test_before_snapshot_rejects_invalid_bodies(path, kwargs):
    (response,) = _post_all([(path, kwargs)])
    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_before_snapshot_request",
            "message": "The before-snapshot request is invalid.",
        }
    }


@pytest.mark.parametrize("path", snapshot.AFTER_RESTORE_PATHS)
def test_after_restore_applies_session_environment(path):
    (response,) = _post_all(
        [(path, {"json": _restore_body(overrides={EXTRA_ENV: "restored"})})]
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert os.environ[SESSION_ENV] == "s1"
    assert os.environ[EXTRA_ENV] == "restored"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_context": "s1"},
        _restore_body(session_id=""),
        _restore_body(session_id="a\0b"),
        _restore_body(restore_id=""),
        _restore_body(overrides=[]),
        _restore_body(overrides={EXTRA_ENV: 1}),
        _restore_body(overrides={"": "x"}),
        _restore_body(overrides={"A=B": "x"}),
        _restore_body(overrides={EXTRA_ENV: "a\0b"}),
        _restore_body(overrides={SESSION_ENV: "other"}),
    ],
)
def test_after_restore_rejects_invalid_context(body):
    (response,) = _post_all([("/_agent/after-restore", {"json": body})])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_after_restore_request"
    assert SESSION_ENV not in os.environ


def test_after_restore_validates_session_guid_only_when_hosted(monkeypatch):
    body = _restore_body(overrides={GUID_ENV: "not-a-guid"})
    (local,) = _post_all([("/_agent/after-restore", {"json": body})])
    assert local.status_code == 200

    monkeypatch.setenv("FOUNDRY_HOSTING_ENVIRONMENT", "1")
    bad, good = _post_all(
        [
            ("/_agent/after-restore", {"json": body}),
            (
                "/_agent/after-restore",
                {"json": _restore_body(overrides={GUID_ENV: "0" * 32})},
            ),
        ]
    )
    assert bad.status_code == 400
    assert good.status_code == 200


def test_after_restore_pins_session_and_dedupes_restore_ids():
    first, replay, other_session, next_restore = _post_all(
        [
            ("/_agent/after-restore", {"json": _restore_body()}),
            (
                "/_agent/after-restore",
                {"json": _restore_body(overrides={EXTRA_ENV: "ignored"})},
            ),
            ("/_agent/after-restore", {"json": _restore_body(session_id="s2")}),
            (
                "/_agent/after-restore",
                {"json": _restore_body(restore_id="r2", overrides={EXTRA_ENV: "v2"})},
            ),
        ]
    )
    assert first.status_code == replay.status_code == next_restore.status_code == 200
    assert other_session.status_code == 409
    assert other_session.json()["error"]["code"] == "session_mismatch"
    assert os.environ[SESSION_ENV] == "s1"
    assert os.environ[EXTRA_ENV] == "v2"


def test_snapshot_cycle_resets_after_restore():
    responses = _post_all(
        [
            ("/_agent/before-snapshot", {"json": {}}),
            ("/_agent/after-restore", {"json": _restore_body()}),
            ("/_agent/before-snapshot", {"json": {}}),
        ]
    )
    assert [r.status_code for r in responses] == [200, 200, 200]


def test_after_restore_rolls_back_when_environment_write_fails(monkeypatch):
    monkeypatch.setenv(EXTRA_ENV, "original")
    real_update = os.environ.update
    fail = [True]

    def flaky_update(values):
        real_update(values)
        if fail:
            fail.clear()
            raise OSError("boom")

    monkeypatch.setattr(os.environ, "update", flaky_update)
    failed, retry = _post_all(
        [
            (
                "/_agent/after-restore",
                {"json": _restore_body(overrides={EXTRA_ENV: "new"})},
            ),
            ("/_agent/after-restore", {"json": _restore_body(session_id="s2")}),
        ]
    )
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "after_restore_failed"
    # The retry is for a different session: proving the failure pinned nothing.
    assert retry.status_code == 200
    assert os.environ[EXTRA_ENV] == "original"
    assert os.environ[SESSION_ENV] == "s2"

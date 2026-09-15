"""Existing-job management never submits training or deploys a model."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from castia.finetuning import FineTuningClient, JobReference

ENDPOINT = "https://example.services.ai.azure.com/api/projects/test"


def client_fixture():
    transport = MagicMock()
    return FineTuningClient(ENDPOINT, client=transport), transport


def test_management_methods_use_explicit_bounded_requests():
    client, transport = client_fixture()
    jobs = transport.fine_tuning.jobs
    jobs.list.return_value.data = [1, 2, 3]
    jobs.list_events.return_value.data = ["e1", "e2"]
    jobs.checkpoints.list.return_value.data = ["c1", "c2"]
    assert client.list(limit=2, after="job-old") == [1, 2]
    assert client.events("job-1", limit=1) == ["e1"]
    assert client.checkpoints("job-1", limit=1, after="c0") == ["c1"]
    client.status("job-1")
    client.cancel("job-1")
    jobs.list.assert_called_once_with(limit=2, after="job-old", timeout=30)
    jobs.retrieve.assert_called_once_with("job-1", timeout=30)
    jobs.cancel.assert_called_once_with("job-1", timeout=30)
    jobs.create.assert_not_called()
    transport.files.create.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
def test_invalid_limits_make_no_service_calls(limit):
    client, transport = client_fixture()
    with pytest.raises(ValueError, match="limit"):
        client.list(limit=limit)
    assert not transport.mock_calls


@pytest.mark.parametrize("identifier", ["", "../other", "job?key=x", "job\nx", " job"])
def test_invalid_identifier_rejected(identifier):
    client, transport = client_fixture()
    with pytest.raises(ValueError, match="identifier"):
        client.status(identifier)
    assert not transport.mock_calls


def test_watch_terminal_and_failure_are_returned_without_cancel(monkeypatch):
    client, transport = client_fixture()
    jobs = transport.fine_tuning.jobs
    jobs.retrieve.side_effect = [
        SimpleNamespace(status="running"),
        SimpleNamespace(status="failed"),
    ]
    monkeypatch.setattr("castia.finetuning.jobs.time.sleep", lambda _: None)
    assert client.watch("job-1").status == "failed"
    jobs.cancel.assert_not_called()


def test_watch_timeout_is_bounded_and_does_not_cancel(monkeypatch):
    client, transport = client_fixture()
    transport.fine_tuning.jobs.retrieve.return_value = SimpleNamespace(status="running")
    ticks = iter([0, 0, 1, 2])
    monkeypatch.setattr("castia.finetuning.jobs.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("castia.finetuning.jobs.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError):
        client.watch("job-1", timeout=1)
    transport.fine_tuning.jobs.retrieve.assert_called_once_with("job-1", timeout=1)
    transport.fine_tuning.jobs.cancel.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout_rejected(timeout):
    with pytest.raises(ValueError):
        FineTuningClient(ENDPOINT, request_timeout=timeout)


def test_reference_roundtrip_no_overwrite_and_no_credentials(tmp_path):
    path = tmp_path / "job.json"
    ref = JobReference(ENDPOINT, "job-1")
    ref.save(path)
    assert JobReference.load(path) == ref
    with pytest.raises(FileExistsError):
        ref.save(path)
    assert set(json.loads(path.read_text())) == {
        "schema_version", "job_id", "project_endpoint",
    }
    with pytest.raises(ValueError):
        JobReference("https://user:password@example.com", "job-1")
    with pytest.raises(ValueError):
        JobReference(ENDPOINT, "job-1", schema_version=2)


def test_handoff_is_not_deployment():
    client, transport = client_fixture()
    transport.fine_tuning.jobs.retrieve.return_value = SimpleNamespace(
        status="succeeded", id="job-1", fine_tuned_model="ft:model:1",
        result_files=["file-result"],
    )
    handoff = client.deployment_handoff("job-1")
    assert handoff["deployment_status"] == "not_deployed"
    assert handoff["fine_tuned_model"] == "ft:model:1"
    assert client.result_files("job-1") == ["file-result"]
    assert all(call[0].startswith("fine_tuning.jobs.retrieve") for call in transport.mock_calls)


def test_handoff_rejects_unfinished_job():
    client, transport = client_fixture()
    transport.fine_tuning.jobs.retrieve.return_value = SimpleNamespace(status="running")
    with pytest.raises(ValueError, match="succeeded"):
        client.deployment_handoff("job-1")

def test_result_download_checks_ownership_and_cleans_partial_file(tmp_path):
    client, transport = client_fixture()
    transport.fine_tuning.jobs.retrieve.return_value = SimpleNamespace(result_files=["file-1"])
    response = transport.files.with_streaming_response.content.return_value.__enter__.return_value
    response.iter_bytes.return_value = [b"abc", b"def"]
    target = tmp_path / "result.jsonl"
    with pytest.raises(ValueError, match="not a result"):
        client.download_result("job-1", "other-file", target)
    with pytest.raises(ValueError, match="max_bytes"):
        client.download_result("job-1", "file-1", target, max_bytes=4)
    assert list(tmp_path.iterdir()) == []
    response.iter_bytes.return_value = [b"abc"]
    assert client.download_result("job-1", "file-1", target) == target
    assert target.read_bytes() == b"abc"
    with pytest.raises(FileExistsError):
        client.download_result("job-1", "file-1", target)


def test_injected_client_not_closed():
    client, transport = client_fixture()
    with client:
        pass
    transport.close.assert_not_called()


def test_management_cli_reference_conflict_fails_before_network(tmp_path, capsys):
    from castia.__main__ import main

    reference = tmp_path / "job.json"
    JobReference(ENDPOINT, "job-1").save(reference)
    assert main(["finetune", "status", "job-2", "--reference", str(reference)]) == 2
    assert "conflicts" in capsys.readouterr().err


def test_management_cli_failed_job_returns_nonzero(monkeypatch, capsys):
    from castia.__main__ import main

    client, transport = client_fixture()
    job = MagicMock()
    job.model_dump.return_value = {"status": "failed"}
    transport.fine_tuning.jobs.retrieve.return_value = job
    monkeypatch.setattr("castia.finetuning.jobs.FineTuningClient", lambda *a, **k: client)
    assert main(["finetune", "status", "job-1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"

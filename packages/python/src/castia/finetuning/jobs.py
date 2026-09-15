"""Bounded fine-tuning inspection and explicit deployment handoff."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from openai import OpenAI
    from openai.types.fine_tuning import FineTuningJob, FineTuningJobEvent
    from openai.types.fine_tuning.jobs import FineTuningJobCheckpoint


def _positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _identifier(value: str, name: str) -> str:
    if not value or value != value.strip() or any(c in value for c in "/\\?#\r\n"):
        raise ValueError(f"{name} must be a nonempty service identifier")
    return value


@dataclass(frozen=True)
class JobReference:
    """Persist only the endpoint and ID needed to resume inspection."""

    project_endpoint: str
    job_id: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        parsed = urlsplit(self.project_endpoint)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
        ):
            raise ValueError("project_endpoint must be HTTPS without credentials or query")
        _identifier(self.job_id, "job_id")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported job-reference schema_version")

    def save(self, path: str | os.PathLike[str]) -> None:
        """Create a resumable reference without replacing an existing file."""
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(asdict(self), stream, indent=2, allow_nan=False)
            stream.write("\n")

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> JobReference:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {
            "project_endpoint", "job_id", "schema_version",
        }:
            raise ValueError("invalid fine-tuning job reference")
        if not isinstance(data["project_endpoint"], str) or not isinstance(data["job_id"], str):
            raise TypeError("job reference endpoint and ID must be strings")
        return cls(**data)


class FineTuningClient:
    """Manage existing jobs; never upload, create training, or deploy a model.

    List operations read one explicitly bounded page. ``after`` allows callers
    to request another page without an unbounded automatic traversal.
    """

    def __init__(
        self, endpoint: str | None = None, *, client: OpenAI | None = None,
        request_timeout: float = 30,
    ) -> None:
        _positive(request_timeout, "request_timeout")
        self.endpoint = endpoint or os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
        self.request_timeout = request_timeout
        self._client = client
        self._owns_client = client is None
        self._project = None
        self._credential = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.endpoint:
                raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT or endpoint is required")
            JobReference(self.endpoint, "endpoint-validation")
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
            self._project = AIProjectClient(
                endpoint=self.endpoint, credential=self._credential,
            )
            self._client = self._project.get_openai_client(
                timeout=self.request_timeout, max_retries=0,
            )
        return self._client

    @staticmethod
    def _page_size(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")

    def list(self, *, limit: int = 20, after: str | None = None) -> list[FineTuningJob]:
        self._page_size(limit)
        if after is None:
            page = self.client.fine_tuning.jobs.list(
                limit=limit, timeout=self.request_timeout,
            )
        else:
            page = self.client.fine_tuning.jobs.list(
                limit=limit, after=_identifier(after, "after"), timeout=self.request_timeout,
            )
        return page.data[:limit]

    def status(self, job_id: str, *, timeout: float | None = None) -> FineTuningJob:
        request_timeout = self.request_timeout if timeout is None else timeout
        _positive(request_timeout, "timeout")
        return self.client.fine_tuning.jobs.retrieve(
            _identifier(job_id, "job_id"), timeout=request_timeout,
        )

    def watch(
        self, job_id: str, *, timeout: float = 300, poll_interval: float = 10,
    ) -> FineTuningJob:
        """Wait for a terminal state; timeout does not cancel someone else's job."""
        _positive(timeout, "timeout")
        _positive(poll_interval, "poll_interval")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"fine-tuning job {job_id} did not finish within {timeout}s")
            job = self.status(job_id, timeout=min(self.request_timeout, remaining))
            if job.status in {"succeeded", "failed", "cancelled", "canceled"}:
                return job
            time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

    def cancel(self, job_id: str) -> FineTuningJob:
        """Cancel only the explicitly identified job."""
        return self.client.fine_tuning.jobs.cancel(
            _identifier(job_id, "job_id"), timeout=self.request_timeout,
        )

    def events(
        self, job_id: str, *, limit: int = 20, after: str | None = None,
    ) -> list[FineTuningJobEvent]:
        self._page_size(limit)
        jobs = self.client.fine_tuning.jobs
        if after is None:
            page = jobs.list_events(
                _identifier(job_id, "job_id"), limit=limit, timeout=self.request_timeout,
            )
        else:
            page = jobs.list_events(
                _identifier(job_id, "job_id"), limit=limit,
                after=_identifier(after, "after"), timeout=self.request_timeout,
            )
        return page.data[:limit]

    def checkpoints(
        self, job_id: str, *, limit: int = 20, after: str | None = None,
    ) -> list[FineTuningJobCheckpoint]:
        self._page_size(limit)
        checkpoints = self.client.fine_tuning.jobs.checkpoints
        if after is None:
            page = checkpoints.list(
                _identifier(job_id, "job_id"), limit=limit, timeout=self.request_timeout,
            )
        else:
            page = checkpoints.list(
                _identifier(job_id, "job_id"), limit=limit,
                after=_identifier(after, "after"), timeout=self.request_timeout,
            )
        return page.data[:limit]

    def result_files(self, job_id: str) -> list[str]:
        """Return result file IDs, without downloading potentially sensitive data."""
        return list(self.status(job_id).result_files or [])

    def download_result(
        self, job_id: str, file_id: str, path: str | os.PathLike[str], *,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> Path:
        """Download one owned result file, with a byte limit and no overwrite."""
        _identifier(file_id, "file_id")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        target = Path(path)
        if target.exists():
            raise FileExistsError(target)
        if file_id not in self.result_files(job_id):
            raise ValueError("file_id is not a result file of the specified job")
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".castia-result-")
        try:
            with os.fdopen(fd, "wb") as stream:
                with self.client.files.with_streaming_response.content(
                    file_id, timeout=self.request_timeout,
                ) as response:
                    written = 0
                    for chunk in response.iter_bytes(chunk_size=65536):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError("result file exceeds max_bytes")
                        stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic, exclusive publication: never overwrite a concurrent writer.
            os.link(temporary, target)
            return target
        finally:
            Path(temporary).unlink(missing_ok=True)

    def deployment_handoff(self, job_id: str) -> dict[str, object]:
        """Identify a trained model for external deployment, not a deployment name."""
        job = self.status(job_id)
        if job.status != "succeeded" or not job.fine_tuned_model:
            raise ValueError("deployment handoff requires a succeeded job with a model")
        return {
            "schema_version": 1,
            "job_id": job.id,
            "project_endpoint": self.endpoint,
            "fine_tuned_model": job.fine_tuned_model,
            "result_files": list(job.result_files or []),
            "deployment_status": "not_deployed",
            "next_step": "Deploy the selected model externally, then evaluate the deployment.",
        }

    def close(self) -> None:
        if self._owns_client:
            try:
                if self._client is not None:
                    self._client.close()
            finally:
                try:
                    if self._project is not None:
                        self._project.close()
                finally:
                    if self._credential is not None:
                        self._credential.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

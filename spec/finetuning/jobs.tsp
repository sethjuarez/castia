namespace Castia.Spec.Finetuning;

@doc("Pure fine-tuning job management guards. These seams never upload files, submit training, or deploy models.")
interface FinetuningJobsRuntime {
  @sync
  @vector(#{
    name: "job-reference-normalizes-valid-input",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test", jobId: "job-1", schemaVersion: 1 },
    expected: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test", job_id: "job-1", schema_version: 1 },
  })
  @vector(#{
    name: "job-reference-rejects-query-endpoint",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test?api-version=1", jobId: "job-1", schemaVersion: 1 },
    expectedError: #{ message: "project_endpoint must be HTTPS without credentials or query" },
  })
  @vector(#{
    name: "job-reference-accepts-empty-query-marker",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test?", jobId: "job-1", schemaVersion: 1 },
    expected: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test?", job_id: "job-1", schema_version: 1 },
  })
  @vector(#{
    name: "job-reference-rejects-unsupported-schema",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test", jobId: "job-1", schemaVersion: 2 },
    expectedError: #{ message: "unsupported job-reference schema_version" },
  })
  jobReference(projectEndpoint: string, jobId: string, schemaVersion: int32): unknown;

  @sync
  @vector(#{
    name: "parse-job-reference-accepts-exact-shape",
    stage: "callable",
    input: #{ data: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test", job_id: "job-1", schema_version: 1 } },
    expected: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test", job_id: "job-1", schema_version: 1 },
  })
  @vector(#{
    name: "parse-job-reference-rejects-extra-key",
    stage: "callable",
    input: #{ data: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test", job_id: "job-1", schema_version: 1, token: "secret" } },
    expectedError: #{ message: "invalid fine-tuning job reference" },
  })
  @vector(#{
    name: "parse-job-reference-rejects-non-string-job-id",
    stage: "callable",
    input: #{ data: #{ project_endpoint: "https://example.services.ai.azure.com/api/projects/test", job_id: 5, schema_version: 1 } },
    expectedError: #{ message: "job reference endpoint and ID must be strings" },
  })
  parseJobReference(data: unknown): unknown;

  @sync
  @vector(#{
    name: "request-timeout-accepts-positive-finite",
    stage: "callable",
    input: #{ requestTimeout: 30 },
    expected: 30,
  })
  @vector(#{
    name: "request-timeout-rejects-zero",
    stage: "callable",
    input: #{ requestTimeout: 0 },
    expectedError: #{ message: "request_timeout must be finite and positive" },
  })
  validateRequestTimeout(requestTimeout: float64): unknown;

  @sync
  @vector(#{
    name: "job-page-request-bounds-limit-and-after",
    stage: "callable",
    input: #{ limit: 2, after: "job-old" },
    expected: #{ limit: 2, after: "job-old" },
  })
  @vector(#{
    name: "job-page-request-omits-null-after",
    stage: "callable",
    input: #{ limit: 20, after: null },
    expected: #{ limit: 20 },
  })
  @vector(#{
    name: "job-page-request-rejects-bool-limit",
    stage: "callable",
    input: #{ limit: true, after: null },
    expectedError: #{ message: "limit must be an integer between 1 and 100" },
  })
  @vector(#{
    name: "job-page-request-rejects-invalid-after",
    stage: "callable",
    input: #{ limit: 20, after: "../other" },
    expectedError: #{ message: "after must be a nonempty service identifier" },
  })
  pageRequest(limit: unknown, after: string | null): unknown;

  @sync
  @vector(#{
    name: "job-page-request-validates-job-id",
    stage: "callable",
    input: #{ jobId: "job-1", limit: 1, after: "cursor-1" },
    expected: #{ job_id: "job-1", limit: 1, after: "cursor-1" },
  })
  @vector(#{
    name: "job-page-request-rejects-invalid-job-id",
    stage: "callable",
    input: #{ jobId: "bad/path", limit: 1, after: null },
    expectedError: #{ message: "job_id must be a nonempty service identifier" },
  })
  @vector(#{
    name: "job-page-request-validates-limit-before-job-id",
    stage: "callable",
    input: #{ jobId: "bad/path", limit: 0, after: null },
    expectedError: #{ message: "limit must be an integer between 1 and 100" },
  })
  jobPageRequest(jobId: string, limit: unknown, after: string | null): unknown;

  @sync
  @vector(#{
    name: "page-slice-truncates-to-limit",
    stage: "callable",
    input: #{ data: #["a", "b", "c"], limit: 2 },
    expected: #["a", "b"],
  })
  @vector(#{
    name: "page-slice-rejects-float-limit",
    stage: "callable",
    input: #{ data: #["a"], limit: 1.5 },
    expectedError: #{ message: "limit must be an integer between 1 and 100" },
  })
  @vector(#{
    name: "page-slice-rejects-non-array-data",
    stage: "callable",
    input: #{ data: #{ value: "not a page" }, limit: 1 },
    expectedError: #{ message: "page data must be an array" },
  })
  pageSlice(data: unknown, limit: unknown): unknown;

  @sync
  @vector(#{
    name: "job-status-request-uses-request-timeout-by-default",
    stage: "callable",
    input: #{ jobId: "job-1", requestTimeout: 30, timeout: null },
    expected: #{ job_id: "job-1", timeout: 30 },
  })
  @vector(#{
    name: "job-status-request-overrides-timeout",
    stage: "callable",
    input: #{ jobId: "job-1", requestTimeout: 30, timeout: 1 },
    expected: #{ job_id: "job-1", timeout: 1 },
  })
  @vector(#{
    name: "job-status-request-rejects-zero-timeout",
    stage: "callable",
    input: #{ jobId: "job-1", requestTimeout: 30, timeout: 0 },
    expectedError: #{ message: "timeout must be finite and positive" },
  })
  statusRequest(jobId: string, requestTimeout: float64, timeout: float64 | null): unknown;

  @sync
  @vector(#{
    name: "job-watch-poll-timeout-clamps-remaining",
    stage: "callable",
    input: #{ requestTimeout: 30, remaining: 1 },
    expected: 1,
  })
  @vector(#{
    name: "job-watch-poll-timeout-keeps-smaller-request-timeout",
    stage: "callable",
    input: #{ requestTimeout: 30, remaining: 40 },
    expected: 30,
  })
  watchPollTimeout(requestTimeout: float64, remaining: float64): unknown;

  @sync
  @vector(#{
    name: "job-watch-sleep-clamps-to-deadline",
    stage: "callable",
    input: #{ pollInterval: 10, remaining: 3 },
    expected: 3,
  })
  @vector(#{
    name: "job-watch-sleep-never-negative",
    stage: "callable",
    input: #{ pollInterval: 10, remaining: -1 },
    expected: 0,
  })
  watchSleepDuration(pollInterval: float64, remaining: float64): unknown;

  @sync
  @vector(#{
    name: "job-watch-request-validates-polling",
    stage: "callable",
    input: #{ jobId: "job-1", timeout: 300, pollInterval: 10 },
    expected: #{ job_id: "job-1", timeout: 300, poll_interval: 10 },
  })
  @vector(#{
    name: "job-watch-request-rejects-zero-poll",
    stage: "callable",
    input: #{ jobId: "job-1", timeout: 300, pollInterval: 0 },
    expectedError: #{ message: "poll_interval must be finite and positive" },
  })
  watchRequest(jobId: string, timeout: float64, pollInterval: float64): unknown;

  @sync
  @vector(#{
    name: "job-terminal-statuses-match-python",
    stage: "callable",
    input: #{ status: "cancelled" },
    expected: true,
  })
  @vector(#{
    name: "job-terminal-american-canceled-spelling",
    stage: "callable",
    input: #{ status: "canceled" },
    expected: true,
  })
  @vector(#{
    name: "job-running-is-not-terminal",
    stage: "callable",
    input: #{ status: "running" },
    expected: false,
  })
  terminalStatus(status: string): boolean;

  @sync
  @vector(#{
    name: "job-result-files-omits-missing",
    stage: "callable",
    input: #{ job: #{ status: "running" } },
    expected: #[],
  })
  @vector(#{
    name: "job-result-files-preserves-array",
    stage: "callable",
    input: #{ job: #{ result_files: #["file-a", "file-b"] } },
    expected: #["file-a", "file-b"],
  })
  resultFiles(job: unknown): string[];

  @sync
  @vector(#{
    name: "job-download-guard-accepts-owned-file",
    stage: "callable",
    input: #{ job: #{ result_files: #["file-1"] }, fileId: "file-1", maxBytes: 4194304 },
    expected: #{ file_id: "file-1", max_bytes: 4194304 },
  })
  @vector(#{
    name: "job-download-guard-rejects-unowned-file",
    stage: "callable",
    input: #{ job: #{ result_files: #["file-1"] }, fileId: "other-file", maxBytes: 4194304 },
    expectedError: #{ message: "file_id is not a result file of the specified job" },
  })
  @vector(#{
    name: "job-download-guard-rejects-zero-max-bytes",
    stage: "callable",
    input: #{ job: #{ result_files: #["file-1"] }, fileId: "file-1", maxBytes: 0 },
    expectedError: #{ message: "max_bytes must be a positive integer" },
  })
  downloadGuard(job: unknown, fileId: string, maxBytes: unknown): unknown;

  @sync
  @vector(#{
    name: "job-download-limit-allows-exact-boundary",
    stage: "callable",
    input: #{ written: 4, maxBytes: 4 },
    expected: false,
  })
  @vector(#{
    name: "job-download-limit-rejects-byte-after-boundary",
    stage: "callable",
    input: #{ written: 5, maxBytes: 4 },
    expected: true,
  })
  downloadLimitExceeded(written: unknown, maxBytes: unknown): boolean;

  @sync
  @vector(#{
    name: "job-deployment-handoff-is-not-deployment",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test", job: #{ status: "succeeded", id: "job-1", fine_tuned_model: "ft:model:1", result_files: #["file-result"] } },
    expected: #{ schema_version: 1, job_id: "job-1", project_endpoint: "https://example.services.ai.azure.com/api/projects/test", fine_tuned_model: "ft:model:1", result_files: #["file-result"], deployment_status: "not_deployed", next_step: "Deploy the selected model externally, then evaluate the deployment." },
  })
  @vector(#{
    name: "job-deployment-handoff-rejects-running-job",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test", job: #{ status: "running", id: "job-1" } },
    expectedError: #{ message: "deployment handoff requires a succeeded job with a model" },
  })
  @vector(#{
    name: "job-deployment-handoff-rejects-missing-job-id",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.services.ai.azure.com/api/projects/test", job: #{ status: "succeeded", fine_tuned_model: "ft:model:1" } },
    expectedError: #{ message: "deployment handoff requires a succeeded job with a model" },
  })
  deploymentHandoff(projectEndpoint: string | null, job: unknown): unknown;
}

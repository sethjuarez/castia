namespace Castia.Spec.Observe;

@doc("Pure live-observe configuration guards and response classifiers; no network submission.")
interface ObserveLiveRuntime {
  @sync
  @vector(#{
    name: "validate-limits-defaults",
    stage: "callable",
    input: #{ limits: null },
    expected: #{ maxRequests: 20, maxSeconds: 120.0, maxOutputTokens: 128, maxTotalOutputTokens: 1024, maxResponseBytes: 2000000 },
  })
  @vector(#{
    name: "validate-limits-rejects-output-over-total",
    stage: "callable",
    input: #{ limits: #{ maxOutputTokens: 129, maxTotalOutputTokens: 128 } },
    expectedError: #{ message: "max_output_tokens cannot exceed max_total_output_tokens" },
  })
  validateLimits(limits: unknown): unknown;

  @sync
  @vector(#{
    name: "validate-live-config-defaults-no-network",
    stage: "callable",
    input: #{ config: #{} },
    expected: #{ projectReadScope: "https://ai.azure.com/.default", hostedScope: "https://ai.azure.com/.default", allowOptimizerSubmit: false, optimizerTimeoutSeconds: 60.0, optimizerPollSeconds: 5.0, cleanupTimeoutSeconds: 5.0, limits: #{ maxRequests: 20, maxSeconds: 120.0, maxOutputTokens: 128, maxTotalOutputTokens: 1024, maxResponseBytes: 2000000 } },
  })
  @vector(#{
    name: "validate-live-config-rejects-finetuning-endpoint",
    stage: "callable",
    input: #{ config: #{ hostedInvocationsUrl: "https://example.com/fine%5ftuning/jobs" } },
    expectedError: #{ message: "Observation probes never submit or mutate fine-tuning jobs" },
  })
  @vector(#{
    name: "validate-live-config-rejects-non-object-limits",
    stage: "callable",
    input: #{ config: #{ limits: "bad" } },
    expectedError: #{ message: "limits must be a configuration object" },
  })
  @vector(#{
    name: "validate-live-config-rejects-toolbox-tool-string",
    stage: "callable",
    input: #{ config: #{ toolboxTools: "web" } },
    expectedError: #{ message: "toolbox_tools must contain explicit known tool names" },
  })
  @vector(#{
    name: "validate-live-config-rejects-non-bool-optimizer-opt-in",
    stage: "callable",
    input: #{ config: #{ allowOptimizerSubmit: "true" } },
    expectedError: #{ message: "allow_optimizer_submit must be a boolean" },
  })
  @vector(#{
    name: "validate-live-config-rejects-optimizer-without-cleanup-budget",
    stage: "callable",
    input: #{ config: #{ projectEndpoint: "https://project.example/api/projects/demo", allowOptimizerSubmit: true, optimizerRequest: #{ options: #{ maxCandidates: 1 } }, limits: #{ maxRequests: 2, maxSeconds: 120, maxOutputTokens: 128, maxTotalOutputTokens: 1024, maxResponseBytes: 2000000 } } },
    expectedError: #{ message: "optimizer submission needs at least 3 requests and reserved cleanup time" },
  })
  validateLiveConfig(config: unknown): unknown;

  @sync
  @vector(#{
    name: "response-text-result-passes-expected-text-and-usage-only",
    stage: "callable",
    input: #{ response: #{ status: "completed", output: #[#{ type: "message", content: #[#{ type: "output_text", text: "CASTIA_OK" }] }], usage: #{ inputTokens: 7, outputTokens: 2, totalTokens: 9 } }, expected: "CASTIA_OK" },
    expected: #{ status: "pass", diagnostic: "Response text assertion passed.", category: null, evidence: #{ usage: #{ inputTokens: 7, outputTokens: 2, totalTokens: 9 }, outputCharacters: 9, cost: null } },
  })
  @vector(#{
    name: "response-text-result-fails-service-envelope",
    stage: "callable",
    input: #{ response: #{ status: "failed", outputText: "CASTIA_OK", error: #{ message: "SECRET" } }, expected: "CASTIA_OK" },
    expected: #{ status: "fail", diagnostic: "Response did not complete successfully.", category: "service", evidence: #{} },
  })
  responseTextResult(response: unknown, expected: string | null): unknown;

  @sync
  @vector(#{
    name: "stream-result-requires-deltas-and-completed-event",
    stage: "callable",
    input: #{ events: #[#{ type: "response.output_text.delta", delta: "CASTIA_" }, #{ type: "response.output_text.delta", delta: "OK" }, #{ type: "response.completed", response: #{ usage: #{ inputTokens: 1, outputTokens: 2, totalTokens: 3 } } }] },
    expected: #{ status: "pass", diagnostic: "Streaming completion and delta assertions passed.", category: null, evidence: #{ deltaCount: 2, completedCount: 1, usage: #{ inputTokens: 1, outputTokens: 2, totalTokens: 3 } } },
  })
  @vector(#{
    name: "stream-result-fails-without-completed-event",
    stage: "callable",
    input: #{ events: #[#{ type: "response.output_text.delta", delta: "CASTIA_" }, #{ type: "response.output_text.delta", delta: "OK" }] },
    expected: #{ status: "fail", diagnostic: "Streaming completion/delta assertion failed.", category: "assertion", evidence: #{ deltaCount: 2, completedCount: 0, usage: #{ inputTokens: null, outputTokens: null, totalTokens: null } } },
  })
  streamResult(events: unknown): unknown;

  @sync
  @vector(#{
    name: "optimizer-status-recognizes-id-aliases",
    stage: "callable",
    input: #{ payload: #{ operationId: "job", status: "Completed" }, expectedId: "job" },
    expected: "completed",
  })
  @vector(#{
    name: "optimizer-status-rejects-mismatched-job",
    stage: "callable",
    input: #{ payload: #{ operationId: "other", status: "Completed" }, expectedId: "job" },
    expectedError: #{ message: "Optimizer returned an invalid or mismatched job identity." },
  })
  @vector(#{
    name: "optimizer-status-rejects-invalid-result-shape",
    stage: "callable",
    input: #{ payload: #{ status: "Succeeded", result: #{ best: 7 } }, expectedId: null },
    expectedError: #{ message: "Optimizer best-candidate identity must be text." },
  })
  optimizerStatus(payload: unknown, expectedId: string | null): unknown;

  @sync
  @vector(#{
    name: "candidate-config-validates-known-fields-without-content",
    stage: "callable",
    input: #{ payload: #{ `model`: "gpt-5-mini", systemPrompt: "SECRET", tools: #[#{ type: "function" }], skills: #[], temperature: 0 } },
    expected: #{ valid: true },
  })
  @vector(#{
    name: "candidate-config-rejects-empty-config",
    stage: "callable",
    input: #{ payload: #{} },
    expectedError: #{ message: "Optimizer candidate config has no recognized configuration fields." },
  })
  candidateConfig(payload: unknown): unknown;
}

namespace Castia.Spec.Optimizing;

@doc("Pure Agent Optimizer job helpers. Network submission, polling, and disk writes stay host-specific.")
interface OptimizerJobsRuntime {
  @sync
  @vector(#{
    name: "optimizer-tool-names-from-nested-and-flat-functions",
    stage: "callable",
    input: #{ tools: #[#{ `type`: "function", `function`: #{ name: "nested" } }, #{ `type`: "function", name: "flat" }, #{ `type`: "mcp", serverLabel: "graph" }] },
    expected: #["nested", "flat"],
  })
  optimizerToolNames(tools: unknown): string[];

  @sync
  @vector(#{
    name: "optimizer-request-inlines-baseline-and-dataset",
    stage: "callable",
    input: #{
      evalConfig: #{
        agent: #{ name: "optimizer-smoke" },
        evaluators: #[#{ name: "smoke-core", version: "1" }],
        dataset: #{ localUri: "data.jsonl" },
        options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini", maxCandidates: 2 },
      },
      baseline: #{ `model`: "gpt-4o", instructions: "Use the web tool carefully.\n", tools: #[#{ `type`: "function", `function`: #{ name: "web" } }] },
      datasetItems: #[#{ query: "hello", answer: "world" }],
      validationItems: null,
      agentName: null,
      agentVersion: null,
      evalModel: null,
      optimizeModel: null,
      maxCandidates: null,
    },
    expected: #{
      target_configuration: #{ `type`: "foundry_agent", name: "optimizer-smoke" },
      optimization_model_configuration: #{ `model`: "gpt-5-mini" },
      optimization_configuration: #{
        `type`: "agent_optimization",
        goal: "improve_quality",
        evaluation_configuration: #{
          training_set: #{ `type`: "target_completion", source: #{ `type`: "inline", test_cases: #[#{ query: "hello", ground_truth: "world" }] } },
          evaluators: #[#{ name: "smoke-core", version: "1" }],
          evaluation_model: #{ `model`: "gpt-4o" },
        },
        candidate_search_configuration: #{ max_candidates: 2 },
        baseline_agent_configuration: #{ system_prompt: "Use the web tool carefully.\n", current_model: "gpt-4o", skills: #[], tools: #[#{ `type`: "function", `function`: #{ name: "web" } }] },
        agent_optimization_space: #{},
      },
    },
  })
  @vector(#{
    name: "optimizer-request-uses-registered-dataset-reference",
    stage: "callable",
    input: #{
      evalConfig: #{
        agent: #{ name: "optimizer-smoke" },
        evaluators: #["smoke-core"],
        dataset: #{ name: "smoke-data", version: 3 },
        options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini" },
      },
      baseline: #{},
      datasetItems: null,
      validationItems: null,
      agentName: null,
      agentVersion: null,
      evalModel: null,
      optimizeModel: null,
      maxCandidates: null,
    },
    expected: #{
      target_configuration: #{ `type`: "foundry_agent", name: "optimizer-smoke" },
      optimization_model_configuration: #{ `model`: "gpt-5-mini" },
      optimization_configuration: #{
        `type`: "agent_optimization",
        goal: "improve_quality",
        evaluation_configuration: #{ training_set: #{ `type`: "target_completion", source: #{ `type`: "dataset_reference", name: "smoke-data", version: "3" } }, evaluators: #[#{ name: "smoke-core" }], evaluation_model: #{ `model`: "gpt-4o" } },
        candidate_search_configuration: #{},
        baseline_agent_configuration: #{ skills: #[], tools: #[] },
        agent_optimization_space: #{},
      },
    },
  })
  @vector(#{
    name: "optimizer-request-agent-model-wins-over-baseline",
    stage: "callable",
    input: #{
      evalConfig: #{ agent: #{ name: "optimizer-smoke", `model`: "gpt-agent" }, evaluators: #["smoke-core"], dataset: #{ name: "smoke-data", version: "3" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini", maxConcurrentAgentRuns: 2 } },
      baseline: #{ `model`: "gpt-baseline" },
      datasetItems: null,
      validationItems: null,
      agentName: null,
      agentVersion: null,
      evalModel: null,
      optimizeModel: null,
      maxCandidates: null,
    },
    expected: #{
      target_configuration: #{ `type`: "foundry_agent", name: "optimizer-smoke" },
      optimization_model_configuration: #{ `model`: "gpt-5-mini" },
      optimization_configuration: #{
        `type`: "agent_optimization",
        goal: "improve_quality",
        evaluation_configuration: #{ training_set: #{ `type`: "target_completion", source: #{ `type`: "dataset_reference", name: "smoke-data", version: "3" } }, evaluators: #[#{ name: "smoke-core" }], evaluation_model: #{ `model`: "gpt-4o" }, max_concurrent_agent_runs: 2 },
        candidate_search_configuration: #{},
        baseline_agent_configuration: #{ current_model: "gpt-agent", skills: #[], tools: #[] },
        agent_optimization_space: #{},
      },
    },
  })
  @vector(#{
    name: "optimizer-request-legacy-dataset-file-inlines-items",
    stage: "callable",
    input: #{
      evalConfig: #{ agent: #{ name: "optimizer-smoke" }, evaluators: #["smoke-core"], datasetFile: "data.jsonl", options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini" } },
      baseline: #{},
      datasetItems: #[#{ query: "hello" }],
      validationItems: null,
      agentName: null,
      agentVersion: null,
      evalModel: null,
      optimizeModel: null,
      maxCandidates: null,
    },
    expected: #{
      target_configuration: #{ `type`: "foundry_agent", name: "optimizer-smoke" },
      optimization_model_configuration: #{ `model`: "gpt-5-mini" },
      optimization_configuration: #{
        `type`: "agent_optimization",
        goal: "improve_quality",
        evaluation_configuration: #{ training_set: #{ `type`: "target_completion", source: #{ `type`: "inline", test_cases: #[#{ query: "hello" }] } }, evaluators: #[#{ name: "smoke-core" }], evaluation_model: #{ `model`: "gpt-4o" } },
        candidate_search_configuration: #{},
        baseline_agent_configuration: #{ skills: #[], tools: #[] },
        agent_optimization_space: #{},
      },
    },
  })
  @vector(#{
    name: "optimizer-request-requires-agent-name",
    stage: "callable",
    input: #{ evalConfig: #{ evaluators: #["smoke"], dataset: #{ name: "data" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini" } }, baseline: #{}, datasetItems: null, validationItems: null, agentName: null, agentVersion: null, evalModel: null, optimizeModel: null, maxCandidates: null },
    expectedError: #{ message: "agent.name is required (or pass --agent)" },
  })
  @vector(#{
    name: "optimizer-request-rejects-preview-only-options",
    stage: "callable",
    input: #{ evalConfig: #{ agent: #{ name: "optimizer-smoke" }, evaluators: #["smoke"], dataset: #{ name: "data", version: "1" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini", maxStalls: 3 } }, baseline: #{}, datasetItems: null, validationItems: null, agentName: null, agentVersion: null, evalModel: null, optimizeModel: null, maxCandidates: null },
    expectedError: #{ message: "options.max_stalls is not supported: the GA optimizer has no stall budget; drop it" },
  })
  @vector(#{
    name: "optimizer-request-registered-dataset-requires-version",
    stage: "callable",
    input: #{ evalConfig: #{ agent: #{ name: "optimizer-smoke" }, evaluators: #["smoke"], dataset: #{ name: "data" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini" } }, baseline: #{}, datasetItems: null, validationItems: null, agentName: null, agentVersion: null, evalModel: null, optimizeModel: null, maxCandidates: null },
    expectedError: #{ message: "dataset.version is required for a registered dataset (dataset.name='data')" },
  })
  optimizerRequest(evalConfig: unknown, baseline: unknown, datasetItems: unknown, validationItems: unknown, agentName: string | null, agentVersion: string | null, evalModel: string | null, optimizeModel: string | null, maxCandidates: int32 | null): unknown;

  @sync
  @vector(#{
    name: "optimizer-start-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p/", action: "start", jobId: null, candidateId: null, body: #{ x: 1 } },
    expected: #{ method: "POST", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs?api-version=v1", headers: #{ `Content-Type`: "application/json" }, body: #{ x: 1 } },
  })
  @vector(#{
    name: "optimizer-candidate-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "candidate", jobId: "opt_1", candidateId: "cand_1", body: null },
    expected: #{ method: "GET", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1/candidates/cand_1?expand=mutations&api-version=v1", headers: #{}, body: null },
  })
  @vector(#{
    name: "optimizer-candidate-config-file-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "candidate_config_file", jobId: "opt_1", candidateId: "cand_1", body: null },
    expected: #{ method: "GET", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1/files?path=config.json&candidate_id=cand_1&api-version=v1", headers: #{}, body: null },
  })
  @vector(#{
    name: "optimizer-estimate-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "estimate", jobId: null, candidateId: null, body: #{ x: 1 } },
    expected: #{ method: "POST", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs:estimate?api-version=v1", headers: #{ `Content-Type`: "application/json" }, body: #{ x: 1 } },
  })
  @vector(#{
    name: "optimizer-status-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "status", jobId: "opt_1", candidateId: null, body: null },
    expected: #{ method: "GET", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1?api-version=v1", headers: #{}, body: null },
  })
  @vector(#{
    name: "optimizer-cancel-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "cancel", jobId: "opt_1", candidateId: null, body: null },
    expected: #{ method: "POST", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1:cancel?api-version=v1", headers: #{}, body: null },
  })
  @vector(#{
    name: "optimizer-status-requires-job-id",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "status", jobId: null, candidateId: null, body: null },
    expectedError: #{ message: "jobId is required" },
  })
  optimizerRestRequest(projectEndpoint: string, action: string, jobId: string | null, candidateId: string | null, body: unknown): unknown;

  @sync
  @vector(#{ name: "optimizer-terminal-statuses", stage: "callable", input: #{ status: "cancelled" }, expected: true })
  @vector(#{ name: "optimizer-skipped-is-terminal", stage: "callable", input: #{ status: "skipped" }, expected: true })
  @vector(#{ name: "optimizer-nonterminal-status", stage: "callable", input: #{ status: "in_progress" }, expected: false })
  terminalOptimizerStatus(status: string | null): boolean;

  @sync
  @vector(#{
    name: "optimizer-best-candidate-from-candidate-summary",
    stage: "callable",
    input: #{ status: #{ result: #{ candidate_summary: #{ baseline_id: "cand_0", best_id: "cand_2" } } } },
    expected: "cand_2",
  })
  @vector(#{
    name: "optimizer-best-candidate-absent-without-summary",
    stage: "callable",
    input: #{ status: #{ result: #{ candidates: #[#{ candidate_id: "legacy", score: 0.9 }] } } },
    expected: null,
  })
  bestOptimizerCandidateId(status: unknown): string | null;

  @sync
  @vector(#{
    name: "optimizer-job-id-extraction",
    stage: "callable",
    input: #{ payload: #{ id: "opt_1" } },
    expected: "opt_1",
  })
  optimizerJobId(payload: unknown): string | null;

  @sync
  @vector(#{
    name: "optimizer-candidate-apply-plan",
    stage: "callable",
    input: #{ candidateId: "cand_1", config: #{ `model`: "gpt-5-mini", temperature: 0, system_prompt: "Prefer short answers.", tools: #[#{ `type`: "function", `function`: #{ name: "web" } }], skills: #[#{ name: "tone", description: "Voice", body: "Be crisp." }] } },
    expected: #{
      candidateDir: "cand_1",
      metadata: "model: gpt-5-mini\ntemperature: 0\ninstruction_file: instructions.md\ntool_file: tools.json\ntools_file: tools.json\nskill_dir: skills\n",
      instructions: "Prefer short answers.",
      tools: "[\n  {\n    \"function\": {\n      \"name\": \"web\"\n    },\n    \"type\": \"function\"\n  }\n]\n",
      skillFiles: #[#{ path: "skills/tone/SKILL.md", content: "---\nname: tone\ndescription: Voice\n---\nBe crisp.\n" }],
    },
  })
  optimizerCandidateApplyPlan(candidateId: string, config: unknown): unknown;
}

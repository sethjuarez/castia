namespace Castia.Spec.Optimizing;

@doc("Pure Agent Optimizer job helpers. Network submission, polling, and disk writes stay host-specific.")
interface OptimizerJobsRuntime {
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
      agent: #{ agent_name: "optimizer-smoke", agent_version: "" },
      evaluators: #[#{ name: "smoke-core", version: "1" }],
      options: #{ eval_model: "gpt-4o", optimization_model: "gpt-5-mini", optimization_config: #{ `model`: "gpt-4o", system_prompt: "Use the web tool carefully.\n", skills: #[], tools: #[#{ `type`: "function", `function`: #{ name: "web" } }] }, max_candidates: 2 },
      train_dataset: #{ `type`: "inline", items: #[#{ query: "hello", answer: "world" }] },
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
      agent: #{ agent_name: "optimizer-smoke", agent_version: "" },
      evaluators: #[#{ name: "smoke-core" }],
      options: #{ eval_model: "gpt-4o", optimization_model: "gpt-5-mini", optimization_config: #{ skills: #[] } },
      train_dataset: #{ `type`: "reference", name: "smoke-data", version: "3" },
    },
  })
  @vector(#{
    name: "optimizer-request-agent-model-wins-over-baseline",
    stage: "callable",
    input: #{
      evalConfig: #{ agent: #{ name: "optimizer-smoke", `model`: "gpt-agent" }, evaluators: #["smoke-core"], dataset: #{ name: "smoke-data" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini", evaluationLevel: "medium", maxStalls: 3, maxConcurrentAgentRuns: 2 } },
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
      agent: #{ agent_name: "optimizer-smoke", agent_version: "" },
      evaluators: #[#{ name: "smoke-core" }],
      options: #{ eval_model: "gpt-4o", optimization_model: "gpt-5-mini", optimization_config: #{ `model`: "gpt-agent", skills: #[] }, evaluation_level: "medium", max_stalls: 3, max_concurrent_agent_runs: 2 },
      train_dataset: #{ `type`: "reference", name: "smoke-data" },
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
      agent: #{ agent_name: "optimizer-smoke", agent_version: "" },
      evaluators: #[#{ name: "smoke-core" }],
      options: #{ eval_model: "gpt-4o", optimization_model: "gpt-5-mini", optimization_config: #{ skills: #[] } },
      train_dataset: #{ `type`: "inline", items: #[#{ query: "hello" }] },
    },
  })
  @vector(#{
    name: "optimizer-request-requires-agent-name",
    stage: "callable",
    input: #{ evalConfig: #{ evaluators: #["smoke"], dataset: #{ name: "data" }, options: #{ evalModel: "gpt-4o", optimizationModel: "gpt-5-mini" } }, baseline: #{}, datasetItems: null, validationItems: null, agentName: null, agentVersion: null, evalModel: null, optimizeModel: null, maxCandidates: null },
    expectedError: #{ message: "agent.name is required (or pass --agent)" },
  })
  optimizerRequest(evalConfig: unknown, baseline: unknown, datasetItems: unknown, validationItems: unknown, agentName: string | null, agentVersion: string | null, evalModel: string | null, optimizeModel: string | null, maxCandidates: int32 | null): unknown;

  @sync
  @vector(#{
    name: "optimizer-start-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p/", action: "start", jobId: null, candidateId: null, body: #{ x: 1 } },
    expected: #{ method: "POST", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs?api-version=v1", headers: #{ `Foundry-Features`: "AgentsOptimization=V2Preview", `Content-Type`: "application/json" }, body: #{ inputs: #{ x: 1 } } },
  })
  @vector(#{
    name: "optimizer-candidate-config-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "candidate_config", jobId: "opt_1", candidateId: "cand_1", body: null },
    expected: #{ method: "GET", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1/candidates/cand_1/config?api-version=v1", headers: #{ `Foundry-Features`: "AgentsOptimization=V2Preview" }, body: null },
  })
  @vector(#{
    name: "optimizer-status-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "status", jobId: "opt_1", candidateId: null, body: null },
    expected: #{ method: "GET", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1?api-version=v1", headers: #{ `Foundry-Features`: "AgentsOptimization=V2Preview" }, body: null },
  })
  @vector(#{
    name: "optimizer-cancel-rest-request",
    stage: "callable",
    input: #{ projectEndpoint: "https://example.ai.azure.com/api/projects/p", action: "cancel", jobId: "opt_1", candidateId: null, body: null },
    expected: #{ method: "POST", url: "https://example.ai.azure.com/api/projects/p/agent_optimization_jobs/opt_1:cancel?api-version=v1", headers: #{ `Foundry-Features`: "AgentsOptimization=V2Preview" }, body: null },
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
  @vector(#{ name: "optimizer-nonterminal-status", stage: "callable", input: #{ status: "running" }, expected: false })
  terminalOptimizerStatus(status: string | null): boolean;

  @sync
  @vector(#{
    name: "optimizer-best-candidate-explicit-best",
    stage: "callable",
    input: #{ status: #{ result: #{ bestCandidateId: "cand_2" } } },
    expected: "cand_2",
  })
  @vector(#{
    name: "optimizer-best-candidate-by-score",
    stage: "callable",
    input: #{ status: #{ result: #{ candidates: #[#{ candidate_id: "low", avg_score: 0.1 }, #{ candidateId: "high", score: 0.9 }] } } },
    expected: "high",
  })
  @vector(#{
    name: "optimizer-best-candidate-first-tie-wins",
    stage: "callable",
    input: #{ status: #{ result: #{ candidates: #[#{ candidate_id: "first", score: 0.5 }, #{ candidate_id: "second", score: 0.5 }] } } },
    expected: "first",
  })
  @vector(#{
    name: "optimizer-best-candidate-falsy-score-falls-through",
    stage: "callable",
    input: #{ status: #{ result: #{ candidates: #[#{ candidate_id: "chosen", avg_score: 0, score: 0.9 }] } } },
    expected: "chosen",
  })
  bestOptimizerCandidateId(status: unknown): string | null;

  @sync
  @vector(#{
    name: "optimizer-job-id-extraction",
    stage: "callable",
    input: #{ payload: #{ operationId: "opt_1" } },
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

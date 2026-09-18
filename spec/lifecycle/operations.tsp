namespace Castia.Spec.Lifecycle;

@doc("Offline reviewed lifecycle curation and dataset export contract.")
interface LifecycleOperationsRuntime {
  @sync
  @vector(#{
    name: "curate-dataset-deterministic-six-traces",
    stage: "callable",
    input: #{
      traces: #[
        #{ traceId: "trace-0", input: "question-0", reference: "reference-0", reviewer: "human-reviewer", group: "conversation-0", approved: true, referenceOrigin: "human", modelOutput: "generated-0" },
        #{ traceId: "trace-1", input: "question-1", reference: "reference-1", reviewer: "human-reviewer", group: "conversation-1", approved: true, referenceOrigin: "human", modelOutput: "generated-1" },
        #{ traceId: "trace-2", input: "question-2", reference: "reference-2", reviewer: "human-reviewer", group: "conversation-2", approved: true, referenceOrigin: "human", modelOutput: "generated-2" },
        #{ traceId: "trace-3", input: "question-3", reference: "reference-3", reviewer: "human-reviewer", group: "conversation-3", approved: true, referenceOrigin: "human", modelOutput: "generated-3" },
        #{ traceId: "trace-4", input: "question-4", reference: "reference-4", reviewer: "human-reviewer", group: "conversation-4", approved: true, referenceOrigin: "human", modelOutput: "generated-4" },
        #{ traceId: "trace-5", input: "question-5", reference: "reference-5", reviewer: "human-reviewer", group: "conversation-5", approved: true, referenceOrigin: "human", modelOutput: "generated-5" }
      ],
      redactionVersion: "v1",
      heldoutFraction: 0.2,
      seed: "castia"
    },
    expected: #{
      schemaVersion: 1,
      kind: "dataset",
      examples: #[
        #{ input: "question-4", reference: "reference-4", group: "ada4f2d6999ab34adfb25b7924fe007ceebd8575555c8fb9e98e14bd440730a1", provenance: #["trace-4"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
        #{ input: "question-3", reference: "reference-3", group: "207f3f2c40a278c4c0373565e29ee0157614384a3c7285510fbaf3e446d71033", provenance: #["trace-3"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
        #{ input: "question-1", reference: "reference-1", group: "3d4d007868105f5324540be8e475012d789e11a404abea386c89b8e5616cac18", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
        #{ input: "question-5", reference: "reference-5", group: "e6cc1575b9e2288b0b5f0c6abd927b77b2a0a740a0fad986c5cf8e0c95f09b9e", provenance: #["trace-5"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
        #{ input: "question-0", reference: "reference-0", group: "69e88732eac8006038bbfaff8641b2e67221dd5d1e1fc80dcd83a3e71cfd82c7", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
        #{ input: "question-2", reference: "reference-2", group: "f8c77c9e7194fc372600650152a562be9321bce0c51fbfdc9bbd8eeb894a0e2a", provenance: #["trace-2"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }
      ],
      trainIds: #["229f6d6e9faeec2efc159c566693ac0452a360088f86ea457a1c34057cfe62e1", "378e032c99714dc149845afc558df0d658421ed86673d0579da0fc32eae2d39b", "d351e278dadc78bec9ad701fbcee682f73a485f5be9e63999b55661db26cf357", "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"],
      heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"],
      seed: "castia",
      redactionVersion: "v1"
    },
  })
  @vector(#{
    name: "curate-dataset-rejects-model-gold",
    stage: "callable",
    input: #{ traces: #[#{ traceId: "trace-0", input: "question-0", reference: "generated-0", reviewer: "human-reviewer", group: "conversation-0", approved: true, referenceOrigin: "human", modelOutput: "generated-0" }], redactionVersion: "v1", heldoutFraction: 0.2, seed: "castia" },
    expectedError: #{ message: "model output cannot be reused as gold" },
  })
  @vector(#{
    name: "curate-dataset-rejects-unknown-reference-origin",
    stage: "callable",
    input: #{ traces: #[#{ traceId: "trace-0", input: "question-0", reference: "reference-0", reviewer: "human-reviewer", group: "conversation-0", approved: true, referenceOrigin: "model", modelOutput: "generated-0" }], redactionVersion: "v1", heldoutFraction: 0.2, seed: "castia" },
    expectedError: #{ message: "reference_origin must be one of: human, authoritative, deterministic; model-generated and unknown origins are not accepted" },
  })
  @vector(#{
    name: "curate-dataset-rejects-unapproved-trace",
    stage: "callable",
    input: #{ traces: #[#{ traceId: "trace-0", input: "question-0", reference: "reference-0", reviewer: "human-reviewer", group: "conversation-0", approved: false, referenceOrigin: "human", modelOutput: "generated-0" }], redactionVersion: "v1", heldoutFraction: 0.2, seed: "castia" },
    expectedError: #{ message: "approved must be literal true and every trace requires explicit review approval" },
  })
  @vector(#{
    name: "curate-dataset-rejects-zero-heldout-fraction",
    stage: "callable",
    input: #{ traces: #[#{ traceId: "trace-0", input: "question-0", reference: "reference-0", reviewer: "human-reviewer", group: "conversation-0", approved: true, referenceOrigin: "human", modelOutput: "generated-0" }, #{ traceId: "trace-1", input: "question-1", reference: "reference-1", reviewer: "human-reviewer", group: "conversation-1", approved: true, referenceOrigin: "human", modelOutput: "generated-1" }], redactionVersion: "v1", heldoutFraction: 0.0, seed: "castia" },
    expectedError: #{ message: "heldout_fraction must be between zero and one" },
  })
  curateDataset(traces: unknown, redactionVersion: string, heldoutFraction: float64, seed: string): unknown;

  @sync
  @vector(#{
    name: "dataset-jsonl-exports-heldout",
    stage: "callable",
    input: #{
      dataset: #{
        schemaVersion: 1,
        kind: "dataset",
        examples: #[
          #{ input: "question-4", reference: "reference-4", group: "ada4f2d6999ab34adfb25b7924fe007ceebd8575555c8fb9e98e14bd440730a1", provenance: #["trace-4"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
          #{ input: "question-3", reference: "reference-3", group: "207f3f2c40a278c4c0373565e29ee0157614384a3c7285510fbaf3e446d71033", provenance: #["trace-3"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
          #{ input: "question-1", reference: "reference-1", group: "3d4d007868105f5324540be8e475012d789e11a404abea386c89b8e5616cac18", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
          #{ input: "question-5", reference: "reference-5", group: "e6cc1575b9e2288b0b5f0c6abd927b77b2a0a740a0fad986c5cf8e0c95f09b9e", provenance: #["trace-5"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
          #{ input: "question-0", reference: "reference-0", group: "69e88732eac8006038bbfaff8641b2e67221dd5d1e1fc80dcd83a3e71cfd82c7", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] },
          #{ input: "question-2", reference: "reference-2", group: "f8c77c9e7194fc372600650152a562be9321bce0c51fbfdc9bbd8eeb894a0e2a", provenance: #["trace-2"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }
        ],
        trainIds: #["229f6d6e9faeec2efc159c566693ac0452a360088f86ea457a1c34057cfe62e1", "378e032c99714dc149845afc558df0d658421ed86673d0579da0fc32eae2d39b", "d351e278dadc78bec9ad701fbcee682f73a485f5be9e63999b55661db26cf357", "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"],
        heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3"],
        seed: "castia",
        redactionVersion: "v1"
      },
      split: "heldout"
    },
    expected: "{\"example_id\":\"bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20\",\"messages\":[{\"content\":\"question-1\",\"role\":\"user\"}],\"provenance\":[\"trace-1\"],\"reference\":\"reference-1\",\"reference_origins\":[\"human\"]}\n{\"example_id\":\"eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3\",\"messages\":[{\"content\":\"question-2\",\"role\":\"user\"}],\"provenance\":[\"trace-2\"],\"reference\":\"reference-2\",\"reference_origins\":[\"human\"]}\n",
  })
  @vector(#{
    name: "dataset-jsonl-rejects-unknown-split",
    stage: "callable",
    input: #{ dataset: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-0", reference: "reference-0", group: "69e88732eac8006038bbfaff8641b2e67221dd5d1e1fc80dcd83a3e71cfd82c7", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }, #{ input: "question-1", reference: "reference-1", group: "3d4d007868105f5324540be8e475012d789e11a404abea386c89b8e5616cac18", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], seed: "castia", redactionVersion: "v1" }, split: "validation" },
    expectedError: #{ message: "split must be train or heldout" },
  })
  datasetJsonl(dataset: unknown, split: string): string;

  @vector(#{
    name: "evaluate-outcomes-repeated-heldout-run",
    stage: "callable",
    input: #{
      agent: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "instructions.md", sha256: "5ad131c7cb35b34d24e0110e754475cd7d369fc9b9f3dd0c967aeb35e6fbcb4f", size: 11 }], dependencies: #{ castia: "0.6.0" }, `model`: #{ deployment: "gpt-4o" }, instructions: "Be helpful.", tools: #[] },
      dataset: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-1", reference: "reference-1", group: "group-1", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }, #{ input: "question-0", reference: "reference-0", group: "group-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], seed: "castia", redactionVersion: "v1" },
      evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } },
      outcomes: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ quality: 1.0, cost: 0.0, latency_seconds: 0.01 } }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 1, metrics: #{ quality: 1.0, cost: 0.001, latency_seconds: 0.01 } }],
      split: "heldout",
      repeats: 2,
      concurrency: 1,
      timeoutSeconds: 1.0
    },
    expected: #{ schemaVersion: 1, kind: "run", agentId: "ed6976555b4b1cd6947ae5f83a2c2f2402a5eda92fdf5f9d87536d834937378d", datasetId: "1b3464b7c98e0ba2e06fb6ace75ae03a8d35bba035615663b20f603f75c7a152", evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], repeats: 2, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{ cost: 0.0, latency_seconds: 0.01, quality: 1.0 }, error: null }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 1, metrics: #{ cost: 0.001, latency_seconds: 0.01, quality: 1.0 }, error: null }] },
  })
  @vector(#{
    name: "evaluate-outcomes-sanitizes-callback-and-metric-errors",
    stage: "callable",
    input: #{
      agent: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "instructions.md", sha256: "5ad131c7cb35b34d24e0110e754475cd7d369fc9b9f3dd0c967aeb35e6fbcb4f", size: 11 }], dependencies: #{ castia: "0.6.0" }, `model`: #{ deployment: "gpt-4o" }, instructions: "Be helpful.", tools: #[] },
      dataset: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-1", reference: "reference-1", group: "group-1", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }, #{ input: "question-0", reference: "reference-0", group: "group-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], seed: "castia", redactionVersion: "v1" },
      evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } },
      outcomes: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, error: "callback_error", metrics: #{} }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 1, metrics: #{ quality: "0.8" } }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 2, metrics: #{ quality: 0.5, latency_seconds: 0.01 } }],
      split: "heldout",
      repeats: 3,
      concurrency: 1,
      timeoutSeconds: 1.0
    },
    expected: #{ schemaVersion: 1, kind: "run", agentId: "ed6976555b4b1cd6947ae5f83a2c2f2402a5eda92fdf5f9d87536d834937378d", datasetId: "1b3464b7c98e0ba2e06fb6ace75ae03a8d35bba035615663b20f603f75c7a152", evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } }, split: "heldout", expectedIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], repeats: 3, results: #[#{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 0, metrics: #{}, error: "callback_error" }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 1, metrics: #{}, error: "invalid_metrics" }, #{ exampleId: "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20", repetition: 2, metrics: #{ latency_seconds: 0.01, quality: 0.5 }, error: null }] },
  })
  @vector(#{
    name: "evaluate-outcomes-rejects-unknown-split",
    stage: "callable",
    input: #{
      agent: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "instructions.md", sha256: "5ad131c7cb35b34d24e0110e754475cd7d369fc9b9f3dd0c967aeb35e6fbcb4f", size: 11 }], dependencies: #{ castia: "0.6.0" }, `model`: #{ deployment: "gpt-4o" }, instructions: "Be helpful.", tools: #[] },
      dataset: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-1", reference: "reference-1", group: "group-1", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }, #{ input: "question-0", reference: "reference-0", group: "group-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], seed: "castia", redactionVersion: "v1" },
      evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } },
      outcomes: #[],
      split: "validation",
      repeats: 1,
      concurrency: 1,
      timeoutSeconds: 1.0
    },
    expectedError: #{ message: "split must be train or heldout" },
  })
  @vector(#{
    name: "evaluate-outcomes-rejects-zero-timeout",
    stage: "callable",
    input: #{
      agent: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "instructions.md", sha256: "5ad131c7cb35b34d24e0110e754475cd7d369fc9b9f3dd0c967aeb35e6fbcb4f", size: 11 }], dependencies: #{ castia: "0.6.0" }, `model`: #{ deployment: "gpt-4o" }, instructions: "Be helpful.", tools: #[] },
      dataset: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-1", reference: "reference-1", group: "group-1", provenance: #["trace-1"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }, #{ input: "question-0", reference: "reference-0", group: "group-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20"], seed: "castia", redactionVersion: "v1" },
      evaluator: #{ name: "rubric", version: "v2", configuration: #{ judge: "model", temperature: 0 } },
      outcomes: #[],
      split: "heldout",
      repeats: 1,
      concurrency: 1,
      timeoutSeconds: 0.0
    },
    expectedError: #{ message: "timeout must be positive" },
  })
  evaluateOutcomes(agent: unknown, dataset: unknown, evaluator: unknown, outcomes: unknown, split: string, repeats: int32, concurrency: int32, timeoutSeconds: float64): unknown;
}

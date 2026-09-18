namespace Castia.Spec.Lifecycle;

@doc("Portable lifecycle evidence record primitives.")
interface LifecycleRecordsRuntime {
  @sync
  @vector(#{
    name: "canonical-json-sorts-keys-and-compacts",
    stage: "callable",
    input: #{ value: #{ b: #[2, 1], a: "é" } },
    expected: "{\"a\":\"é\",\"b\":[2,1]}",
  })
  canonicalJson(value: unknown): string;

  @sync
  @vector(#{
    name: "content-hash-canonical-object",
    stage: "callable",
    input: #{ value: #{ b: #[2, 1], a: "é" } },
    expected: "265cdd44ca612f13fd2b8e14f6913a5513adf3142e6c82317e55ba51948f43f2",
  })
  @vector(#{
    name: "content-hash-example-input",
    stage: "callable",
    input: #{ value: #{ input: "question-0" } },
    expected: "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1",
  })
  contentHash(value: unknown): string;

  @sync
  @vector(#{
    name: "safe-path-normalizes-backslashes",
    stage: "callable",
    input: #{ path: "src\\main.py" },
    expected: "src/main.py",
  })
  @vector(#{
    name: "safe-path-rejects-traversal",
    stage: "callable",
    input: #{ path: "../escape" },
    expectedError: #{ message: "path must be a normalized relative file path" },
  })
  @vector(#{
    name: "safe-path-rejects-windows-device",
    stage: "callable",
    input: #{ path: "CON.txt" },
    expectedError: #{ message: "path must be a normalized relative file path" },
  })
  @vector(#{
    name: "safe-path-rejects-empty",
    stage: "callable",
    input: #{ path: "" },
    expectedError: #{ message: "relative path must be nonempty text" },
  })
  safePath(path: string): string;

  @sync
  @vector(#{
    name: "check-public-accepts-placeholders",
    stage: "callable",
    input: #{ value: #{ apiKey: "[redacted]", nested: #{ password: "<redacted>" } } },
    expected: true,
  })
  @vector(#{
    name: "check-public-rejects-secret-key",
    stage: "callable",
    input: #{ value: #{ clientSecret: "private" } },
    expectedError: #{ message: "secret-bearing configuration key is not evidence" },
  })
  @vector(#{
    name: "check-public-rejects-camelcase-secret-key",
    stage: "callable",
    input: #{ value: #{ authToken: "private" } },
    expectedError: #{ message: "secret-bearing configuration key is not evidence" },
  })
  @vector(#{
    name: "check-public-rejects-credential-value",
    stage: "callable",
    input: #{ value: "Authorization: Bearer abcdefghijklmnopqrstuvwxyz" },
    expectedError: #{ message: "credential-shaped content is not evidence" },
  })
  @vector(#{
    name: "check-public-rejects-nested-assignment-string",
    stage: "callable",
    input: #{ value: "{\"outer\": {\"password\": \"hunter2\"}}" },
    expectedError: #{ message: "secret-bearing configuration assignment is not evidence" },
  })
  checkPublic(value: unknown): boolean;

  @sync
  @vector(#{
    name: "normalize-agent-record-sorts-source-files",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "b.py", sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", size: 2 }, #{ path: "a.py", sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", size: 1 }], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] } },
    expected: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "a.py", sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", size: 1 }, #{ path: "b.py", sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", size: 2 }], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] },
  })
  @vector(#{
    name: "normalize-record-rejects-unsupported-schema",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 2, kind: "agent" } },
    expectedError: #{ message: "unsupported evidence schema version" },
  })
  @vector(#{
    name: "normalize-record-rejects-duplicate-source-paths",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "agent.py", sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", size: 1 }, #{ path: "AGENT.py", sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", size: 1 }], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "safe", tools: #[] } },
    expectedError: #{ message: "duplicate source paths" },
  })
  @vector(#{
    name: "normalize-run-rejects-duplicate-results",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "run", agentId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", datasetId: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", evaluator: #{ name: "judge", version: "1", configuration: #{} }, split: "heldout", expectedIds: #["cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"], repeats: 1, results: #[#{ exampleId: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", repetition: 0, metrics: #{ score: 1 }, error: null }, #{ exampleId: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", repetition: 0, metrics: #{ score: 1 }, error: null }] } },
    expectedError: #{ message: "unexpected or duplicate evaluation result" },
  })
  @vector(#{
    name: "normalize-decision-rejects-accepted-reasons",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "decision", baselineRunId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", candidateRunId: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", baselineAgentId: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", candidateAgentId: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", accepted: true, reasons: #["regressed"], aggregates: #{ score: 1 }, regressions: #[], gate: #{ minimumScore: 0.8 } } },
    expectedError: #{ message: "accepted decisions have no rejection reasons" },
  })
  @vector(#{
    name: "normalize-dataset-rejects-empty-reference-origins",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "dataset", examples: #[#{ input: "question-0", reference: "reference-0", group: "conversation-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #[] }], trainIds: #["d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"], heldoutIds: #["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], seed: "castia", redactionVersion: "v1" } },
    expectedError: #{ message: "reference_origin must be one of: human, authoritative, deterministic; model-generated and unknown origins are not accepted" },
  })
  @vector(#{
    name: "normalize-candidate-rejects-identity-mismatch",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "candidate", baselineId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", agentId: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", files: #[], agentSnapshot: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] } } },
    expectedError: #{ message: "candidate agent identity does not match its evaluated snapshot" },
  })
  @vector(#{
    name: "normalize-candidate-rejects-unbound-staged-payload",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "candidate", baselineId: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", agentId: "71a13986ae6035b591b21492123923a691834687f4e9e3aa83ec1e9acbebdb02", files: #[#{ path: "agent.txt", content: "hellø" }], agentSnapshot: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "agent.txt", sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824", size: 5 }], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] } } },
    expectedError: #{ message: "staged payload is not bound to the evaluated snapshot; capture files before evaluation" },
  })
  normalizeRecord(value: unknown): unknown;

  @sync
  @vector(#{
    name: "record-id-agent-hash-vector",
    stage: "callable",
    input: #{ value: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[#{ path: "agent.py", sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", size: 0 }], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] } },
    expected: "1adec8c6672ca08db9323fe9512150565047d77d3df71b71c6a2a7932b4346da",
  })
  recordId(value: unknown): string;

  @sync
  @vector(#{
    name: "example-id-hashes-input-only",
    stage: "callable",
    input: #{ value: #{ input: "question-0", reference: "reference-0", group: "conversation-0", provenance: #["trace-0"], reviewers: #["human-reviewer"], referenceOrigins: #["human"] } },
    expected: "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1",
  })
  exampleId(value: unknown): string;
}

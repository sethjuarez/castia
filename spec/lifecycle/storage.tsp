namespace Castia.Spec.Lifecycle;

@doc("File-backed immutable lifecycle artifact store contract.")
interface LifecycleStorageRuntime {
  @sync
  @vector(#{
    name: "put-artifact-writes-normalized-envelope",
    stage: "callable",
    input: #{ root: "$temp/evidence", record: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful." } },
    expected: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59",
  })
  @vector(#{
    name: "put-artifact-is-idempotent",
    stage: "callable",
    files: #[
      #{ path: "evidence/c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59.json", content: "{\"id\":\"c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59\",\"record\":{\"dependencies\":{},\"instructions\":\"Be helpful.\",\"kind\":\"agent\",\"model\":{\"deployment\":\"baseline\"},\"schema_version\":1,\"source_files\":[],\"tools\":[]}}\n" },
    ],
    input: #{ root: "$temp/evidence", record: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful." } },
    expected: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59",
  })
  putArtifact(root: string, record: unknown): string;

  @sync
  @vector(#{
    name: "get-artifact-roundtrips-record",
    stage: "callable",
    files: #[
      #{ path: "evidence/c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59.json", content: "{\"id\":\"c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59\",\"record\":{\"dependencies\":{},\"instructions\":\"Be helpful.\",\"kind\":\"agent\",\"model\":{\"deployment\":\"baseline\"},\"schema_version\":1,\"source_files\":[],\"tools\":[]}}\n" },
    ],
    input: #{ root: "$temp/evidence", id: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59" },
    expected: #{ schemaVersion: 1, kind: "agent", sourceFiles: #[], dependencies: #{}, `model`: #{ deployment: "baseline" }, instructions: "Be helpful.", tools: #[] },
  })
  @vector(#{
    name: "get-artifact-rejects-envelope-mismatch",
    stage: "callable",
    files: #[
      #{ path: "evidence/c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59.json", content: "{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"record\":{\"dependencies\":{},\"instructions\":\"Be helpful.\",\"kind\":\"agent\",\"model\":{\"deployment\":\"baseline\"},\"schema_version\":1,\"source_files\":[],\"tools\":[]}}\n" },
    ],
    input: #{ root: "$temp/evidence", id: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59" },
    expectedError: #{ message: "artifact envelope identity mismatch" },
  })
  @vector(#{
    name: "get-artifact-rejects-missing-json",
    stage: "callable",
    input: #{ root: "$temp/evidence", id: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59" },
    expectedError: #{ message: "cannot read evidence JSON" },
  })
  @vector(#{
    name: "get-artifact-rejects-top-level-array",
    stage: "callable",
    files: #[
      #{ path: "evidence/c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59.json", content: "[]\n" },
    ],
    input: #{ root: "$temp/evidence", id: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59" },
    expectedError: #{ message: "cannot read evidence JSON" },
  })
  @vector(#{
    name: "get-artifact-rejects-non-object-record",
    stage: "callable",
    files: #[
      #{ path: "evidence/c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59.json", content: "{\"id\":\"c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59\",\"record\":[]}\n" },
    ],
    input: #{ root: "$temp/evidence", id: "c495bdfc9ddaea5fae01f5e8363543acb8fe64306767df0330856c14bc3c6d59" },
    expectedError: #{ message: "invalid artifact content" },
  })
  getArtifact(root: string, id: string): unknown;
}

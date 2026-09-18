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
    name: "check-public-rejects-bearer-value",
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
}

namespace Castia.Spec.Lifecycle;

@doc("Versioned `castia.cli.v1` JSON envelope printed by lifecycle commands run with `--json`. `ok` is derived from status and error diagnostics; results and diagnostics are redacted.")
interface LifecycleOutputRuntime {
  @sync
  @vector(#{
    name: "envelope-ok-with-defaults",
    stage: "callable",
    input: #{ command: "eval check", status: "ok", result: #{ evaluators: #["rubric"] }, diagnostics: #[], nextActions: #[], effects: #{} },
    expected: #{
      schema: "castia.cli.v1",
      command: "eval check",
      ok: true,
      status: "ok",
      effects: #{ network: false, billable: false, mutating: false },
      result: #{ evaluators: #["rubric"] },
      diagnostics: #[],
      next: #[],
    },
  })
  @vector(#{
    name: "envelope-error-diagnostic-clears-ok",
    stage: "callable",
    input: #{
      command: "finetune check",
      status: "ok",
      result: null,
      diagnostics: #["RFT requires --validation", #{ severity: "warning", code: "W1", message: "small split" }],
      nextActions: #["python -m castia finetune check --validation val.jsonl"],
      effects: #{ network: false },
    },
    expected: #{
      schema: "castia.cli.v1",
      command: "finetune check",
      ok: false,
      status: "ok",
      effects: #{ network: false, billable: false, mutating: false },
      result: null,
      diagnostics: #[#{ severity: "error", code: null, message: "RFT requires --validation" }, #{ severity: "warning", code: "W1", message: "small split" }],
      next: #["python -m castia finetune check --validation val.jsonl"],
    },
  })
  @vector(#{
    name: "envelope-failed-status-clears-ok-and-redacts",
    stage: "callable",
    input: #{
      command: "optimize status",
      status: "failed",
      result: #{ job: "job-1", api_key: "abc", max_tokens: 10 },
      diagnostics: #[],
      nextActions: #[],
      effects: #{ network: true },
    },
    expected: #{
      schema: "castia.cli.v1",
      command: "optimize status",
      ok: false,
      status: "failed",
      effects: #{ network: true, billable: false, mutating: false },
      result: #{ job: "job-1", api_key: "[REDACTED]", max_tokens: 10 },
      diagnostics: #[],
      next: #[],
    },
  })
  @vector(#{
    name: "envelope-rejects-unknown-effect",
    stage: "callable",
    input: #{ command: "eval run", status: "ok", result: null, diagnostics: #[], nextActions: #[], effects: #{ teleport: true } },
    expectedError: #{ message: "unknown effect keys: teleport" },
  })
  @vector(#{
    name: "envelope-rejects-unknown-severity",
    stage: "callable",
    input: #{ command: "eval run", status: "ok", result: null, diagnostics: #[#{ severity: "fatal", message: "x" }], nextActions: #[], effects: #{} },
    expectedError: #{ message: "diagnostic severity must be one of error, warning, info" },
  })
  commandEnvelope(
    command: string,
    status: string,
    result: unknown,
    diagnostics: unknown,
    nextActions: string[],
    effects: unknown,
  ): unknown;

  @sync
  @vector(#{
    name: "redact-secret-keys-recursively",
    stage: "callable",
    input: #{ value: #{ clientSecret: "s", nested: #[#{ password: "p", `Authorization`: "Bearer abc" }], input_tokens: 5, token: null } },
    expected: #{ clientSecret: "[REDACTED]", nested: #[#{ password: "[REDACTED]", `Authorization`: "[REDACTED]" }], input_tokens: 5, token: null },
  })
  @vector(#{
    name: "redact-secret-patterns-in-strings",
    stage: "callable",
    input: #{ value: "https://a.blob.core.windows.net/c/f?sv=1&sig=abc%3D&se=2 AccountKey=xyz; Bearer eyJ.a-b" },
    expected: "https://a.blob.core.windows.net/c/f?sv=1&sig=[REDACTED]&se=2 AccountKey=[REDACTED]; Bearer [REDACTED]",
  })
  redactSecrets(value: unknown): unknown;
}

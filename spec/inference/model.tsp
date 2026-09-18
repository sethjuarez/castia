namespace Castia.Spec.Inference;

@doc("Pure model-call helper semantics shared by SDKs.")
interface ModelRuntime {
  @sync
  @vector(#{
    name: "reasoning-none",
    stage: "callable",
    input: #{ effort: null },
    expected: #{},
  })
  @vector(#{
    name: "reasoning-normalizes",
    stage: "callable",
    input: #{ effort: " High " },
    expected: #{ reasoning: #{ effort: "high" } },
  })
  @vector(#{
    name: "reasoning-rejects-typo",
    stage: "callable",
    input: #{ effort: "extreme" },
    expectedError: "reasoning_effort must be one of ('minimal', 'low', 'medium', 'high'), got 'extreme'",
  })
  reasoningParam(effort: string | null): unknown;

  @sync
  @vector(#{
    name: "instructions-none",
    stage: "callable",
    input: #{ instructions: null },
    expected: #{},
  })
  @vector(#{
    name: "instructions-present",
    stage: "callable",
    input: #{ instructions: "be terse" },
    expected: #{ instructions: "be terse" },
  })
  instructionsParam(instructions: string | null): unknown;

  @sync
  @vector(#{
    name: "public-tool-spec-strips-private-metadata",
    stage: "callable",
    input: #{
      spec: #{
        type: "mcp",
        server_label: "toolbox",
        server_url: "https://x/mcp",
        xCastiaOptimizerToolDefinitions: #[#{ function: #{ name: "x" } }],
      },
      toolDefinitions: #[],
    },
    expected: #{
      type: "mcp",
      server_label: "toolbox",
      server_url: "https://x/mcp",
    },
  })
  @vector(#{
    name: "public-tool-spec-does-not-rewrite-flat-function",
    stage: "callable",
    input: #{
      spec: #{ type: "function", name: "send_email", description: "Original.", parameters: #{ type: "object" } },
      toolDefinitions: #[#{ type: "function", function: #{ name: "send_email", description: "Rewritten." } }],
    },
    expected: #{ type: "function", name: "send_email", description: "Original.", parameters: #{ type: "object" } },
  })
  publicToolSpec(spec: unknown, toolDefinitions: unknown): unknown;
}

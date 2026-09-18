namespace Castia.Spec.Optimizing;

@doc("Resolved model instructions and tool definitions for an agent turn.")
model AgentConfigResolution {
  source: string;
  `model`?: string;
  instructions?: string;
  toolDefinitions?: Record<unknown>[];
}

@doc("Portable optimizer config resolver seam; source precedence is pinned by conformance fixtures.")
interface AgentConfigResolver {
  @runtimeCancellable
  @vector(#{
    name: "inline-config-wins",
    stage: "callable",
    env: #{
      AZURE_AI_MODEL_DEPLOYMENT_NAME: "gpt-env",
      OPTIMIZATION_CONFIG: "{\"model\":\"gpt-inline\",\"instructions\":\"inline instructions\",\"toolDefinitions\":[{\"function\":{\"name\":\"web\"}}]}",
    },
    files: #[
      #{ path: "baseline/metadata.yaml", content: "model: gpt-local\ninstruction_file: instructions.md\n" },
      #{ path: "baseline/instructions.md", content: "local instructions" },
    ],
    input: #{ configDir: "$temp" },
    expected: #{
      source: "env:OPTIMIZATION_CONFIG",
      `model`: "gpt-inline",
      instructions: "inline instructions",
      toolDefinitions: #[#{ function: #{ name: "web" } }],
    },
  })
  @vector(#{
    name: "local-baseline-with-tools-file-alias",
    stage: "callable",
    env: #{ AZURE_AI_MODEL_DEPLOYMENT_NAME: "gpt-env" },
    files: #[
      #{ path: "baseline/metadata.yaml", content: "model: gpt-local\ninstruction_file: instructions.md\ntool_file: missing.json\ntools_file: tools.json\n" },
      #{ path: "baseline/instructions.md", content: "local instructions" },
      #{ path: "baseline/tools.json", content: "[{\"type\":\"function\",\"function\":{\"name\":\"search\"}}]" },
    ],
    input: #{ configDir: "$temp" },
    expected: #{
      source: "local:baseline",
      `model`: "gpt-local",
      instructions: "local instructions",
      toolDefinitions: #[#{ type: "function", function: #{ name: "search" } }],
    },
  })
  @vector(#{
    name: "local-candidate-wins-over-baseline",
    stage: "callable",
    env: #{ OPTIMIZATION_CANDIDATE_ID: "candidate-1" },
    files: #[
      #{ path: "baseline/metadata.yaml", content: "model: gpt-baseline\ninstruction_file: instructions.md\n" },
      #{ path: "baseline/instructions.md", content: "baseline instructions" },
      #{ path: "candidate-1/metadata.yaml", content: "model: gpt-candidate\ninstruction_file: instructions.md\n" },
      #{ path: "candidate-1/instructions.md", content: "candidate instructions" },
    ],
    input: #{ configDir: "$temp" },
    expected: #{
      source: "local:candidate-1",
      `model`: "gpt-candidate",
      instructions: "candidate instructions",
    },
  })
  @vector(#{
    name: "resolver-api-unsupported-is-loud",
    stage: "callable",
    env: #{
      AZURE_AI_MODEL_DEPLOYMENT_NAME: "gpt-env",
      OPTIMIZATION_CANDIDATE_ID: "candidate-1",
      OPTIMIZATION_RESOLVE_ENDPOINT: "https://example.invalid/resolve",
    },
    files: #[
      #{ path: "baseline/metadata.yaml", content: "model: gpt-local\ninstruction_file: instructions.md\n" },
      #{ path: "baseline/instructions.md", content: "local instructions" },
    ],
    input: #{ configDir: "$temp" },
    expected: #{
      source: "unsupported:resolver_api",
      `model`: "gpt-env",
    },
  })
  @vector(#{
    name: "missing-sources-default",
    stage: "callable",
    env: #{ AZURE_AI_MODEL_DEPLOYMENT_NAME: "gpt-env" },
    input: #{ configDir: "$temp" },
    expected: #{
      source: "default",
      `model`: "gpt-env",
    },
  })
  @doc("Resolve the active agent config from injected candidate, resolver API, local baseline, or defaults.")
  resolve(configDir?: string): AgentConfigResolution;
}

namespace Castia.Spec.Runtime;

@doc("Pure Agent/Router application helpers. Decorator registration and handler invocation stay host-specific.")
interface RuntimeApplicationRuntime {
  @sync
  @vector(#{
    name: "registered-tools-empty",
    stage: "callable",
    input: #{ providerOutputs: #[] },
    expected: #[],
  })
  @vector(#{
    name: "registered-tools-flattens-providers",
    stage: "callable",
    input: #{ providerOutputs: #[#[#{ name: "a" }], #[#{ name: "b" }]] },
    expected: #[#{ name: "a" }, #{ name: "b" }],
  })
  @vector(#{
    name: "registered-tools-dedupes-named-tools-first-wins",
    stage: "callable",
    input: #{ providerOutputs: #[#[#{ name: "dup", description: "first" }], #[#{ name: "dup", description: "second" }]] },
    expected: #[#{ name: "dup", description: "first" }],
  })
  @vector(#{
    name: "registered-tools-keeps-raw-specs-without-name",
    stage: "callable",
    input: #{ providerOutputs: #[#[#{ type: "mcp", serverLabel: "graph" }, #{ name: "search" }], #[#{ type: "mcp", serverLabel: "graph" }]] },
    expected: #[#{ type: "mcp", serverLabel: "graph" }, #{ name: "search" }, #{ type: "mcp", serverLabel: "graph" }],
  })
  @doc("JSON objects with a name model local Tool instances; objects without a name model raw optimizer/toolbox specs that Python passes through without de-duping.")
  registeredTools(providerOutputs: unknown): unknown;

  @sync
  @vector(#{
    name: "include-application-plan-merges-tool-providers",
    stage: "callable",
    input: #{ existingToolProviderCount: 1, incomingToolProviderCount: 2 },
    expected: #{ toolProviderCount: 3 },
  })
  includeApplicationPlan(existingToolProviderCount: int32, incomingToolProviderCount: int32): unknown;

  @sync
  @vector(#{
    name: "responses-only-application-default-name-and-stream",
    stage: "callable",
    input: #{ name: "hal", requestedName: null, hasResponses: true, hasResponsesStream: true, registeredTools: #[#{ name: "a" }] },
    expected: #{ name: "hal-optimize", protocols: #["responses"], wireProtocols: #["responses", "responses_stream"], tools: #[#{ name: "a" }] },
  })
  @vector(#{
    name: "responses-only-application-custom-name",
    stage: "callable",
    input: #{ name: "hal", requestedName: "custom", hasResponses: true, hasResponsesStream: false, registeredTools: #[] },
    expected: #{ name: "custom", protocols: #["responses"], wireProtocols: #["responses"], tools: #[] },
  })
  @vector(#{
    name: "responses-only-application-empty-custom-name-falls-back",
    stage: "callable",
    input: #{ name: "hal", requestedName: "", hasResponses: true, hasResponsesStream: false, registeredTools: #[] },
    expected: #{ name: "hal-optimize", protocols: #["responses"], wireProtocols: #["responses"], tools: #[] },
  })
  @vector(#{
    name: "responses-only-application-unnamed",
    stage: "callable",
    input: #{ name: "", requestedName: null, hasResponses: true, hasResponsesStream: false, registeredTools: #[] },
    expected: #{ name: null, protocols: #["responses"], wireProtocols: #["responses"], tools: #[] },
  })
  @vector(#{
    name: "responses-only-application-requires-responses",
    stage: "callable",
    input: #{ name: "hal", requestedName: null, hasResponses: false, hasResponsesStream: true, registeredTools: #[] },
    expectedError: #{ message: "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting." },
  })
  responsesOnlyApplicationProjection(name: string, requestedName: string | null, hasResponses: boolean, hasResponsesStream: boolean, registeredTools: unknown): unknown;
}

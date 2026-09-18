namespace Castia.Spec.Observe;

@doc("Foundry GenAI span label attributes used by observe tracing helpers.")
interface ObserveTracingRuntime {
  @sync
  @vector(#{
    name: "invoke-agent-defaults-to-env-and-provider",
    stage: "callable",
    input: #{ name: null, envName: "hal-autopilot", envVersion: "3", system: null },
    expected: #{ tracerName: "castia", spanName: "invoke_agent hal-autopilot", attributes: #{ `gen_ai.operation.name`: "invoke_agent", `gen_ai.system`: "microsoft.foundry", `gen_ai.provider.name`: "microsoft.foundry", `gen_ai.agent.name`: "hal-autopilot", `gen_ai.agent.id`: "hal-autopilot:3", `gen_ai.agent.version`: "3" } },
  })
  @vector(#{
    name: "invoke-agent-explicit-name-wins-without-empty-version",
    stage: "callable",
    input: #{ name: "example", envName: "hal-autopilot", envVersion: "", system: "custom.system" },
    expected: #{ tracerName: "castia", spanName: "invoke_agent example", attributes: #{ `gen_ai.operation.name`: "invoke_agent", `gen_ai.system`: "custom.system", `gen_ai.provider.name`: "custom.system", `gen_ai.agent.name`: "example" } },
  })
  @vector(#{
    name: "invoke-agent-empty-env-name-is-not-replaced",
    stage: "callable",
    input: #{ name: null, envName: "", envVersion: null, system: null },
    expected: #{ tracerName: "castia", spanName: "invoke_agent ", attributes: #{ `gen_ai.operation.name`: "invoke_agent", `gen_ai.system`: "microsoft.foundry", `gen_ai.provider.name`: "microsoft.foundry", `gen_ai.agent.name`: "" } },
  })
  invokeAgentSpan(name: string | null, envName: string | null, envVersion: string | null, system: string | null): unknown;

  @sync
  @vector(#{
    name: "execute-tool-span-labels-tool-name",
    stage: "callable",
    input: #{ name: "lookup", system: null },
    expected: #{ tracerName: "castia", spanName: "execute_tool lookup", attributes: #{ `gen_ai.operation.name`: "execute_tool", `gen_ai.system`: "microsoft.foundry", `gen_ai.provider.name`: "microsoft.foundry", `gen_ai.tool.name`: "lookup" } },
  })
  executeToolSpan(name: string, system: string | null): unknown;
}

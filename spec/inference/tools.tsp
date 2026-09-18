namespace Castia.Spec.Inference;

@doc("Model-facing tool catalog and function-tool spec seams.")
interface ToolCatalogRuntime {
  @sync
  @vector(#{
    name: "activity-tool-names",
    stage: "callable",
    input: #{},
    expected: #["react_to_message", "cite_source"],
  })
  activityToolNames(): unknown;

  @sync
  @vector(#{
    name: "graph-tool-names",
    stage: "callable",
    input: #{},
    expected: #["send_email", "reply_email", "create_document", "read_inbox"],
  })
  graphToolNames(): unknown;

  @sync
  @vector(#{
    name: "agent-tool-names",
    stage: "callable",
    input: #{},
    expected: #["react_to_message", "cite_source", "send_email", "reply_email", "create_document", "read_inbox"],
  })
  agentToolNames(): unknown;

  @sync
  @vector(#{
    name: "tool-spec-omits-scopes",
    stage: "callable",
    input: #{ tool: #{ name: "send_email", description: "Send.", parameters: #{ type: "object" }, scopes: #["Mail.Send"] } },
    expected: #{ type: "function", name: "send_email", description: "Send.", parameters: #{ type: "object" } },
  })
  toolSpec(tool: unknown): unknown;
}

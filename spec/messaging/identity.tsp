namespace Castia.Spec.Messaging;

@doc("Agentic-user mailbox identity gates.")
interface IdentityRuntime {
  @sync
  @vector(#{
    name: "agentic-user-id",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "agenticUser", agenticUserId: "user-1" } } },
    expected: "user-1",
  })
  @vector(#{
    name: "app-instance-has-no-mailbox",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "agenticAppInstance", agenticUserId: "user-1" } } },
    expected: null,
  })
  agenticUserId(activity: Castia.Spec.Protocols.Activity): string | null;

  @sync
  @vector(#{
    name: "require-agentic-user-pass",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "agenticUser", agenticUserId: "user-1" } } },
    expected: "user-1",
  })
  @vector(#{
    name: "require-agentic-user-fails",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "bot" } } },
    expectedError: "this action requires the agent's Agentic-User identity (Frontier preview); recipient.role='bot' carries no mailbox",
  })
  requireAgenticUser(activity: Castia.Spec.Protocols.Activity): string;
}

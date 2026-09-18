namespace Castia.Spec.Messaging;

@doc("Activity invoke request/response helpers.")
interface InvokesRuntime {
  @sync
  @vector(#{
    name: "message-response",
    stage: "callable",
    input: #{ text: "done" },
    expected: #{ statusCode: 200, type: "application/vnd.microsoft.activity.message", value: "done" },
  })
  messageInvokeResponse(text: string): unknown;

  @sync
  @vector(#{
    name: "card-response-unwraps-attachment",
    stage: "callable",
    input: #{ card: #{ contentType: "application/vnd.microsoft.card.adaptive", content: #{ type: "AdaptiveCard", body: #[] } } },
    expected: #{ statusCode: 200, type: "application/vnd.microsoft.card.adaptive", value: #{ type: "AdaptiveCard", body: #[] } },
  })
  cardInvokeResponse(card: unknown): unknown;

  @sync
  @vector(#{
    name: "feedback-payload",
    stage: "callable",
    input: #{ activity: #{ value: #{ actionName: "feedback", actionValue: #{ reaction: "like", feedback: "{\"feedbackText\":\"nice\"}" } } } },
    expected: #{ reaction: "like", feedback: "{\"feedbackText\":\"nice\"}" },
  })
  @vector(#{
    name: "feedback-empty",
    stage: "callable",
    input: #{ activity: #{} },
    expected: #{ reaction: null, feedback: null },
  })
  feedbackPayload(activity: Castia.Spec.Protocols.Activity): unknown;

  @sync
  @vector(#{
    name: "card-action",
    stage: "callable",
    input: #{ activity: #{ value: #{ action: #{ type: "Action.Execute", verb: "approve", data: #{ choice: "approve" } }, trigger: "manual" } } },
    expected: #{ verb: "approve", data: #{ choice: "approve" } },
  })
  cardAction(activity: Castia.Spec.Protocols.Activity): unknown;
}

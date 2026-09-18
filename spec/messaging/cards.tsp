namespace Castia.Spec.Messaging;

@doc("Pure Teams card and action builders used by outgoing message payloads.")
interface CardsRuntime {
  @sync
  @vector(#{
    name: "adaptive-card-default-version",
    stage: "callable",
    input: #{ body: #[#{ type: "TextBlock", text: "Deployment complete", weight: "Bolder" }], card: #{} },
    expected: #{
      contentType: "application/vnd.microsoft.card.adaptive",
      content: #{
        type: "AdaptiveCard",
        version: "1.5",
        body: #[#{ type: "TextBlock", text: "Deployment complete", weight: "Bolder" }],
      },
    },
  })
  adaptiveCard(body: unknown, card: unknown): unknown;

  @sync
  @vector(#{
    name: "suggested-actions-string-and-dict",
    stage: "callable",
    input: #{ actions: #["Yes", #{ title: "Nope", value: "No", type: "postBack" }] },
    expected: #{
      actions: #[
        #{ type: "imBack", title: "Yes", value: "Yes" },
        #{ type: "postBack", title: "Nope", value: "No" },
      ],
      to: #[],
    },
  })
  suggestedActions(actions: unknown): unknown;

  @sync
  @vector(#{
    name: "action-chips",
    stage: "callable",
    input: #{ actions: #["Thanks", #{ title: "More", value: "Tell me more" }], prompt: "Anything else?" },
    expected: #{
      contentType: "application/vnd.microsoft.card.adaptive",
      content: #{
        type: "AdaptiveCard",
        version: "1.5",
        body: #[#{ type: "TextBlock", text: "Anything else?", wrap: true }],
        actions: #[
          #{ type: "Action.Submit", title: "Thanks", data: #{ msteams: #{ type: "imBack", value: "Thanks" }, choice: "Thanks" } },
          #{ type: "Action.Submit", title: "More", data: #{ msteams: #{ type: "imBack", value: "Tell me more" }, choice: "Tell me more" } },
        ],
      },
    },
  })
  actionChips(actions: unknown, prompt: string): unknown;

  @sync
  @vector(#{
    name: "decision-card",
    stage: "callable",
    input: #{ actions: #[#{ title: "Approve", verb: "approve", data: #{ id: "42" } }, "cancel"], prompt: "Deploy?", card: #{} },
    expected: #{
      contentType: "application/vnd.microsoft.card.adaptive",
      content: #{
        type: "AdaptiveCard",
        version: "1.5",
        body: #[#{ type: "TextBlock", text: "Deploy?", wrap: true, weight: "Bolder" }],
        actions: #[
          #{ type: "Action.Execute", title: "Approve", verb: "approve", data: #{ id: "42" } },
          #{ type: "Action.Execute", title: "cancel", verb: "cancel", data: #{} },
        ],
      },
    },
  })
  decisionCard(actions: unknown, prompt: string, card: unknown): unknown;
}

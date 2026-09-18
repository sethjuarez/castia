namespace Castia.Spec.Messaging;

@doc("Pure Teams message decoration builders.")
interface EntitiesRuntime {
  @sync
  @vector(#{
    name: "citation-caps-keywords",
    stage: "callable",
    input: #{ position: 2, name: "Quarterly Report", url: "https://example/report", abstractText: "Q3 numbers", keywords: #["a", "b", "c", "d"], icon: "PDF" },
    expected: #{
      position: 2,
      appearance: #{
        name: "Quarterly Report",
        url: "https://example/report",
        abstract: "Q3 numbers",
        keywords: #["a", "b", "c"],
        image: #{ name: "PDF" },
      },
    },
  })
  citation(position: int32, name: string, url: string, abstractText: string, keywords: unknown, icon: string): unknown;

  @sync
  @vector(#{
    name: "sensitivity-with-description",
    stage: "callable",
    input: #{ name: "Confidential", description: "Internal only" },
    expected: #{ name: "Confidential", description: "Internal only" },
  })
  sensitivityLabel(name: string, description: string): unknown;

  @sync
  @vector(#{
    name: "message-entity-combines-facets",
    stage: "callable",
    input: #{
      aiGenerated: true,
      citations: #[#{ position: 1, appearance: #{ name: "Doc" } }],
      sensitivity: #{ name: "Confidential" },
    },
    expected: #{
      type: "https://schema.org/Message",
      additionalType: #["AIGeneratedContent"],
      citation: #[#{ position: 1, appearance: #{ name: "Doc" } }],
      usageInfo: #{ name: "Confidential" },
    },
  })
  @vector(#{
    name: "message-entity-none",
    stage: "callable",
    input: #{ aiGenerated: false, citations: null, sensitivity: null },
    expected: null,
  })
  messageEntity(aiGenerated: boolean, citations: unknown, sensitivity: unknown): unknown;

  @sync
  @vector(#{
    name: "feedback-default",
    stage: "callable",
    input: #{ kind: "default" },
    expected: #{ feedbackLoop: #{ type: "default" } },
  })
  feedbackChannelData(kind: string): unknown;

  @sync
  @vector(#{
    name: "mention-entity",
    stage: "callable",
    input: #{ accountId: "8:orgid:aad-123", name: "Ada" },
    expected: #{ type: "mention", mentioned: #{ id: "8:orgid:aad-123", name: "Ada" }, text: "<at>Ada</at>" },
  })
  mentionEntity(accountId: string, name: string): unknown;
}

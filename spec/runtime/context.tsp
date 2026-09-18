namespace Castia.Spec.Runtime;

@doc("Pure turn decoration helpers. Ambient contextvars and connector IO remain host-specific.")
interface RuntimeContextRuntime {
  @sync
  @vector(#{
    name: "turn-cite-auto-increments-position",
    stage: "callable",
    input: #{ citations: #[#{ position: 1, appearance: #{ name: "First" } }], name: "Second", url: "https://x", abstractText: "", keywords: null, icon: "" },
    expected: #{ position: 2, citations: #[#{ position: 1, appearance: #{ name: "First" } }, #{ `@type`: "Claim", position: 2, appearance: #{ `@type`: "DigitalDocument", name: "Second", url: "https://x" } }] },
  })
  turnCite(citations: unknown, name: string, url: string, abstractText: string, keywords: unknown, icon: string): unknown;

  @sync
  @vector(#{
    name: "decorate-message-merges-turn-and-overrides",
    stage: "callable",
    input: #{
      payload: #{ type: "message", text: "hi" },
      turn: #{ aiGenerated: true, citations: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "From tool" } }], sensitivity: null, feedback: null, importance: null },
      aiGenerated: false,
      citations: null,
      sensitivity: null,
      feedback: "default",
      importance: "high"
    },
    expected: #{ type: "message", text: "hi", entities: #[#{ type: "https://schema.org/Message", `@type`: "Message", `@context`: "https://schema.org", `@id`: "", additionalType: #["AIGeneratedContent"], citation: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "From tool" } }] }], channelData: #{ feedbackLoop: #{ type: "default" } }, importance: "high" },
  })
  @vector(#{
    name: "decorate-message-no-turn-uses-overrides-only",
    stage: "callable",
    input: #{ payload: #{ type: "message" }, turn: null, aiGenerated: true, citations: null, sensitivity: null, feedback: null, importance: null },
    expected: #{ type: "message", entities: #[#{ type: "https://schema.org/Message", `@type`: "Message", `@context`: "https://schema.org", `@id`: "", additionalType: #["AIGeneratedContent"] }] },
  })
  @vector(#{
    name: "decorate-message-appends-root-and-preserves-existing-channel-data",
    stage: "callable",
    input: #{
      payload: #{ type: "message", entities: #[#{ type: "mention" }], channelData: #{ existing: true } },
      turn: #{ aiGenerated: false, citations: #[], sensitivity: #{ `@type`: "CreativeWork", name: "Confidential" }, feedback: "custom", importance: "urgent" },
      aiGenerated: false,
      citations: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "Override citation" } }],
      sensitivity: null,
      feedback: null,
      importance: null
    },
    expected: #{ type: "message", entities: #[#{ type: "mention" }, #{ type: "https://schema.org/Message", `@type`: "Message", `@context`: "https://schema.org", `@id`: "", citation: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "Override citation" } }], usageInfo: #{ `@type`: "CreativeWork", name: "Confidential" } }], channelData: #{ existing: true, feedbackLoop: #{ type: "custom" } }, importance: "urgent" },
  })
  @vector(#{
    name: "decorate-message-overrides-win-over-turn-scalars",
    stage: "callable",
    input: #{
      payload: #{ type: "message" },
      turn: #{ aiGenerated: false, citations: #[], sensitivity: #{ `@type`: "CreativeWork", name: "Internal" }, feedback: "custom", importance: "normal" },
      aiGenerated: false,
      citations: null,
      sensitivity: #{ `@type`: "CreativeWork", name: "Confidential" },
      feedback: "default",
      importance: "urgent"
    },
    expected: #{ type: "message", entities: #[#{ type: "https://schema.org/Message", `@type`: "Message", `@context`: "https://schema.org", `@id`: "", usageInfo: #{ `@type`: "CreativeWork", name: "Confidential" } }], channelData: #{ feedbackLoop: #{ type: "default" } }, importance: "urgent" },
  })
  @vector(#{
    name: "decorate-message-keeps-turn-citations-before-overrides",
    stage: "callable",
    input: #{
      payload: #{ type: "message" },
      turn: #{ aiGenerated: false, citations: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "Turn citation" } }], sensitivity: null, feedback: null, importance: null },
      aiGenerated: false,
      citations: #[#{ `@type`: "Claim", position: 2, appearance: #{ `@type`: "DigitalDocument", name: "Override citation" } }],
      sensitivity: null,
      feedback: null,
      importance: null
    },
    expected: #{ type: "message", entities: #[#{ type: "https://schema.org/Message", `@type`: "Message", `@context`: "https://schema.org", `@id`: "", citation: #[#{ `@type`: "Claim", position: 1, appearance: #{ `@type`: "DigitalDocument", name: "Turn citation" } }, #{ `@type`: "Claim", position: 2, appearance: #{ `@type`: "DigitalDocument", name: "Override citation" } }] }] },
  })
  @vector(#{
    name: "decorate-message-empty-feedback-override-suppresses-turn-feedback",
    stage: "callable",
    input: #{ payload: #{ type: "message" }, turn: #{ aiGenerated: false, citations: #[], sensitivity: null, feedback: "custom", importance: "normal" }, aiGenerated: false, citations: null, sensitivity: null, feedback: "", importance: "" },
    expected: #{ type: "message" },
  })
  @vector(#{
    name: "decorate-message-no-decorations-is-no-op",
    stage: "callable",
    input: #{ payload: #{ type: "message", text: "hi" }, turn: null, aiGenerated: false, citations: null, sensitivity: null, feedback: null, importance: null },
    expected: #{ type: "message", text: "hi" },
  })
  decorateMessage(payload: unknown, turn: unknown, aiGenerated: boolean, citations: unknown, sensitivity: unknown, feedback: string | null, importance: string | null): unknown;
}

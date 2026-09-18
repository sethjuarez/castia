namespace Castia.Spec.Messaging;

@doc("Pure Teams streaming payload builders.")
interface StreamingRuntime {
  @sync
  @vector(#{
    name: "informative-chunk-has-sequence-and-no-stream-id",
    stage: "callable",
    input: #{ streamType: "informative", text: "Thinking...", streamId: null, sequence: 0, isFinal: false, attachments: null, suggestions: null },
    expected: #{ type: "typing", channelData: #{ streamType: "informative", streamSequence: 1 }, entities: #[#{ type: "streamInfo", streamType: "informative", streamSequence: 1 }], text: "Thinking..." },
  })
  @vector(#{
    name: "streaming-chunk-reuses-stream-id",
    stage: "callable",
    input: #{ streamType: "streaming", text: "Hello", streamId: "created-id", sequence: 1, isFinal: false, attachments: null, suggestions: null },
    expected: #{ type: "typing", channelData: #{ streamType: "streaming", streamId: "created-id", streamSequence: 2 }, entities: #[#{ type: "streamInfo", streamType: "streaming", streamId: "created-id", streamSequence: 2 }], text: "Hello" },
  })
  @vector(#{
    name: "final-chunk-omits-sequence-and-adds-suggestions",
    stage: "callable",
    input: #{ streamType: "final", text: "Hello world", streamId: "created-id", sequence: 3, isFinal: true, attachments: null, suggestions: #{ actions: #[#{ type: "imBack", title: "Thanks!", value: "Thanks!" }], to: #[] } },
    expected: #{ type: "message", channelData: #{ streamType: "final", streamId: "created-id" }, entities: #[#{ type: "streamInfo", streamType: "final", streamId: "created-id" }], text: "Hello world", suggestedActions: #{ actions: #[#{ type: "imBack", title: "Thanks!", value: "Thanks!" }], to: #[] } },
  })
  streamChunk(streamType: string, text: string, streamId: string | null, sequence: int32, isFinal: boolean, attachments: unknown, suggestions: unknown): unknown;

  @sync
  @vector(#{
    name: "capture-first-created-stream-id",
    stage: "callable",
    input: #{ currentStreamId: null, createdId: "created-id" },
    expected: "created-id",
  })
  @vector(#{
    name: "keep-existing-stream-id",
    stage: "callable",
    input: #{ currentStreamId: "first-id", createdId: "second-id" },
    expected: "first-id",
  })
  captureStreamId(currentStreamId: string | null, createdId: string | null): string | null;

  @sync
  @vector(#{
    name: "update-allowed-before-text",
    stage: "callable",
    input: #{ finished: false, currentText: "" },
    expected: true,
  })
  @vector(#{
    name: "update-ignored-after-text",
    stage: "callable",
    input: #{ finished: false, currentText: "body" },
    expected: false,
  })
  updateAllowed(finished: boolean, currentText: string): boolean;

  @sync
  @vector(#{
    name: "finish-allowed-before-finished",
    stage: "callable",
    input: #{ finished: false },
    expected: true,
  })
  @vector(#{
    name: "finish-ignored-after-finished",
    stage: "callable",
    input: #{ finished: true },
    expected: false,
  })
  finishAllowed(finished: boolean): boolean;

  @sync
  @vector(#{
    name: "next-sequence-advances-interim",
    stage: "callable",
    input: #{ sequence: 2, isFinal: false },
    expected: 3,
  })
  @vector(#{
    name: "next-sequence-does-not-advance-final",
    stage: "callable",
    input: #{ sequence: 3, isFinal: true },
    expected: 3,
  })
  nextSequence(sequence: int32, isFinal: boolean): int32;

  @sync
  @vector(#{
    name: "next-flush-updates-after-emitted-chunk",
    stage: "callable",
    input: #{ lastFlush: 1, now: 2, emitted: true },
    expected: 2,
  })
  @vector(#{
    name: "next-flush-keeps-last-time-when-not-emitted",
    stage: "callable",
    input: #{ lastFlush: 1, now: 2, emitted: false },
    expected: 1,
  })
  nextFlush(lastFlush: float64, now: float64, emitted: boolean): unknown;

  @sync
  @vector(#{
    name: "append-plan-flushes-after-min-interval",
    stage: "callable",
    input: #{ finished: false, currentText: "Hello", delta: " world", lastFlush: 1, now: 2, minInterval: 0.75 },
    expected: #{ text: "Hello world", flush: true, nextFlush: 2 },
  })
  @vector(#{
    name: "append-plan-ignores-empty-delta",
    stage: "callable",
    input: #{ finished: false, currentText: "Hello", delta: "", lastFlush: 1, now: 2, minInterval: 0.75 },
    expected: #{ text: "Hello", flush: false, nextFlush: 1 },
  })
  appendPlan(finished: boolean, currentText: string, delta: string, lastFlush: float64, now: float64, minInterval: float64): unknown;

  @sync
  @vector(#{
    name: "finish-text-defaults-to-accumulated",
    stage: "callable",
    input: #{ currentText: "Hello", overrideText: null },
    expected: "Hello",
  })
  @vector(#{
    name: "finish-text-uses-explicit-override",
    stage: "callable",
    input: #{ currentText: "Hello", overrideText: "" },
    expected: "",
  })
  finishText(currentText: string, overrideText: string | null): string;
}

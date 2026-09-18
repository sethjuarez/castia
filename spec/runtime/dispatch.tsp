namespace Castia.Spec.Runtime;

@doc("Pure dispatch planning and result shaping. Calling user code, telemetry, and async dependency execution stay host-specific.")
interface RuntimeDispatchRuntime {
  @sync
  @vector(#{
    name: "activity-dispatch-text-handler-skips-empty-input",
    stage: "callable",
    input: #{ parameters: #[#{ name: "text" }], text: "" },
    expected: #{ skip: true, injections: #[] },
  })
  @vector(#{
    name: "activity-dispatch-injects-text-message-activity-and-dependency",
    stage: "callable",
    input: #{ parameters: #[#{ name: "text" }, #{ name: "msg", annotation: "Message" }, #{ name: "activity", annotation: "Activity" }, #{ name: "model", annotation: "Activity", defaultKind: "Depends" }], text: "hello" },
    expected: #{ skip: false, injections: #[#{ name: "text", source: "text", value: "hello" }, #{ name: "msg", source: "message" }, #{ name: "activity", source: "activity" }, #{ name: "model", source: "dependency" }] },
  })
  @vector(#{
    name: "activity-dispatch-no-text-parameter-does-not-skip-empty-input",
    stage: "callable",
    input: #{ parameters: #[#{ name: "activity", annotation: "Activity" }], text: "" },
    expected: #{ skip: false, injections: #[#{ name: "activity", source: "activity" }] },
  })
  activityDispatchPlan(parameters: unknown, text: string): unknown;

  @sync
  @vector(#{
    name: "invoke-dispatch-bare-parameter-receives-value",
    stage: "callable",
    input: #{ parameters: #[#{ name: "value" }, #{ name: "msg", annotation: "Message" }], value: #{ action: "approve" } },
    expected: #{ injections: #[#{ name: "value", source: "invoke_value", value: #{ action: "approve" } }, #{ name: "msg", source: "message" }] },
  })
  invokeDispatchPlan(parameters: unknown, value: unknown): unknown;

  @sync
  @vector(#{
    name: "wire-dispatch-rejects-activity-annotation",
    stage: "callable",
    input: #{ handlerName: "reply", parameters: #[#{ name: "activity", annotation: "Activity" }], text: "hello", streaming: false },
    expectedError: #{ message: "handler 'reply' asks for Activity, which only exists on the Activity Protocol; a handler served over @app.responses(), @app.chat(), or @app.invocations() must take the input text and Depends(...) only." },
  })
  @vector(#{
    name: "wire-stream-dispatch-rejects-message-annotation",
    stage: "callable",
    input: #{ handlerName: "reply_stream", parameters: #[#{ name: "msg", annotation: "Message" }], text: "hello", streaming: true },
    expectedError: #{ message: "handler 'reply_stream' asks for Message, which only exists on the Activity Protocol; a streaming wire handler must take the input text and Depends(...) only." },
  })
  @vector(#{
    name: "wire-stream-dispatch-rejects-activity-annotation-with-streaming-message",
    stage: "callable",
    input: #{ handlerName: "reply_stream", parameters: #[#{ name: "activity", annotation: "Activity" }], text: "hello", streaming: true },
    expectedError: #{ message: "handler 'reply_stream' asks for Activity, which only exists on the Activity Protocol; a streaming wire handler must take the input text and Depends(...) only." },
  })
  @vector(#{
    name: "wire-dispatch-injects-text-and-dependency",
    stage: "callable",
    input: #{ handlerName: "reply", parameters: #[#{ name: "text" }, #{ name: "model", defaultKind: "Depends" }], text: "hello", streaming: false },
    expected: #{ injections: #[#{ name: "text", source: "text", value: "hello" }, #{ name: "model", source: "dependency" }] },
  })
  wireDispatchPlan(handlerName: string, parameters: unknown, text: string, streaming: boolean): unknown;

  @sync
  @vector(#{
    name: "activity-result-empty-or-non-string-is-none",
    stage: "callable",
    input: #{ result: "" },
    expected: null,
  })
  @vector(#{
    name: "activity-result-nonempty-string",
    stage: "callable",
    input: #{ result: "ok" },
    expected: "ok",
  })
  @vector(#{
    name: "activity-result-non-string-is-none",
    stage: "callable",
    input: #{ result: 42 },
    expected: null,
  })
  activityResultText(result: unknown): string | null;

  @sync
  @vector(#{
    name: "invoke-result-object-or-none",
    stage: "callable",
    input: #{ result: #{ ok: true } },
    expected: #{ ok: true },
  })
  @vector(#{
    name: "invoke-result-non-object-is-none",
    stage: "callable",
    input: #{ result: "ignored" },
    expected: null,
  })
  @vector(#{
    name: "invoke-result-array-is-none",
    stage: "callable",
    input: #{ result: #[] },
    expected: null,
  })
  invokeResultBody(result: unknown): unknown;

  @sync
  @vector(#{
    name: "wire-return-body-string-or-empty",
    stage: "callable",
    input: #{ result: 42 },
    expected: "",
  })
  @vector(#{
    name: "wire-return-body-string-passthrough",
    stage: "callable",
    input: #{ result: "body" },
    expected: "body",
  })
  returnBody(result: unknown): string;

  @sync
  @vector(#{
    name: "stream-chunks-filter-empty-and-non-string",
    stage: "callable",
    input: #{ result: #["a", "", 1, "b"] },
    expected: #["a", "b"],
  })
  @vector(#{
    name: "stream-chunks-single-string",
    stage: "callable",
    input: #{ result: "done" },
    expected: #["done"],
  })
  @vector(#{
    name: "stream-chunks-empty-string-is-empty",
    stage: "callable",
    input: #{ result: "" },
    expected: #[],
  })
  @vector(#{
    name: "stream-chunks-non-stream-result-is-empty",
    stage: "callable",
    input: #{ result: 42 },
    expected: #[],
  })
  streamChunks(result: unknown): string[];
}

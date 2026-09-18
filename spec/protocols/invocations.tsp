namespace Castia.Spec.Protocols;

@doc("Portable helper seam for the Foundry Invocations protocol wire shape.")
interface InvocationsRuntime {
  @sync
  @vector(#{
    name: "message-field",
    stage: "callable",
    input: #{ body: #{ message: "hello" } },
    expected: "hello",
  })
  @vector(#{
    name: "input-field",
    stage: "callable",
    input: #{ body: #{ input: "hello" } },
    expected: "hello",
  })
  @vector(#{
    name: "message-wins-over-input",
    stage: "callable",
    input: #{ body: #{ message: "message", input: "input" } },
    expected: "message",
  })
  @vector(#{
    name: "bare-string",
    stage: "callable",
    input: #{ body: "hello" },
    expected: "hello",
  })
  @vector(#{
    name: "null-is-empty",
    stage: "callable",
    input: #{ body: null },
    expected: "",
  })
  @vector(#{
    name: "non-string-fields-are-empty",
    stage: "callable",
    input: #{ body: #{ message: 42, input: true } },
    expected: "",
  })
  @doc("Normalize an Invocations request payload to the text sent to a handler.")
  inputText(body: unknown): string;

  @sync
  @vector(#{
    name: "invocations-body",
    stage: "callable",
    input: #{ text: "hello" },
    expected: #{ output: "hello" },
  })
  @doc("Wrap handler text in Castia's minimal Invocations output envelope.")
  body(text: string): unknown;
}

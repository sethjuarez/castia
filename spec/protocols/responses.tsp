namespace Castia.Spec.Protocols;

@doc("Portable helper seam for the OpenAI Responses protocol wire shape.")
interface ResponsesRuntime {
  @sync
  @vector(#{
    name: "plain-string",
    stage: "callable",
    input: #{ value: "hello world" },
    expected: "hello world",
  })
  @vector(#{
    name: "empty-string",
    stage: "callable",
    input: #{ value: "" },
    expected: "",
  })
  @vector(#{
    name: "null-is-empty",
    stage: "callable",
    input: #{ value: null },
    expected: "",
  })
  @vector(#{
    name: "number-is-empty",
    stage: "callable",
    input: #{ value: 42 },
    expected: "",
  })
  @vector(#{
    name: "non-list-non-string-is-empty",
    stage: "callable",
    input: #{ value: #{ role: "user" } },
    expected: "",
  })
  @vector(#{
    name: "list-role-string-content",
    stage: "callable",
    input: #{ value: #[#{ role: "user", content: "say hi" }] },
    expected: "say hi",
  })
  @vector(#{
    name: "list-content-parts",
    stage: "callable",
    input: #{
      value: #[
        #{
          role: "user",
          content: #[
            #{ type: "input_text", text: "part one " },
            #{ type: "input_text", text: "part two" },
          ],
        },
      ],
    },
    expected: "part one part two",
  })
  @vector(#{
    name: "output-text-and-text-part-types",
    stage: "callable",
    input: #{
      value: #[
        #{
          role: "user",
          content: #[
            #{ type: "output_text", text: "a" },
            #{ type: "text", text: "b" },
          ],
        },
      ],
    },
    expected: "ab",
  })
  @vector(#{
    name: "prefers-last-user-turn",
    stage: "callable",
    input: #{
      value: #[
        #{ role: "user", content: "first" },
        #{ role: "assistant", content: "reply" },
        #{ role: "user", content: "second" },
      ],
    },
    expected: "second",
  })
  @vector(#{
    name: "roleless-item-falls-back",
    stage: "callable",
    input: #{ value: #[#{ content: "no role here" }] },
    expected: "no role here",
  })
  @vector(#{
    name: "user-turn-wins-over-later-roleless-without-text",
    stage: "callable",
    input: #{
      value: #[
        #{ role: "user", content: "the question" },
        #{ role: "assistant", content: "" },
      ],
    },
    expected: "the question",
  })
  @vector(#{
    name: "bare-string-items-fall-back-to-last",
    stage: "callable",
    input: #{ value: #["first", "second"] },
    expected: "second",
  })
  @vector(#{
    name: "empty-list-is-empty",
    stage: "callable",
    input: #{ value: #[] },
    expected: "",
  })
  @doc("Normalize the Responses API polymorphic input field to the text sent to a handler.")
  inputText(value: unknown): string;

  @sync
  @vector(#{
    name: "output-body",
    stage: "callable",
    input: #{ text: "hello" },
    expected: #{
      object: "response",
      status: "completed",
      output_text: "hello",
      output: #[
        #{
          type: "message",
          role: "assistant",
          content: #[#{ type: "output_text", text: "hello" }],
        },
      ],
    },
  })
  @doc("Wrap handler text in the minimal OpenAI Responses envelope; vector comparison normalizes the generated id.")
  outputBody(text: string): unknown;
}

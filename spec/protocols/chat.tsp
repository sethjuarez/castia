namespace Castia.Spec.Protocols;

@doc("Portable helper seam for the OpenAI Chat Completions protocol wire shape.")
interface ChatRuntime {
  @sync
  @vector(#{
    name: "last-user-turn",
    stage: "callable",
    input: #{
      messages: #[
        #{ role: "user", content: "first" },
        #{ role: "assistant", content: "reply" },
        #{ role: "user", content: "second" },
      ],
    },
    expected: "second",
  })
  @vector(#{
    name: "missing-user-turn",
    stage: "callable",
    input: #{ messages: #[#{ role: "assistant", content: "reply" }] },
    expected: "",
  })
  @vector(#{
    name: "null-messages",
    stage: "callable",
    input: #{ messages: null },
    expected: "",
  })
  @vector(#{
    name: "missing-content-is-empty",
    stage: "callable",
    input: #{ messages: #[#{ role: "user" }] },
    expected: "",
  })
  @doc("Return the content of the last user message in a Chat Completions messages array.")
  lastUserText(messages: unknown): string;

  @sync
  @vector(#{
    name: "chat-body",
    stage: "callable",
    input: #{ text: "hello" },
    expected: #{
      object: "chat.completion",
      choices: #[
        #{
          index: 0,
          message: #{ role: "assistant", content: "hello" },
          finish_reason: "stop",
        },
      ],
    },
  })
  @doc("Wrap handler text in a minimal OpenAI Chat Completions envelope; vector comparison normalizes the generated id.")
  body(text: string): unknown;
}

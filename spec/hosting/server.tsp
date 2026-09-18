namespace Castia.Spec.Hosting;

@doc("Pure hosting server helpers. FastAPI route registration and network serving remain host-specific.")
interface HostingServerRuntime {
  @sync
  @vector(#{
    name: "readiness-body",
    stage: "callable",
    input: #{},
    expected: "Agent running!",
  })
  readinessBody(): string;

  @sync
  @vector(#{
    name: "sse-event-minifies-json",
    stage: "callable",
    input: #{ eventType: "response.output_text.delta", payload: #{ type: "response.output_text.delta", delta: "hi" } },
    expected: "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"hi\"}\n\n",
  })
  @vector(#{
    name: "sse-event-completed-preserves-python-response-key-order",
    stage: "callable",
    input: #{ eventType: "response.completed", payload: #{ id: "resp_1", object: "response", status: "completed", output_text: "ok", output: #[#{ type: "message", role: "assistant", content: #[#{ type: "output_text", text: "ok" }] }] } },
    expected: "event: response.completed\ndata: {\"id\":\"resp_1\",\"object\":\"response\",\"status\":\"completed\",\"output_text\":\"ok\",\"output\":[{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"ok\"}]}]}\n\n",
  })
  @vector(#{
    name: "sse-event-escapes-non-ascii-like-python-json-dumps",
    stage: "callable",
    input: #{ eventType: "response.output_text.delta", payload: #{ type: "response.output_text.delta", delta: "café 🚀" } },
    expected: "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"caf\\u00e9 \\ud83d\\ude80\"}\n\n",
  })
  sseEvent(eventType: string, payload: unknown): string;

  @sync
  @vector(#{
    name: "wire-endpoints-plan-in-python-registration-order",
    stage: "callable",
    input: #{ wireProtocols: #["invocations", "responses", "chat", "responses_stream", "unknown"] },
    expected: #[#{ protocol: "responses", method: "POST", path: "/responses", streaming: true }, #{ protocol: "chat", method: "POST", path: "/chat/completions" }, #{ protocol: "invocations", method: "POST", path: "/invocations" }],
  })
  @vector(#{
    name: "wire-endpoints-empty",
    stage: "callable",
    input: #{ wireProtocols: #[] },
    expected: #[],
  })
  @vector(#{
    name: "wire-endpoints-responses-stream-alone-registers-nothing",
    stage: "callable",
    input: #{ wireProtocols: #["responses_stream"] },
    expected: #[],
  })
  wireEndpoints(wireProtocols: string[]): unknown;
}

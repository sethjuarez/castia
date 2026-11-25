# Conformance fixture: GenAI semantic conventions

`genai_semconv.json` pins the OpenTelemetry GenAI shapes every castia SDK
emits, so Monitor, trace-based evals and the eval harness read the same
attributes no matter which SDK produced them.

- **Version pin.** castia follows GenAI semantic conventions **v1.42.0** (the
  last versioned cut before the conventions moved to
  `open-telemetry/semantic-conventions-genai`) and reports
  `https://opentelemetry.io/schemas/1.42.0` as the tracer and logger schema URL.
- **Content.** `gen_ai.input.messages`, `gen_ai.output.messages` and
  `gen_ai.system_instructions` use the parts schema (`{"role", "parts":[{"type":"text","content"}]}`)
  and are emitted only when `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`.
- **Request parameters.** Zero is a real setting: `temperature=0` and `seed=0`
  are recorded; only unset (`null`) values are dropped. Booleans are not numbers.
- **Server tools.** Provider-executed tool items in a Responses `output`
  (`mcp_call`, `file_search_call`, ...) become synthesized `execute_tool {name}`
  spans with `castia.tool.synthesized=true`. Failures (an `error`, or status
  `failed`/`incomplete`) get error status and `error.type`; HTTP status codes in
  string errors normalize to `http_<code>` (the KB `402 PaymentRequired` case).
  Tool arguments and results are content and follow the content opt-in.
- **Tool-error gate.** An eval run is invalid when its tool error rate is
  strictly above `max_error_rate`, or it saw fewer than `min_calls` tool calls.
  Inputs may mix `castia.tool_capture/v1` documents, `execute_tool` span dicts
  and normalized server tool calls. `tool_reported_ok=false` counts as an error.
- **Human evaluations.** `gen_ai.evaluation.result` events carry
  `gen_ai.evaluation.name`, a float `gen_ai.evaluation.score.value` and/or
  `gen_ai.evaluation.score.label`, and `castia.evaluation.source=human`, parented
  to the reviewed span through a W3C `traceparent`.

Read by `packages/python/tests/test_observe_genai.py` and
`packages/rust/tests/observe_genai_conformance.rs`.

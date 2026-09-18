namespace Castia.Spec.Runtime;

@doc("Pure router registration helpers. Handler decorators and dispatch execution stay host-specific.")
interface RuntimeRouterRuntime {
  @sync
  @vector(#{
    name: "registered-protocols-activity-first-wire-order",
    stage: "callable",
    input: #{ activityRouteCount: 1, invokeNames: #[], wireProtocols: #["chat", "responses"] },
    expected: #["activity", "responses", "chat"],
  })
  @vector(#{
    name: "registered-protocols-invoke-only-is-activity",
    stage: "callable",
    input: #{ activityRouteCount: 0, invokeNames: #["message/submitAction"], wireProtocols: #[] },
    expected: #["activity"],
  })
  @vector(#{
    name: "registered-protocols-no-chat-publishable-assumption",
    stage: "callable",
    input: #{ activityRouteCount: 0, invokeNames: #[], wireProtocols: #["chat", "invocations", "responses_stream", "responses"] },
    expected: #["responses", "invocations", "chat"],
  })
  registeredProtocols(activityRouteCount: int32, invokeNames: string[], wireProtocols: string[]): string[];

  @sync
  @vector(#{
    name: "include-plan-merges-non-conflicting-routers",
    stage: "callable",
    input: #{ existingWire: #["responses"], incomingWire: #["chat"], existingInvokes: #["a"], incomingInvokes: #["b"] },
    expected: #{ ok: true, wire: #["responses", "chat"], invokes: #["a", "b"] },
  })
  @vector(#{
    name: "include-plan-rejects-duplicate-wire-protocol",
    stage: "callable",
    input: #{ existingWire: #["responses"], incomingWire: #["responses"], existingInvokes: #[], incomingInvokes: #[] },
    expectedError: #{ message: "protocol 'responses' already has a handler ('existing'); a wire protocol answers every caller, so it takes exactly one handler, but 'incoming' also registered @app.responses(). Keep a single handler per wire protocol." },
  })
  @vector(#{
    name: "include-plan-rejects-duplicate-invoke-name",
    stage: "callable",
    input: #{ existingWire: #[], incomingWire: #[], existingInvokes: #["message/submitAction"], incomingInvokes: #["message/submitAction"] },
    expectedError: #{ message: "invoke 'message/submitAction' already has a handler ('existing'); an invoke name is answered by exactly one handler, but 'incoming' also registered @app.invoke('message/submitAction'). Keep a single handler per invoke name." },
  })
  includePlan(existingWire: string[], incomingWire: string[], existingInvokes: string[], incomingInvokes: string[]): unknown;

  @sync
  @vector(#{
    name: "responses-only-default-name",
    stage: "callable",
    input: #{ name: "hal", hasResponses: true, toolNames: #["a", "a", "b"] },
    expected: #{ name: "hal-optimize", protocols: #["responses"], tools: #["a", "b"] },
  })
  @vector(#{
    name: "responses-only-requires-responses",
    stage: "callable",
    input: #{ name: "hal", hasResponses: false, toolNames: #[] },
    expectedError: #{ message: "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting." },
  })
  @vector(#{
    name: "responses-only-unnamed-agent-stays-unnamed",
    stage: "callable",
    input: #{ name: "", hasResponses: true, toolNames: #[] },
    expected: #{ name: null, protocols: #["responses"], tools: #[] },
  })
  responsesOnlyProjection(name: string, hasResponses: boolean, toolNames: string[]): unknown;
}

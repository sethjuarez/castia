namespace Castia.Spec.Delivery;

@doc("Pure azure.yaml protocol reconciliation helpers. YAML loading/writing and app importing stay host-specific.")
interface DeliveryManifestRuntime {
  @sync
  @vector(#{
    name: "publishable-protocols-filters-local-only",
    stage: "callable",
    input: #{ registeredProtocols: #["activity", "chat", "responses"] },
    expected: #["activity", "responses"],
  })
  publishableProtocols(registeredProtocols: string[]): string[];

  @sync
  @vector(#{
    name: "skipped-protocols-captures-local-only",
    stage: "callable",
    input: #{ registeredProtocols: #["activity", "chat", "responses"] },
    expected: #["chat"],
  })
  skippedProtocols(registeredProtocols: string[]): string[];

  @sync
  @vector(#{
    name: "service-protocol-items-pin-version",
    stage: "callable",
    input: #{ protocols: #["activity", "responses"] },
    expected: #[#{ protocol: "activity", version: "2.0.0" }, #{ protocol: "responses", version: "2.0.0" }],
  })
  serviceProtocolItems(protocols: string[]): unknown;

  @sync
  @vector(#{
    name: "manifest-plan-updates-named-service-and-endpoint",
    stage: "callable",
    input: #{ appName: "my-agent", registeredProtocols: #["activity", "chat", "responses"], manifest: #{ services: #{ `my-agent`: #{ protocols: #[#{ protocol: "activity", version: "1.0.0" }], agentEndpoint: #{ protocols: #["activity"] } } } }, check: true },
    expected: #{ service: "my-agent", desired: #["activity", "responses"], skipped: #["chat"], before_service: #["activity"], after_service: #["activity", "responses"], before_endpoint: #["activity"], after_endpoint: #["activity", "responses"], changed: true, written: false, notes: #["local-only protocol(s) not published to Foundry: chat"] },
  })
  @vector(#{
    name: "manifest-plan-single-service-fallback",
    stage: "callable",
    input: #{ appName: "agent-name", registeredProtocols: #["responses"], manifest: #{ services: #{ svc: #{ protocols: #[#{ protocol: "responses", version: "2.0.0" }] } } }, check: true },
    expected: #{ service: "svc", desired: #["responses"], skipped: #[], before_service: #["responses"], after_service: #["responses"], before_endpoint: null, after_endpoint: null, changed: false, written: false, notes: #[] },
  })
  @vector(#{
    name: "manifest-plan-multiple-services-require-name-match",
    stage: "callable",
    input: #{ appName: "missing", registeredProtocols: #["responses"], manifest: #{ services: #{ one: #{}, two: #{} } }, check: true },
    expectedError: #{ message: "agent name 'missing' not among services ['one', 'two']; cannot pick which service to update" },
  })
  @vector(#{
    name: "manifest-plan-write-branch-adds-authorization-note",
    stage: "callable",
    input: #{ appName: "my-agent", registeredProtocols: #["activity", "chat", "responses"], manifest: #{ services: #{ `my-agent`: #{ protocols: #[#{ protocol: "activity", version: "1.0.0" }], agentEndpoint: #{ protocols: #["activity"] } } } }, check: false },
    expected: #{ service: "my-agent", desired: #["activity", "responses"], skipped: #["chat"], before_service: #["activity"], after_service: #["activity", "responses"], before_endpoint: #["activity"], after_endpoint: #["activity", "responses"], changed: true, written: true, notes: #["local-only protocol(s) not published to Foundry: chat", "authorizationSchemes left as authored; a non-activity protocol may need its own scheme -- review before deploy"] },
  })
  @vector(#{
    name: "manifest-plan-endpoint-present-without-activity-note",
    stage: "callable",
    input: #{ appName: "my-agent", registeredProtocols: #["responses"], manifest: #{ services: #{ `my-agent`: #{ protocols: #[#{ protocol: "activity", version: "2.0.0" }], agentEndpoint: #{ protocols: #["activity"] } } } }, check: true },
    expected: #{ service: "my-agent", desired: #["responses"], skipped: #[], before_service: #["activity"], after_service: #["responses"], before_endpoint: #["activity"], after_endpoint: #["responses"], changed: true, written: false, notes: #["agentEndpoint present but 'activity' is not registered; review whether the Bot Service endpoint should remain"] },
  })
  planManifest(appName: string, registeredProtocols: string[], manifest: unknown, check: boolean): unknown;
}

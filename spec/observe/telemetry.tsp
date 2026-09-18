namespace Castia.Spec.Observe;

@doc("Offline telemetry query, error-classification, and trace-ingestion verification helpers.")
interface ObserveTelemetryRuntime {
  @sync
  @vector(#{
    name: "trace-query-hosted-correlation-and-content-minimization",
    stage: "callable",
    input: #{ query: #{ agentName: "agent", start: "2026-09-15T00:00:00Z", end: "2026-09-15T01:00:00Z", limit: 19, agentVersion: "3", probeTag: "test-tag", includeContent: false, anchor: "requests" } },
    expectedContains: #["requests, dependencies, customEvents", "| where timestamp >= datetime(2026-09-15T00:00:00+00:00) and timestamp < datetime(2026-09-15T01:00:00+00:00)", "agent_name == \"agent\"", "agent_id == \"agent:3\"", "probe_tag == \"test-tag\"", "customDimensions=bag_pack("],
    expectedNotContains: #["gen_ai.input.messages"],
    expectedSuffix: "| take 19",
    expected: null,
  })
  @vector(#{
    name: "trace-query-local-invoke-agent-anchor",
    stage: "callable",
    input: #{ query: #{ agentName: "agent", start: "2026-09-15T00:00:00Z", end: "2026-09-15T01:00:00Z", limit: 100, probeTag: "probe-id", includeContent: false, anchor: "invoke_agent", traceId: "trace-id" } },
    expectedContains: #["let scoped_operations = union isfuzzy=true dependencies, customEvents", "customDimensions[\"gen_ai.operation.name\"]) == \"invoke_agent\"", "operation_Id == \"trace-id\"", "castia.probe_id", "withsource=castia_table_name"],
    expectedSuffix: "| take 100",
    expected: null,
  })
  traceQueryKql(query: unknown): unknown;

  @sync
  @vector(#{
    name: "http-error-classifies-status-without-raw-content",
    stage: "callable",
    input: #{ status: 403 },
    expected: #{ category: "authorization", message: "Service returned HTTP 403.", statusCode: 403 },
  })
  @vector(#{
    name: "http-error-classifies-service",
    stage: "callable",
    input: #{ status: 500 },
    expected: #{ category: "service", message: "Service returned HTTP 500.", statusCode: 500 },
  })
  httpError(status: int32): unknown;

  @sync
  @vector(#{
    name: "verify-probe-finds-exact-tag-after-bounded-polls",
    stage: "callable",
    input: #{ attempts: #[#[#{ probeTag: "unrelated" }], #[#{ probeTag: "unrelated" }], #[#{ probeTag: "wanted" }]], probeTag: "wanted", timeoutSeconds: 60.0, pollSeconds: 2.0, maxAttempts: 12 },
    expected: #{ status: "pass", probeTag: "wanted", attempts: 3, matchedRecords: 1, elapsedSeconds: 4.0, diagnostic: "Tagged trace visible." },
  })
  @vector(#{
    name: "verify-probe-blocks-missing-tag-without-fake-pass",
    stage: "callable",
    input: #{ attempts: #[#[], #[]], probeTag: "wanted", timeoutSeconds: 3.0, pollSeconds: 2.0, maxAttempts: 12 },
    expected: #{ status: "blocked", probeTag: "wanted", attempts: 2, matchedRecords: 0, elapsedSeconds: 3.0, diagnostic: "Tagged trace not observed within the ingestion window; not proof of agent failure." },
  })
  verifyProbe(attempts: unknown, probeTag: string, timeoutSeconds: float64, pollSeconds: float64, maxAttempts: int32): unknown;

  @sync
  @vector(#{
    name: "telemetry-probe-query-sanitizes-content",
    stage: "callable",
    input: #{ query: #{ agentName: "agent", includeContent: true, probeTag: "tag" }, ingestionStatus: "pass", ingestionDiagnostic: "Tagged trace visible.", ingestionEvidence: #{ matchedRecords: 1 } },
    expected: #{ query: #{ agentName: "agent", includeContent: false, probeTag: "tag" }, ingestion: #{ status: "pass", diagnostic: "Tagged trace visible.", reason: null, evidence: #{ matchedRecords: 1 } } },
  })
  @vector(#{
    name: "telemetry-probe-ingestion-needs-tag",
    stage: "callable",
    input: #{ query: #{ agentName: "agent", includeContent: true }, ingestionStatus: "pass", ingestionDiagnostic: "ignored", ingestionEvidence: #{} },
    expected: #{ query: #{ agentName: "agent", includeContent: false }, ingestion: #{ status: "blocked", diagnostic: "An exact tagged probe is required.", reason: "prerequisite", evidence: null } },
  })
  telemetryProbePlan(query: unknown, ingestionStatus: string, ingestionDiagnostic: string, ingestionEvidence: unknown): unknown;
}

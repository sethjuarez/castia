namespace Castia.Spec.Observe;

@doc("Content-minimizing execution record normalization and summary contract.")
interface ObserveRecordsRuntime {
  @sync
  @vector(#{
    name: "normalize-record-minimizes-content-and-derives-usage",
    stage: "callable",
    input: #{
      row: #{
        timestamp: "2026-09-15T08:00:00-07:00",
        source: "requests",
        name: "chat model",
        operation_Id: "trace",
        id: "span",
        operation_ParentId: "parent",
        success: "true",
        duration: 42.5,
        customDimensions: #{
          `gen_ai.agent.id`: "test-agent:3",
          `gen_ai.usage.input_tokens`: "7",
          `gen_ai.usage.output_tokens`: 3,
          `gen_ai.tool.name`: "lookup",
          `castia.probe_tag`: "probe",
          `gen_ai.input.messages`: "PRIVATE PROMPT",
          Authorization: "SECRET"
        }
      },
      includeContent: false
    },
    expected: #{ timestamp: "2026-09-15T15:00:00Z", agentName: "test-agent", agentVersion: "3", traceId: "trace", spanId: "span", parentId: "parent", source: "requests", operation: "chat model", status: "success", latencyMs: 42.5, inputTokens: 7, outputTokens: 3, totalTokens: 10, toolName: "lookup", probeTag: "probe", cost: null },
  })
  @vector(#{
    name: "normalize-record-content-is-opt-in-and-allowlisted",
    stage: "callable",
    input: #{ row: #{ customDimensions: #{ `gen_ai.input.messages`: "PRIVATE PROMPT", Authorization: "SECRET" } }, includeContent: true },
    expected: #{ timestamp: null, agentName: null, agentVersion: null, traceId: null, spanId: null, parentId: null, source: null, operation: null, status: "unknown", latencyMs: null, inputTokens: null, outputTokens: null, totalTokens: null, toolName: null, probeTag: null, cost: null, content: #{ `gen_ai.input.messages`: "PRIVATE PROMPT" } },
  })
  @vector(#{
    name: "normalize-record-recognizes-agentserver-identity",
    stage: "callable",
    input: #{ row: #{ customDimensions: #{ `azure.ai.agentserver.agent_name`: "hal-autopilot", `azure.ai.agentserver.agent_version`: "3" } }, includeContent: false },
    expected: #{ timestamp: null, agentName: "hal-autopilot", agentVersion: "3", traceId: null, spanId: null, parentId: null, source: null, operation: null, status: "unknown", latencyMs: null, inputTokens: null, outputTokens: null, totalTokens: null, toolName: null, probeTag: null, cost: null },
  })
  normalizeRecord(row: unknown, includeContent: boolean): unknown;

  @sync
  @vector(#{
    name: "summarize-distinguishes-unknowns-and-zeroes",
    stage: "callable",
    input: #{
      records: #[
        #{ timestamp: null, agentName: null, agentVersion: null, traceId: null, spanId: null, parentId: null, source: null, operation: null, status: "success", latencyMs: 0, inputTokens: 0, outputTokens: null, totalTokens: null, toolName: "tool", probeTag: null, cost: null },
        #{ timestamp: null, agentName: null, agentVersion: null, traceId: null, spanId: null, parentId: null, source: null, operation: null, status: "error", latencyMs: 100, inputTokens: 10, outputTokens: null, totalTokens: null, toolName: "tool", probeTag: null, cost: null },
        #{ timestamp: null, agentName: null, agentVersion: null, traceId: null, spanId: null, parentId: null, source: null, operation: null, status: "unknown", latencyMs: null, inputTokens: null, outputTokens: null, totalTokens: null, toolName: null, probeTag: null, cost: null }
      ]
    },
    expected: #{ recordCount: 3, errorCount: 1, observedErrorCount: 1, knownStatusCount: 2, unknownStatusCount: 1, errorRate: 0.5, errorRateDenominator: "known_status_records", latencyMs: #{ count: 2, unknownCount: 1, min: 0.0, max: 100.0, p50: 50.0, p95: 95.0, p99: 99.0 }, toolErrors: #{ tool: 1 }, toolStatusCounts: #{ tool: #{ known: 2, `unknown`: 0 } }, usage: #{ inputTokens: #{ observedSum: 10, knownRecords: 2, unknownRecords: 1 }, outputTokens: #{ observedSum: null, knownRecords: 0, unknownRecords: 3 }, totalTokens: #{ observedSum: null, knownRecords: 0, unknownRecords: 3 } }, cost: null, costStatus: "unknown", scope: "observed_spans_may_be_sampled_or_duplicate_usage" },
  })
  summarize(records: unknown): unknown;
}

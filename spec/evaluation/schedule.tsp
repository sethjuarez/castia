namespace Castia.Spec.Evaluation;

@doc("Pure payload builders for scheduled (timerTrigger) evaluation rules and windowed trace evaluation runs, matching the azure-ai-projects 2.11 wire shapes.")
interface EvaluationScheduleRuntime {
  @sync
  @vector(#{ name: "window-hours", stage: "callable", input: #{ text: "24h" }, expected: 86400 })
  @vector(#{ name: "window-minutes-case-insensitive", stage: "callable", input: #{ text: " 30M " }, expected: 1800 })
  @vector(#{ name: "window-days", stage: "callable", input: #{ text: "7d" }, expected: 604800 })
  @vector(#{ name: "window-rejects-unknown-unit", stage: "callable", input: #{ text: "2w" }, expectedError: #{ message: "window must look like 30m, 24h, or 7d" } })
  @vector(#{ name: "window-rejects-zero", stage: "callable", input: #{ text: "0h" }, expectedError: #{ message: "window must be greater than zero" } })
  parseWindow(text: string): unknown;

  @sync
  @vector(#{
    name: "trace-window-full",
    stage: "callable",
    input: #{ request: #{ agent_name: "support", window_seconds: 3600, end_time: 1700003600, agent_version: "3", max_traces: 50, filter_strategy: "smart_filtering" } },
    expected: #{
      type: "azure_ai_trace_data_source",
      trace_source: #{ type: "agent_filter", agent_name: "support", start_time: 1700000000, end_time: 1700003600, agent_version: "3", max_traces: 50, filter_strategy: "smart_filtering" },
    },
  })
  @vector(#{
    name: "trace-window-minimal",
    stage: "callable",
    input: #{ request: #{ agent_name: "support", window_seconds: 60, end_time: 120 } },
    expected: #{ type: "azure_ai_trace_data_source", trace_source: #{ type: "agent_filter", agent_name: "support", start_time: 60, end_time: 120 } },
  })
  @vector(#{
    name: "trace-window-rejects-unknown-strategy",
    stage: "callable",
    input: #{ request: #{ agent_name: "support", window_seconds: 60, end_time: 120, filter_strategy: "newest" } },
    expectedError: #{ message: "filter_strategy must be one of random_sampling, smart_filtering" },
  })
  @vector(#{
    name: "trace-window-rejects-zero-max-traces",
    stage: "callable",
    input: #{ request: #{ agent_name: "support", window_seconds: 60, end_time: 120, max_traces: 0 } },
    expectedError: #{ message: "max_traces must be greater than zero" },
  })
  traceWindowSource(request: unknown): unknown;

  @sync
  @vector(#{
    name: "trigger-cron-normalizes-whitespace",
    stage: "callable",
    input: #{ request: #{ cron: "0  9 * * 1-5", time_zone: "Pacific Standard Time" } },
    expected: #{ type: "Cron", expression: "0 9 * * 1-5", timeZone: "Pacific Standard Time" },
  })
  @vector(#{
    name: "trigger-every-hours",
    stage: "callable",
    input: #{ request: #{ every_hours: 6 } },
    expected: #{ type: "Recurrence", interval: 6, schedule: #{ type: "Hourly" } },
  })
  @vector(#{
    name: "trigger-daily-sorted-unique",
    stage: "callable",
    input: #{ request: #{ daily_hours: #[21, 9, 21] } },
    expected: #{ type: "Recurrence", interval: 1, schedule: #{ type: "Daily", hours: #[9, 21] } },
  })
  @vector(#{
    name: "trigger-rejects-two-cadences",
    stage: "callable",
    input: #{ request: #{ cron: "0 9 * * *", every_hours: 1 } },
    expectedError: #{ message: "set exactly one of cron, every_hours, or daily_hours" },
  })
  @vector(#{
    name: "trigger-rejects-short-cron",
    stage: "callable",
    input: #{ request: #{ cron: "0 9 * *" } },
    expectedError: #{ message: "cron must have five fields: minute hour day month weekday" },
  })
  @vector(#{
    name: "trigger-rejects-hour-out-of-range",
    stage: "callable",
    input: #{ request: #{ daily_hours: #[24] } },
    expectedError: #{ message: "daily_hours must be between 0 and 23" },
  })
  scheduleTrigger(request: unknown): unknown;

  @sync
  @vector(#{
    name: "rule-traces",
    stage: "callable",
    input: #{ request: #{ eval_id: "eval_1", agent_name: "support", source_kind: "traces", trigger: #{ type: "Recurrence", interval: 6, schedule: #{ type: "Hourly" } }, max_traces: 25 } },
    expected: #{
      action: #{ type: "traceEvaluation", evalId: "eval_1", source: #{ type: "azure_ai_traces", agent_name: "support", max_traces: 25 } },
      eventType: "timerTrigger",
      schedule: #{ type: "Recurrence", interval: 6, schedule: #{ type: "Hourly" } },
      enabled: true,
    },
  })
  @vector(#{
    name: "rule-dataset-disabled-with-version",
    stage: "callable",
    input: #{
      request: #{
        eval_id: "eval_1",
        agent_name: "support",
        source_kind: "dataset",
        trigger: #{ type: "Cron", expression: "0 9 * * *" },
        dataset_file_id: "file-1",
        agent_version: "4",
        query_template: "{{item.question}}",
        display_name: "Nightly support",
        enabled: false,
      },
    },
    expected: #{
      action: #{
        type: "datasetEvaluation",
        evalId: "eval_1",
        source: #{
          type: "azure_ai_target_completions",
          source: #{ type: "file_id", id: "file-1" },
          input_messages: #{
            type: "template",
            template: #[#{ type: "message", role: "user", content: #{ type: "input_text", text: "{{item.question}}" } }],
          },
          target: #{ type: "azure_ai_agent", name: "support", version: "4" },
        },
      },
      eventType: "timerTrigger",
      schedule: #{ type: "Cron", expression: "0 9 * * *" },
      enabled: false,
      displayName: "Nightly support",
    },
  })
  @vector(#{
    name: "rule-dataset-requires-file",
    stage: "callable",
    input: #{ request: #{ eval_id: "eval_1", agent_name: "support", source_kind: "dataset", trigger: #{ type: "Cron", expression: "0 9 * * *" } } },
    expectedError: #{ message: "dataset rules require dataset_file_id" },
  })
  @vector(#{
    name: "rule-traces-rejects-dataset-file",
    stage: "callable",
    input: #{ request: #{ eval_id: "eval_1", agent_name: "support", source_kind: "traces", trigger: #{ type: "Cron", expression: "0 9 * * *" }, dataset_file_id: "file-1" } },
    expectedError: #{ message: "dataset_file_id is only valid for dataset rules" },
  })
  @vector(#{
    name: "rule-rejects-unknown-kind",
    stage: "callable",
    input: #{ request: #{ eval_id: "eval_1", agent_name: "support", source_kind: "responses", trigger: #{ type: "Cron", expression: "0 9 * * *" } } },
    expectedError: #{ message: "source_kind must be one of traces, dataset" },
  })
  evaluationRule(request: unknown): unknown;
}

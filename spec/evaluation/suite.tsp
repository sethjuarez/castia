namespace Castia.Spec.Evaluation;

@doc("Pure evaluation suite command builders and rubric parsing.")
interface EvaluationSuiteRuntime {
  @sync
  @vector(#{
    name: "generate-argv-minimal",
    stage: "callable",
    input: #{ options: #{ outFile: "" } },
    expected: #["azd", "ai", "agent", "eval", "generate"],
  })
  @vector(#{
    name: "generate-argv-flags",
    stage: "callable",
    input: #{ options: #{ agent: "hal", genInstructionFile: ".agent_configs/baseline/instructions.md", evalModel: "gpt-4o", maxSamples: 15, evaluators: #["builtin.task_adherence", "smoke-core"], outFile: "eval.generated.yaml", resetDefaults: true, noWait: true, noPrompt: true } },
    expected: #["azd", "ai", "agent", "eval", "generate", "--agent", "hal", "--gen-instruction-file", ".agent_configs/baseline/instructions.md", "--eval-model", "gpt-4o", "--max-samples", "15", "--evaluator", "builtin.task_adherence", "--evaluator", "smoke-core", "--out-file", "eval.generated.yaml", "--reset-defaults", "--no-wait", "--no-prompt"],
  })
  buildGenerateArgv(options: unknown): unknown;

  @sync
  @vector(#{
    name: "update-argv-flags",
    stage: "callable",
    input: #{ options: #{ config: "eval.generated.yaml", evaluatorOnly: true, noPrompt: true } },
    expected: #["azd", "ai", "agent", "eval", "update", "--config", "eval.generated.yaml", "--evaluator-only", "--no-prompt"],
  })
  @vector(#{
    name: "update-argv-null-config",
    stage: "callable",
    input: #{ options: #{ config: null, evaluatorOnly: "yes" } },
    expected: #["azd", "ai", "agent", "eval", "update", "--evaluator-only"],
  })
  buildUpdateArgv(options: unknown): unknown;

  @sync
  @vector(#{
    name: "run-argv-flags",
    stage: "callable",
    input: #{ options: #{ config: "eval.yaml", name: "nightly", noWait: true } },
    expected: #["azd", "ai", "agent", "eval", "run", "--config", "eval.yaml", "--name", "nightly", "--no-wait"],
  })
  @vector(#{
    name: "run-argv-numeric-config",
    stage: "callable",
    input: #{ options: #{ config: 2024, name: true, noWait: 1 } },
    expected: #["azd", "ai", "agent", "eval", "run", "--config", "2024", "--name", "True", "--no-wait"],
  })
  buildRunArgv(options: unknown): unknown;

  @sync
  @vector(#{
    name: "read-rubric-bare-list",
    stage: "callable",
    input: #{ value: #[#{ id: "correct_outcome", description: "Correct", weight: 10 }, #{ id: "general_quality", description: "General", always_applicable: true }] },
    expected: #[#{ id: "correct_outcome", description: "Correct", weight: 10.0, always_applicable: false }, #{ id: "general_quality", description: "General", weight: null, always_applicable: true }],
  })
  @vector(#{
    name: "read-rubric-wrapper-and-name-alias",
    stage: "callable",
    input: #{ value: #{ dimensions: #[#{ name: "legacy_slug", description: "d", weight: 1 }, #{ id: "wins", name: "loses", description: "d" }] } },
    expected: #[#{ id: "legacy_slug", description: "d", weight: 1.0, always_applicable: false }, #{ id: "wins", description: "d", weight: null, always_applicable: false }],
  })
  @vector(#{
    name: "read-rubric-python-coercions",
    stage: "callable",
    input: #{ value: #[#{ id: "", name: 2024, description: 5, always_applicable: "yes" }, #{ id: null, name: "fallback", description: "", always_applicable: 0 }] },
    expected: #[#{ id: "2024", description: "5", weight: null, always_applicable: true }, #{ id: "fallback", description: "", weight: null, always_applicable: false }],
  })
  readRubric(value: unknown): unknown;
}

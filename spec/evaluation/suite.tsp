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

  @sync
  @vector(#{
    name: "load-suite-parses-evaluators-and-datasets",
    stage: "callable",
    files: #[
      #{ path: "eval.yaml", content: "name: smoke\nagent:\n  name: hal\nevaluators:\n  - builtin.task_adherence\n  - name: smoke-core\n    kind: custom\n    local_uri: rubric.json\n    version: 1\ndataset:\n  local_uri: eval.jsonl\n  version: 2\nvalidation_dataset:\n  dataset_file: validation.jsonl\noptions:\n  eval_model: gpt-4o\n" },
    ],
    input: #{ path: "$temp/eval.yaml" },
    expected: #{
      path: "$temp/eval.yaml",
      name: "smoke",
      agent: #{ name: "hal" },
      evaluators: #[#{ name: "builtin.task_adherence", kind: null, local_uri: null, version: null, builtin: true }, #{ name: "smoke-core", kind: "custom", local_uri: "rubric.json", version: "1", builtin: false }],
      datasets: #[#{ role: "dataset", local_uri: "eval.jsonl", dataset_file: null, version: "2" }, #{ role: "validation_dataset", local_uri: null, dataset_file: "validation.jsonl", version: null }],
      options: #{ eval_model: "gpt-4o" },
    },
  })
  loadSuite(path: string): unknown;

  @sync
  @vector(#{
    name: "validate-suite-ok",
    stage: "callable",
    files: #[
      #{ path: "eval.yaml", content: "agent:\n  name: hal\nevaluators:\n  - name: smoke-core\n    version: '1'\n    local_uri: rubric.json\n" },
      #{ path: "rubric.json", content: "[{\"id\":\"correct_outcome\",\"description\":\"Correct\",\"weight\":10},{\"id\":\"general_quality\",\"description\":\"General\",\"always_applicable\":true}]" },
    ],
    input: #{ path: "$temp/eval.yaml" },
    expected: #{ ok: true, problems: #[], notes: #[], rubrics: #[#{ name: "smoke-core", count: 2 }] },
  })
  @vector(#{
    name: "validate-suite-missing-rubric",
    stage: "callable",
    files: #[
      #{ path: "eval.yaml", content: "agent:\n  name: hal\nevaluators:\n  - name: smoke-core\n    local_uri: missing.json\n" },
    ],
    input: #{ path: "$temp/eval.yaml" },
    expected: #{ ok: false, problems: #["evaluator 'smoke-core': rubric dimensions file 'missing.json' does not exist"], notes: #[], rubrics: #[] },
  })
  @vector(#{
    name: "validate-suite-no-evaluators-note",
    stage: "callable",
    files: #[
      #{ path: "eval.yaml", content: "agent:\n  name: hal\n" },
    ],
    input: #{ path: "$temp/eval.yaml" },
    expected: #{ ok: true, problems: #[], notes: #["no evaluators declared -- run 'python -m castia eval generate' to synthesize a rubric, or add builtin.<name>"], rubrics: #[] },
  })
  @vector(#{
    name: "validate-suite-duplicate-rubric-labels-last-wins",
    stage: "callable",
    files: #[
      #{ path: "eval.yaml", content: "evaluators:\n  - name: smoke-core\n    local_uri: first.json\n  - name: smoke-core\n    local_uri: second.json\n" },
      #{ path: "first.json", content: "[{\"id\":\"first\",\"description\":\"First\"}]" },
      #{ path: "second.json", content: "[{\"id\":\"second\",\"description\":\"Second\"},{\"id\":\"general\",\"description\":\"General\"}]" },
    ],
    input: #{ path: "$temp/eval.yaml" },
    expected: #{ ok: true, problems: #[], notes: #[], rubrics: #[#{ name: "smoke-core", count: 2 }] },
  })
  validateSuite(path: string): unknown;
}

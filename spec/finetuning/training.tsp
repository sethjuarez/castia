namespace Castia.Spec.Finetuning;

@doc("Pure fine-tuning validators and payload builders. Uploads and job submissions are intentionally out of scope.")
interface FinetuningTrainingRuntime {
  @sync
  @vector(#{
    name: "sft-validates-assistant-completion",
    stage: "callable",
    input: #{ row: #{ messages: #[#{ role: "system", content: "Be terse." }, #{ role: "user", content: "What is 2+2?" }, #{ role: "assistant", content: "4" }] } },
    expected: #[],
  })
  @vector(#{
    name: "sft-rejects-non-assistant-final-message",
    stage: "callable",
    input: #{ row: #{ messages: #[#{ role: "user", content: "What is 2+2?" }, #{ role: "user", content: "4" }] } },
    expected: #["example: messages must include at least one assistant message", "example: final message role must be 'assistant', got 'user'"],
  })
  @vector(#{
    name: "sft-accepts-tool-call-assistant-message",
    stage: "callable",
    input: #{ row: #{ messages: #[#{ role: "user", content: "Use a tool." }, #{ role: "assistant", content: null, tool_calls: #[#{ id: "call_1", type: "function" }] }] } },
    expected: #[],
  })
  validateSftExample(row: unknown): string[];

  @sync
  @vector(#{
    name: "sft-build-method-with-hyperparameters",
    stage: "callable",
    input: #{ hyperparameters: #{ n_epochs: 2, learning_rate_multiplier: 0.5 } },
    expected: #{ type: "supervised", supervised: #{ hyperparameters: #{ n_epochs: 2, learning_rate_multiplier: 0.5 } } },
  })
  @vector(#{
    name: "sft-build-method-rejects-unknown-hyperparameter",
    stage: "callable",
    input: #{ hyperparameters: #{ beta: 0.1 } },
    expectedError: #{ message: "unknown SFT hyperparameter(s): beta (known: n_epochs, batch_size, learning_rate_multiplier)" },
  })
  buildSftMethod(hyperparameters: unknown): unknown;

  @sync
  @vector(#{
    name: "sft-build-job-omits-empty-optional-values",
    stage: "callable",
    input: #{ `model`: "gpt-4.1-mini", trainingFile: "file-train", validationFile: "", hyperparameters: #{}, suffix: null, seed: null },
    expected: #{ `model`: "gpt-4.1-mini", training_file: "file-train", method: #{ type: "supervised" } },
  })
  buildSftJob(`model`: string, trainingFile: string, validationFile: string | null, hyperparameters: unknown, suffix: string | null, seed: int32 | null): unknown;

  @sync
  @vector(#{
    name: "dpo-validates-preference-pair",
    stage: "callable",
    input: #{ row: #{ input: #{ messages: #[#{ role: "system", content: "Be precise." }, #{ role: "user", content: "Explain gravity." }] }, preferred_output: #[#{ role: "assistant", content: "Gravity attracts objects with mass." }], non_preferred_output: #[#{ role: "assistant", content: "Stuff falls." }] } },
    expected: #[],
  })
  @vector(#{
    name: "dpo-rejects-user-preferred-output",
    stage: "callable",
    input: #{ row: #{ input: #{ messages: #[#{ role: "user", content: "Explain gravity." }] }, preferred_output: #[#{ role: "user", content: "Nope" }], non_preferred_output: #[#{ role: "assistant", content: "Stuff falls." }] } },
    expected: #["preferred_output: message #1 role must be 'assistant' or 'tool', got 'user'", "preferred_output: must include at least one assistant message"],
  })
  validateDpoExample(row: unknown): string[];

  @sync
  @vector(#{
    name: "dpo-build-method-with-hyperparameters",
    stage: "callable",
    input: #{ hyperparameters: #{ beta: 0.1, l2_multiplier: 0.2 } },
    expected: #{ type: "dpo", dpo: #{ hyperparameters: #{ beta: 0.1, l2_multiplier: 0.2 } } },
  })
  @vector(#{
    name: "dpo-build-method-rejects-unknown-hyperparameter",
    stage: "callable",
    input: #{ hyperparameters: #{ reasoning_effort: "high" } },
    expectedError: #{ message: "unknown DPO hyperparameter(s): reasoning_effort (known: n_epochs, batch_size, learning_rate_multiplier, beta, l2_multiplier)" },
  })
  buildDpoMethod(hyperparameters: unknown): unknown;

  @sync
  @vector(#{
    name: "string-check-grader-default-shape",
    stage: "callable",
    input: #{ name: "exact", input: "{{ sample.output_text }}", reference: "{{ item.answer }}", operation: "eq" },
    expected: #{ type: "string_check", name: "exact", input: "{{ sample.output_text }}", reference: "{{ item.answer }}", operation: "eq" },
  })
  @vector(#{
    name: "string-check-grader-rejects-bad-operation",
    stage: "callable",
    input: #{ name: "x", input: "a", reference: "b", operation: "matches" },
    expectedError: #{ message: "operation must be one of ('eq', 'ne', 'like', 'ilike'), got 'matches'" },
  })
  stringCheckGrader(name: string, input: string, reference: string, operation: string): unknown;

  @sync
  @vector(#{
    name: "validate-grader-rejects-unknown-type-and-template-namespace",
    stage: "callable",
    input: #{ grader: #{ type: "mystery", name: "n", input: "{{ foo.bar }}" } },
    expected: #["grader type 'mystery' is not one of ('string_check', 'text_similarity', 'score_model', 'python', 'multi', 'endpoint')", "template {{ foo.bar }} uses unknown namespace 'foo' (expected one of ('sample', 'item'))"],
  })
  @vector(#{
    name: "validate-grader-prefixes-sub-grader-problems",
    stage: "callable",
    input: #{ grader: #{ type: "multi", name: "combo", graders: #{ acc: #{ type: "string_check", name: "acc" } }, calculate_output: "acc" } },
    expected: #["sub-grader 'acc': string_check grader missing required field 'input'", "sub-grader 'acc': string_check grader missing required field 'reference'", "sub-grader 'acc': string_check grader missing required field 'operation'"],
  })
  @vector(#{
    name: "validate-grader-rejects-numeric-string-check-operation",
    stage: "callable",
    input: #{ grader: #{ type: "string_check", name: "acc", input: "a", reference: "b", operation: 5 } },
    expected: #["string_check operation 5 not in ('eq', 'ne', 'like', 'ilike')"],
  })
  validateGrader(grader: unknown): string[];

  @sync
  @vector(#{
    name: "rft-example-requires-final-user",
    stage: "callable",
    input: #{ row: #{ messages: #[#{ role: "system", content: "be helpful" }, #{ role: "assistant", content: "the prompt" }] } },
    expected: #["final message role must be 'user', got 'assistant' (RFT generates the model's turn from the trailing user prompt)"],
  })
  @vector(#{
    name: "rft-example-non-object-last-message-does-not-add-final-role-problem",
    stage: "callable",
    input: #{ row: #{ messages: #["oops"] } },
    expected: #["message #1 is not a role-bearing object"],
  })
  @vector(#{
    name: "rft-example-numeric-role-is-invalid-not-missing",
    stage: "callable",
    input: #{ row: #{ messages: #[#{ role: 5, content: "bad" }] } },
    expected: #["message #1 has invalid role 5", "final message role must be 'user', got 5 (RFT generates the model's turn from the trailing user prompt)"],
  })
  validateRftExample(row: unknown): string[];

  @sync
  @vector(#{
    name: "rft-dataset-cross-checks-grader-item-fields",
    stage: "callable",
    input: #{ rows: #[#{ messages: #[#{ role: "user", content: "Solve it." }], answer: "42" }, #{ messages: #[#{ role: "user", content: "Solve it." }] }], grader: #{ type: "string_check", name: "acc", input: "{{ sample.output_text }}", reference: "{{ item.answer }}", operation: "eq" }, split: "training" },
    expected: #["training[1]: grader references item fields not on the row: answer"],
  })
  validateRftDataset(rows: unknown, grader: unknown, split: string): string[];

  @sync
  @vector(#{
    name: "rft-build-method-allows-reasoning-effort",
    stage: "callable",
    input: #{ grader: #{ type: "string_check", name: "acc", input: "{{ sample.output_text }}", reference: "{{ item.a }}", operation: "eq" }, hyperparameters: #{ reasoning_effort: "high" }, responseFormat: null },
    expected: #{ type: "reinforcement", reinforcement: #{ grader: #{ type: "string_check", name: "acc", input: "{{ sample.output_text }}", reference: "{{ item.a }}", operation: "eq" }, hyperparameters: #{ reasoning_effort: "high" } } },
  })
  @vector(#{
    name: "rft-build-method-rejects-unknown-hyperparameter",
    stage: "callable",
    input: #{ grader: #{ type: "string_check", name: "acc" }, hyperparameters: #{ learningrate: 0.1 }, responseFormat: null },
    expectedError: #{ message: "unknown RFT hyperparameter(s): learningrate (known: eval_interval, eval_samples, compute_multiplier, reasoning_effort, n_epochs, batch_size, learning_rate_multiplier)" },
  })
  buildRftMethod(grader: unknown, hyperparameters: unknown, responseFormat: unknown): unknown;

  @sync
  @vector(#{
    name: "rft-build-job-requires-validation-file",
    stage: "callable",
    input: #{ `model`: "o4-mini", trainingFile: "file-train", validationFile: "", grader: #{ type: "string_check", name: "acc" }, hyperparameters: #{}, responseFormat: null, suffix: null, seed: null },
    expectedError: #{ message: "RFT requires a validation_file in addition to training_file" },
  })
  @vector(#{
    name: "rft-build-job-forwards-response-format",
    stage: "callable",
    input: #{ `model`: "o4-mini", trainingFile: "file-train", validationFile: "file-val", grader: #{ type: "string_check", name: "acc" }, hyperparameters: #{}, responseFormat: #{ type: "json_object" }, suffix: null, seed: null },
    expected: #{ `model`: "o4-mini", training_file: "file-train", validation_file: "file-val", method: #{ type: "reinforcement", reinforcement: #{ grader: #{ type: "string_check", name: "acc" }, response_format: #{ type: "json_object" } } } },
  })
  buildRftJob(`model`: string, trainingFile: string, validationFile: string, grader: unknown, hyperparameters: unknown, responseFormat: unknown, suffix: string | null, seed: int32 | null): unknown;
}

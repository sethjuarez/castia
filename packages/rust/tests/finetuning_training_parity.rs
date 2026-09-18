use castia::finetuning::{
    build_dpo_job, build_dpo_method, build_rft_job, build_rft_method, build_sft_job,
    build_sft_method, grader_item_fields, multi_grader, python_grader, score_model_grader,
    string_check_grader, text_similarity_grader, validate_dpo_example, validate_dpo_splits,
    validate_grader, validate_rft_dataset, validate_rft_example, validate_rft_splits,
    validate_sft_example, validate_sft_splits,
};
use serde_json::json;

fn sft_row(final_role: &str) -> serde_json::Value {
    json!({
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": final_role, "content": "4"},
        ],
    })
}

fn dpo_row() -> serde_json::Value {
    json!({
        "input": {
            "messages": [
                {"role": "system", "content": "Be precise."},
                {"role": "user", "content": "Explain gravity."},
            ],
        },
        "preferred_output": [
            {"role": "assistant", "content": "Gravity attracts objects with mass."},
        ],
        "non_preferred_output": [
            {"role": "assistant", "content": "Stuff falls."},
        ],
    })
}

#[test]
fn sft_validators_accept_tool_calls_and_pin_final_role() {
    assert!(validate_sft_example(&sft_row("assistant")).is_empty());
    let problems = validate_sft_example(&sft_row("user"));
    assert!(problems
        .iter()
        .any(|problem| problem.contains("final message role must be 'assistant'")));
    assert!(validate_sft_splits(&[sft_row("assistant")], Some(&[sft_row("assistant")])).is_empty());

    let mut row = sft_row("assistant");
    row["messages"][2] = json!({
        "role": "assistant",
        "content": null,
        "tool_calls": [{"id": "call_1", "type": "function"}],
    });
    assert!(validate_sft_example(&row).is_empty());
}

#[test]
fn sft_job_builder_matches_python_payload_rules() {
    assert_eq!(
        build_sft_method(Some(
            &json!({"n_epochs": 2, "learning_rate_multiplier": 0.5})
        ))
        .unwrap(),
        json!({
            "type": "supervised",
            "supervised": {"hyperparameters": {"n_epochs": 2, "learning_rate_multiplier": 0.5}},
        })
    );
    assert_eq!(
        build_sft_job(
            "gpt-4.1-mini",
            "file-train",
            Some("file-val"),
            Some(&json!({"n_epochs": 2})),
            Some("sft1"),
            Some(7),
        )
        .unwrap()["validation_file"],
        json!("file-val")
    );
    assert_eq!(
        build_sft_method(Some(&json!({"beta": 0.1})))
            .unwrap_err()
            .to_string(),
        "unknown SFT hyperparameter(s): beta (known: n_epochs, batch_size, learning_rate_multiplier)"
    );
}

#[test]
fn dpo_validators_and_builders_match_python() {
    assert!(validate_dpo_example(&dpo_row()).is_empty());
    let mut bad = dpo_row();
    bad["preferred_output"] = json!([{"role": "user", "content": "Nope"}]);
    let problems = validate_dpo_example(&bad);
    assert!(problems
        .iter()
        .any(|problem| problem.contains("preferred_output") && problem.contains("role must be")));
    assert!(validate_dpo_splits(&[dpo_row()], None).is_empty());

    let mut tool_call = dpo_row();
    tool_call["preferred_output"] = json!([{
        "role": "assistant",
        "content": null,
        "tool_calls": [{"id": "call_1", "type": "function"}],
    }]);
    assert!(validate_dpo_example(&tool_call).is_empty());

    assert_eq!(
        build_dpo_job(
            "gpt-4.1",
            "file-train",
            None,
            Some(&json!({"beta": 0.1, "l2_multiplier": 0.2})),
            None,
            None,
        )
        .unwrap()["method"],
        json!({
            "type": "dpo",
            "dpo": {"hyperparameters": {"beta": 0.1, "l2_multiplier": 0.2}},
        })
    );
    assert_eq!(
        build_dpo_method(Some(&json!({"reasoning_effort": "high"})))
            .unwrap_err()
            .to_string(),
        "unknown DPO hyperparameter(s): reasoning_effort (known: n_epochs, batch_size, learning_rate_multiplier, beta, l2_multiplier)"
    );
}

#[test]
fn grader_builders_and_validation_match_python() {
    let exact = string_check_grader(
        "exact",
        "{{ sample.output_text }}",
        "{{ item.answer }}",
        "eq",
    )
    .unwrap();
    assert_eq!(exact["type"], json!("string_check"));
    assert!(validate_grader(&exact).is_empty());
    assert_eq!(
        string_check_grader("x", "a", "b", "matches")
            .unwrap_err()
            .to_string(),
        "operation must be one of ('eq', 'ne', 'like', 'ilike'), got 'matches'"
    );

    let sim = text_similarity_grader("sim", "a", "b", "rouge_l", Some(0.5)).unwrap();
    assert_eq!(sim["evaluation_metric"], json!("rouge_l"));
    assert_eq!(sim["pass_threshold"], json!(0.5));

    let score = score_model_grader(
        "judge",
        "gpt-4o",
        &[json!({"role": "user", "content": "grade {{ item.answer }} / {{ item.category }}"})],
        None,
        None,
        None,
    );
    assert_eq!(score["range"], json!([0.0, 1.0]));
    assert_eq!(
        grader_item_fields(&score).into_iter().collect::<Vec<_>>(),
        vec!["answer".to_string(), "category".to_string()]
    );

    let py = python_grader("py", "def grade(sample, item):\n    return 1.0\n", None);
    assert!(validate_grader(&py).is_empty());

    let bad_combo = multi_grader(
        "combo",
        &json!({"acc": {"type": "string_check", "name": "acc"}}),
        "acc",
    );
    assert!(validate_grader(&bad_combo)
        .iter()
        .any(|problem| problem.contains("sub-grader 'acc'")));

    let unknown =
        validate_grader(&json!({"type": "mystery", "name": "n", "input": "{{ foo.bar }}"}));
    assert!(unknown.iter().any(|problem| problem.contains("not one of")));
    assert!(unknown
        .iter()
        .any(|problem| problem.contains("unknown namespace")));
}

#[test]
fn rft_validators_cross_check_required_ground_truth_fields() {
    let row = json!({"messages": [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "the prompt"}]});
    assert!(validate_rft_example(&row).is_empty());
    assert!(
        validate_rft_example(&json!({"messages": [{"role": "assistant", "content": "bad"}]}))
            .iter()
            .any(|problem| problem.contains("final message role must be 'user'"))
    );

    let grader =
        string_check_grader("acc", "{{ sample.output_text }}", "{{ item.answer }}", "eq").unwrap();
    let rows = vec![
        json!({"messages": [{"role": "user", "content": "Solve it."}], "answer": "42"}),
        json!({"messages": [{"role": "user", "content": "Solve it."}]}),
    ];
    let problems = validate_rft_dataset(&rows, Some(&grader), "training");
    assert!(problems
        .iter()
        .any(|problem| problem.contains("training[1]") && problem.contains("answer")));

    assert!(validate_rft_splits(
        &[json!({"messages": [{"role": "user", "content": "Solve it."}], "answer": "42"})],
        &[json!({"messages": [{"role": "user", "content": "Solve it."}], "answer": "7"})],
        Some(&grader),
    )
    .is_empty());
}

#[test]
fn rft_payload_builder_keeps_training_submission_offline() {
    let grader =
        string_check_grader("acc", "{{ sample.output_text }}", "{{ item.a }}", "eq").unwrap();
    let method =
        build_rft_method(&grader, Some(&json!({"reasoning_effort": "high"})), None).unwrap();
    assert_eq!(method["type"], json!("reinforcement"));
    assert_eq!(
        method["reinforcement"]["hyperparameters"]["reasoning_effort"],
        json!("high")
    );

    let job = build_rft_job(
        "o4-mini",
        "file-train",
        "file-val",
        &grader,
        Some(&json!({"reasoning_effort": "medium"})),
        Some(&json!({"type": "json_object"})),
        Some("rft1"),
        Some(42),
    )
    .unwrap();
    assert_eq!(job["model"], json!("o4-mini"));
    assert_eq!(job["validation_file"], json!("file-val"));
    assert_eq!(
        job["method"]["reinforcement"]["response_format"],
        json!({"type": "json_object"})
    );
    assert_eq!(
        job["method"]["reinforcement"]["grader"]["type"],
        json!("string_check")
    );
    assert_eq!(
        build_rft_job("o4-mini", "file-train", "", &grader, None, None, None, None)
            .unwrap_err()
            .to_string(),
        "RFT requires a validation_file in addition to training_file"
    );
}

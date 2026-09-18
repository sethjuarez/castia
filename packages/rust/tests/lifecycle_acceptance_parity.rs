use castia::lifecycle::{compare_runs, default_gate};
use serde_json::{json, Value};

const BASELINE_AGENT: &str = "39404774e110fcea7155f5e091de4ed90bbb55564efbb4d810e5477d22d8607a";
const CANDIDATE_AGENT: &str = "82fd115ba1551d85eeee23a53cd346cd80e7e11b6bef4cff3e7484394d187068";
const DATASET: &str = "0eb100552d27d9ecd3c6a2fa362c119d968b656d826e186b322da5f54a2c70d2";
const EXAMPLE_1: &str = "bdf737bc87a151c7436de89d535aee9b5dfbc210f912fd1053a7512aa9ba1f20";
const EXAMPLE_2: &str = "eb8419dfae8418e28509b282030b030f69e9c0b361a674e023ee59ee6ed04cf3";

fn run(agent_id: &str, quality: f64) -> Value {
    json!({
        "schema_version": 1,
        "kind": "run",
        "agent_id": agent_id,
        "dataset_id": DATASET,
        "evaluator": {"name": "rubric", "version": "v1", "configuration": {"judge": "judge-v1"}},
        "split": "heldout",
        "expected_ids": [EXAMPLE_1, EXAMPLE_2],
        "repeats": 1,
        "results": [
            {"example_id": EXAMPLE_1, "repetition": 0, "metrics": {"quality": quality, "latency_seconds": 0.1, "cost": 0.001}, "error": null},
            {"example_id": EXAMPLE_2, "repetition": 0, "metrics": {"quality": quality, "latency_seconds": 0.1, "cost": 0.001}, "error": null},
        ],
    })
}

#[test]
fn default_gate_matches_python_shape() {
    assert_eq!(
        default_gate(),
        json!({
            "minimum_quality": 0,
            "maximum_quality_drop": 0,
            "maximum_example_regressions": 0,
            "maximum_latency_seconds": null,
            "maximum_cost": null,
            "required_metrics": ["quality"],
            "require_heldout": true,
        })
    );
}

#[test]
fn gate_validation_preserves_numbers_and_rejects_bad_shapes() {
    let baseline = run(BASELINE_AGENT, 0.8);
    let mut candidate = run(CANDIDATE_AGENT, 0.9);
    for result in candidate["results"].as_array_mut().unwrap() {
        result["metrics"] = json!({"quality": 0.9});
    }
    let decision = compare_runs(&baseline, &candidate, &json!({"maximum_cost": 1})).unwrap();
    assert_eq!(decision["gate"]["maximum_cost"], json!(1));
    assert_eq!(decision["accepted"], false);

    for (gate, message) in [
        (json!({"unknown": true}), "unexpected gate field: unknown"),
        (
            json!({"maximum_quality_drop": -0.1}),
            "maximum_quality_drop must be >= 0",
        ),
        (json!({"maximum_cost": -1}), "maximum_cost must be >= 0"),
        (
            json!({"maximum_latency_seconds": -1}),
            "maximum_latency_seconds must be >= 0",
        ),
        (
            json!({"maximum_example_regressions": 1.5}),
            "maximum_example_regressions must be an integer >= 0",
        ),
        (
            json!({"required_metrics": "quality"}),
            "required_metrics must be an array",
        ),
        (
            json!({"required_metrics": [""]}),
            "required metric must be nonempty text",
        ),
        (
            json!({"require_heldout": "yes"}),
            "require_heldout must be a boolean",
        ),
    ] {
        assert_eq!(
            compare_runs(&baseline, &candidate, &gate)
                .unwrap_err()
                .to_string(),
            message
        );
    }
}

#[test]
fn comparison_accepts_improvement_and_enforces_cost_latency_metrics() {
    let baseline = run(BASELINE_AGENT, 0.8);
    let candidate = run(CANDIDATE_AGENT, 0.9);
    let accepted = compare_runs(&baseline, &candidate, &json!({})).unwrap();
    assert_eq!(accepted["accepted"], true);
    assert_eq!(accepted["reasons"], json!([]));
    assert_eq!(accepted["aggregates"]["candidate"]["quality"], json!(0.9));
    assert_eq!(
        accepted["aggregates"]["candidate"]
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect::<std::collections::HashSet<_>>(),
        ["quality", "latency_seconds", "cost"]
            .into_iter()
            .map(str::to_string)
            .collect()
    );

    assert_eq!(
        compare_runs(&baseline, &candidate, &json!({"maximum_cost": 0.0001})).unwrap()["accepted"],
        false
    );
    assert_eq!(
        compare_runs(
            &baseline,
            &candidate,
            &json!({"maximum_latency_seconds": 0.01})
        )
        .unwrap()["accepted"],
        false
    );

    let mut missing = candidate.clone();
    for result in missing["results"].as_array_mut().unwrap() {
        result["metrics"] = json!({"quality": 0.9});
    }
    assert_eq!(
        compare_runs(&baseline, &missing, &json!({})).unwrap()["accepted"],
        true
    );
    assert_eq!(
        compare_runs(&baseline, &missing, &json!({"maximum_cost": 1})).unwrap()["accepted"],
        false
    );
    assert_eq!(
        compare_runs(
            &baseline,
            &missing,
            &json!({"required_metrics": ["safety"]})
        )
        .unwrap()["accepted"],
        false
    );
}

#[test]
fn comparison_fails_closed_for_incomparable_or_incomplete_runs() {
    for change in [
        "dataset",
        "evaluator",
        "split",
        "expected",
        "repeats",
        "missing",
        "failed",
        "quality",
    ] {
        let baseline = run(BASELINE_AGENT, 0.8);
        let mut candidate = baseline.clone();
        match change {
            "dataset" => {
                candidate["dataset_id"] =
                    json!("0000000000000000000000000000000000000000000000000000000000000000")
            }
            "evaluator" => candidate["evaluator"]["version"] = json!("v2"),
            "split" => candidate["split"] = json!("train"),
            "expected" => {
                candidate["expected_ids"] = json!([EXAMPLE_1]);
                candidate["results"] = json!([]);
            }
            "repeats" => candidate["repeats"] = json!(2),
            "missing" => {
                candidate["results"].as_array_mut().unwrap().pop();
            }
            "failed" => {
                for result in candidate["results"].as_array_mut().unwrap() {
                    result["metrics"] = json!({});
                    result["error"] = json!("callback_error");
                }
            }
            "quality" => {
                for result in candidate["results"].as_array_mut().unwrap() {
                    result["metrics"] = json!({"latency_seconds": 0.1});
                }
            }
            _ => unreachable!(),
        }
        assert_eq!(
            compare_runs(&baseline, &candidate, &json!({})).unwrap()["accepted"],
            false,
            "{change}"
        );
    }
}

#[test]
fn per_example_regression_cannot_hide_behind_aggregate() {
    let baseline = run(BASELINE_AGENT, 0.5);
    let mut candidate = baseline.clone();
    candidate["results"][0]["metrics"] = json!({"quality": 0.4});
    candidate["results"][1]["metrics"] = json!({"quality": 0.9});

    let rejected = compare_runs(&baseline, &candidate, &json!({})).unwrap();
    assert_eq!(rejected["accepted"], false);
    assert_eq!(rejected["regressions"], json!([EXAMPLE_1]));
    assert!(
        rejected["aggregates"]["candidate"]["quality"]
            .as_f64()
            .unwrap()
            > 0.5
    );

    assert_eq!(
        compare_runs(
            &baseline,
            &candidate,
            &json!({"maximum_example_regressions": 1})
        )
        .unwrap()["accepted"],
        true
    );
}

#[test]
fn train_comparisons_require_explicit_gate_override() {
    let mut baseline = run(BASELINE_AGENT, 0.8);
    baseline["split"] = json!("train");
    let candidate = baseline.clone();
    assert_eq!(
        compare_runs(&baseline, &candidate, &json!({})).unwrap()["accepted"],
        false
    );
    assert_eq!(
        compare_runs(&baseline, &candidate, &json!({"require_heldout": false})).unwrap()
            ["accepted"],
        true
    );
}

#[test]
fn finite_extremes_do_not_leak_nonfinite_values() {
    let baseline = run(BASELINE_AGENT, -1.7e308);
    let candidate = run(CANDIDATE_AGENT, 1.7e308);
    let decision = compare_runs(&baseline, &candidate, &json!({})).unwrap();
    assert_eq!(decision["accepted"], false);
    assert!(decision["reasons"]
        .as_array()
        .unwrap()
        .contains(&json!("nonfinite comparison delta")));
    assert!(!serde_json::to_string(&decision)
        .unwrap()
        .contains("Infinity"));
}

use castia::finetuning::{
    approvals, delete_guard, estimate_training, min_rows_problem, normalize_batch_size,
    python_grader_denied_imports, rft_cost_table, split_job_timing, training_started_at,
    validate_grader, APPROVAL_STAGES, DEFAULT_SECONDS_PER_STEP, DENIED_GRADER_MODULES,
    MIN_RFT_TRAINING_ROWS,
};
use serde_json::{json, Value};

fn fixture(name: &str) -> Value {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../spec/conformance/finetuning")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn cases<'a>(spec: &'a Value, key: &str) -> &'a Vec<Value> {
    spec[key].as_array().unwrap()
}

#[test]
fn constants_match_spec() {
    let spec = fixture("preflight.json");
    assert_eq!(rft_cost_table(), spec["cost_table"]);
    assert_eq!(
        json!(DEFAULT_SECONDS_PER_STEP),
        spec["default_seconds_per_step"]
    );
    assert_eq!(json!(APPROVAL_STAGES), spec["approval_stages"]);
}

#[test]
fn batch_size_cases() {
    let spec = fixture("preflight.json");
    for case in cases(&spec, "batch_size") {
        let result = normalize_batch_size(&case["input"]);
        match case.get("error").and_then(Value::as_str) {
            Some(error) => assert!(result.unwrap_err().0.contains(error), "{case}"),
            None => assert_eq!(json!(result.unwrap()), case["expected"], "{case}"),
        }
    }
}

#[test]
fn estimate_cases() {
    let spec = fixture("preflight.json");
    for case in cases(&spec, "estimate") {
        let input = &case["input"];
        let batch = (!input["batch_size"].is_null()).then_some(&input["batch_size"]);
        let result = estimate_training(
            input["rows"].as_i64().unwrap(),
            input["n_epochs"].as_i64(),
            batch,
            input["seconds_per_step"].as_f64(),
            input["hourly_usd"].as_f64(),
            "developer",
        );
        match case.get("error").and_then(Value::as_str) {
            Some(error) => assert!(result.unwrap_err().0.contains(error), "{case}"),
            None => {
                let got = result.unwrap();
                let expected = &case["expected"];
                for key in [
                    "rows",
                    "n_epochs",
                    "batch_size",
                    "steps",
                    "bound",
                    "assumptions",
                ] {
                    assert_eq!(got[key], expected[key], "{key} in {case}");
                }
                for key in ["hours", "cost_usd"] {
                    let (a, b) = (got[key].as_f64().unwrap(), expected[key].as_f64().unwrap());
                    assert!((a - b).abs() < 1e-9, "{key} in {case}");
                }
            }
        }
    }
}

#[test]
fn timing_and_started_at_cases() {
    let spec = fixture("preflight.json");
    for case in cases(&spec, "training_started_at") {
        let events = case["input"].as_array().unwrap();
        assert_eq!(
            json!(training_started_at(events)),
            case["expected"],
            "{case}"
        );
    }
    for case in cases(&spec, "timing") {
        let input = &case["input"];
        let got = split_job_timing(
            input["created_at"].as_i64().unwrap(),
            input["started_at"].as_i64(),
            input["finished_at"].as_i64(),
            input["now"].as_i64().unwrap(),
        );
        assert_eq!(got, case["expected"], "{case}");
    }
}

#[test]
fn delete_and_approval_cases() {
    let spec = fixture("preflight.json");
    for case in cases(&spec, "delete") {
        let input = &case["input"];
        let got = delete_guard(
            input["fine_tuned_model"].as_str(),
            input["deployments"].as_array().unwrap(),
            input["confirm_model"].as_str(),
            input["yes"].as_bool().unwrap(),
        );
        assert_eq!(got["allowed"], case["expected"]["allowed"], "{case}");
        assert_eq!(
            got["deployments"], case["expected"]["deployments"],
            "{case}"
        );
    }
    for case in cases(&spec, "approvals") {
        let input = &case["input"];
        let got = approvals(
            input["train"].as_bool().unwrap(),
            input["evaluate"].as_bool().unwrap(),
            input["promote"].as_bool().unwrap(),
        );
        assert_eq!(got, case["expected"]);
    }
}

#[test]
fn python_grader_import_cases() {
    let spec = fixture("python_grader_rules.json");
    let denied = spec["denied_modules"].as_object().unwrap();
    assert_eq!(denied.len(), DENIED_GRADER_MODULES.len());
    for (module, evidence, fix) in DENIED_GRADER_MODULES {
        assert_eq!(denied[*module]["evidence"], json!(evidence));
        assert_eq!(denied[*module]["fix"], json!(fix));
    }
    assert_eq!(
        json!(MIN_RFT_TRAINING_ROWS),
        spec["min_training_rows"]["rft"]
    );
    for case in cases(&spec, "cases") {
        let source = case["source"].as_str().unwrap();
        assert_eq!(
            json!(python_grader_denied_imports(source)),
            case["denied"],
            "{case}"
        );
    }
    let grader = json!({
        "type": "python", "name": "g", "image_tag": "2025-05-08",
        "source": "import urllib.parse\n\ndef grade(sample, item):\n    return 1.0\n",
    });
    assert!(validate_grader(&grader)
        .iter()
        .any(|p| p.contains("urllib.parse") && p.contains("str methods or re")));
    assert!(min_rows_problem("rft", 9).is_some());
    assert!(min_rows_problem("rft", 10).is_none());
}

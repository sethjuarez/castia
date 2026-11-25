use castia::finetuning::{
    calculate, grade_local, probe_graders, probe_verdict, render_template, reward_from_events,
    sample_for, DEFAULT_TOLERANCE, FAIL_CEILING, PASS_FLOOR, PROBE_ROWS,
};
use serde_json::{json, Value};
use std::collections::HashMap;

fn fixture() -> Value {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../spec/conformance/finetuning/probe.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn cases<'a>(spec: &'a Value, key: &str) -> &'a Vec<Value> {
    spec[key].as_array().unwrap()
}

#[test]
fn constants_match_spec() {
    let spec = fixture();
    let t = &spec["thresholds"];
    assert_eq!(json!(PROBE_ROWS), t["probe_rows"]);
    assert_eq!(json!(PASS_FLOOR), t["pass_floor"]);
    assert_eq!(json!(FAIL_CEILING), t["fail_ceiling"]);
    assert_eq!(json!(DEFAULT_TOLERANCE), t["default_tolerance"]);
    assert_eq!(probe_graders(), spec["probe_graders"]);
}

#[test]
fn render_and_grade_cases() {
    let spec = fixture();
    for case in cases(&spec, "render") {
        let got = render_template(
            case["template"].as_str().unwrap(),
            &case["sample"],
            &case["item"],
        );
        assert_eq!(json!(got), case["expected"], "{case}");
    }
    for case in cases(&spec, "grade") {
        let got = grade_local(&case["grader"], &sample_for(&case["output"]), &case["item"]);
        assert_eq!(json!(got.unwrap()), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn calculate_supports_python_arithmetic() {
    let scores = HashMap::from([("a".to_string(), 0.5), ("b".to_string(), 1.0)]);
    let eval = |text| calculate(text, &scores).unwrap();
    assert_eq!(eval("0.5 * a + 0.5 * b"), 0.75);
    assert_eq!(eval("-(a - b) ** 2"), -0.25);
    assert_eq!(eval("min(a, b) + max(a, 0, b) + abs(-a)"), 2.0);
    assert!(calculate("a + c", &scores).is_err());
    assert!(calculate("__import__('os')", &scores).is_err());
}

#[test]
fn reward_and_verdict_cases() {
    let spec = fixture();
    for case in cases(&spec, "reward_from_events") {
        let got = reward_from_events(case["input"].as_array().unwrap());
        assert_eq!(json!(got), case["expected"], "{case}");
    }
    for case in cases(&spec, "verdict") {
        let input = &case["input"];
        let got = probe_verdict(
            &input["rewards"],
            input["offline_score"].as_f64(),
            DEFAULT_TOLERANCE,
        );
        assert_eq!(got["status"], case["expected"], "{case}");
    }
}

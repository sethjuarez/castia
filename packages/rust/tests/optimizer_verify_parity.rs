use castia::optimizing::{
    optimize_heldout_overlap, optimize_metric_values, optimize_seed_from_version,
    optimize_termination_label, optimize_training_leaks, optimize_verify_verdict,
    OPTIMIZE_LEAK_MIN_WORDS, OPTIMIZE_LEAK_SHINGLE_WORDS, OPTIMIZE_STALL_MARKERS,
    OPTIMIZE_STOPPED_EARLY_LABEL,
};
use serde_json::{json, Value};

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../spec/conformance/optimization/verify.json"
    ))
    .expect("fixture parses")
}

fn cases<'a>(f: &'a Value, key: &str) -> &'a Vec<Value> {
    f[key].as_array().unwrap()
}

#[test]
fn constants_match_spec() {
    let c = &fixture()["constants"];
    assert_eq!(c["leak_shingle_words"], OPTIMIZE_LEAK_SHINGLE_WORDS);
    assert_eq!(c["leak_min_words"], OPTIMIZE_LEAK_MIN_WORDS);
    assert_eq!(c["stall_markers"], json!(OPTIMIZE_STALL_MARKERS));
    assert_eq!(c["stopped_early_label"], OPTIMIZE_STOPPED_EARLY_LABEL);
}

#[test]
fn training_leaks_match_spec() {
    let f = fixture();
    for case in cases(&f, "training_leaks") {
        let leaks = optimize_training_leaks(
            case["candidate"].as_str().unwrap(),
            case["rows"].as_array().unwrap(),
            case["baseline"].as_str().unwrap(),
        );
        let got: Vec<Value> = leaks
            .iter()
            .map(|l| json!({"row": l.row, "field": l.field, "match": l.matched}))
            .collect();
        assert_eq!(Value::Array(got), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn heldout_overlap_matches_spec() {
    let f = fixture();
    for case in cases(&f, "heldout_overlap") {
        let got = optimize_heldout_overlap(
            case["training"].as_array().unwrap(),
            case["heldout"].as_array().unwrap(),
            "query",
        );
        assert_eq!(json!(got), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn metric_values_match_spec() {
    let f = fixture();
    for case in cases(&f, "metric_values") {
        let got = optimize_metric_values(
            case["rows"].as_array().unwrap(),
            case["metric"].as_str().unwrap(),
        );
        assert_eq!(json!(got), case["expected"], "{}", case["name"]);
    }
}

fn floats(v: &Value) -> Vec<f64> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_f64().unwrap())
        .collect()
}

#[test]
fn verdict_matches_spec() {
    let f = fixture();
    for case in cases(&f, "verdict") {
        let name = case["name"].as_str().unwrap();
        let got = optimize_verify_verdict(
            &floats(&case["baseline"]),
            &floats(&case["candidate"]),
            case["min_delta"].as_f64().unwrap(),
            case["leaks"].as_u64().unwrap() as usize,
            case["overlap"].as_u64().unwrap() as usize,
        );
        let e = &case["expected"];
        assert_eq!(got.status, e["status"], "{name}");
        assert_eq!(got.baseline_mean, e["baseline_mean"].as_f64(), "{name}");
        assert_eq!(got.candidate_mean, e["candidate_mean"].as_f64(), "{name}");
        assert_eq!(got.delta, e["delta"].as_f64(), "{name}");
        assert_eq!(
            got.reasons.len() as u64,
            e["reasons"].as_u64().unwrap(),
            "{name}"
        );
    }
}

#[test]
fn termination_label_matches_spec() {
    let f = fixture();
    for case in cases(&f, "termination_label") {
        assert_eq!(
            optimize_termination_label(&case["status"]),
            case["expected"].as_str(),
            "{}",
            case["name"]
        );
    }
}

#[test]
fn seed_from_version_matches_spec() {
    let f = fixture();
    for case in cases(&f, "seed_from_version") {
        let name = case["name"].as_str().unwrap();
        let got = optimize_seed_from_version(
            &case["request"],
            &case["version"],
            case["agent_version"].as_str().unwrap(),
        );
        if let Some(error) = case["error"].as_str() {
            assert!(got.unwrap_err().contains(error), "{name}");
            continue;
        }
        let (request, seeded) = got.expect(name);
        assert_eq!(request, case["expected_request"], "{name}");
        assert_eq!(json!(seeded), case["expected_seeded"], "{name}");
    }
}

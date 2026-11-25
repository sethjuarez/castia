use castia::optimizing::{
    estimate_optimize, optimize_evaluator_level_problems, optimize_item_field_problems,
    optimize_item_references, OPTIMIZE_ASSUMED_CANDIDATES, OPTIMIZE_DEFAULT_ROW_SECONDS,
    OPTIMIZE_KNOWN_DROPPED_FIELDS, OPTIMIZE_PASSTHROUGH_BY_TYPE, OPTIMIZE_PASSTHROUGH_FIELDS,
    OPTIMIZE_PILOT_MAX_CANDIDATES, OPTIMIZE_PILOT_MAX_ROWS, OPTIMIZE_SERVICE_CAP_HOURS,
    OPTIMIZE_TURN_LEVEL_EVALUATOR_TYPES,
};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(include_str!(
        "../../../spec/conformance/optimization/preflight.json"
    ))
    .expect("fixture parses")
}

fn strs(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

fn assert_prefixes(problems: &[String], expected: &Value, name: &str) {
    let expected = strs(expected);
    assert_eq!(problems.len(), expected.len(), "{name}: {problems:?}");
    for (p, e) in problems.iter().zip(expected.iter()) {
        assert!(p.starts_with(e.as_str()), "{name}: {p:?} !~ {e:?}");
    }
}

#[test]
fn constants_match_spec() {
    let c = &fixture()["constants"];
    assert_eq!(strs(&c["passthrough_fields"]), OPTIMIZE_PASSTHROUGH_FIELDS);
    let by_type = c["passthrough_by_type"].as_object().unwrap();
    assert_eq!(by_type.len(), OPTIMIZE_PASSTHROUGH_BY_TYPE.len());
    for (kind, fields) in OPTIMIZE_PASSTHROUGH_BY_TYPE {
        assert_eq!(strs(&by_type[*kind]), *fields);
    }
    assert_eq!(
        strs(&c["known_dropped_fields"]),
        OPTIMIZE_KNOWN_DROPPED_FIELDS
    );
    assert_eq!(
        strs(&c["turn_level_evaluator_types"]),
        OPTIMIZE_TURN_LEVEL_EVALUATOR_TYPES
    );
    assert_eq!(c["pilot_max_rows"], OPTIMIZE_PILOT_MAX_ROWS);
    assert_eq!(c["pilot_max_candidates"], OPTIMIZE_PILOT_MAX_CANDIDATES);
    assert_eq!(c["service_cap_hours"], OPTIMIZE_SERVICE_CAP_HOURS);
    assert_eq!(c["default_row_seconds"], OPTIMIZE_DEFAULT_ROW_SECONDS);
    assert_eq!(c["assumed_candidates"], OPTIMIZE_ASSUMED_CANDIDATES);
}

#[test]
fn item_references_match_spec() {
    for case in fixture()["item_references"].as_array().unwrap() {
        assert_eq!(
            optimize_item_references(&case["value"]),
            strs(&case["expected"])
        );
    }
}

#[test]
fn evaluator_level_matches_spec() {
    for case in fixture()["evaluator_level"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let problems = optimize_evaluator_level_problems(
            case["evaluators"].as_array().unwrap(),
            &case["options"],
            case.get("definitions"),
        );
        assert_prefixes(&problems, &case["expected"], name);
    }
}

#[test]
fn item_fields_match_spec() {
    for case in fixture()["item_fields"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let passthrough: Option<Vec<String>> = case.get("passthrough").map(strs);
        let passthrough: Option<Vec<&str>> = passthrough
            .as_ref()
            .map(|v| v.iter().map(String::as_str).collect());
        let problems = optimize_item_field_problems(
            case["evaluators"].as_array().unwrap(),
            case["rows"].as_array().map(Vec::as_slice),
            passthrough.as_deref(),
            case.get("definitions"),
        );
        assert_prefixes(&problems, &case["expected"], name);
    }
}

#[test]
fn estimate_matches_spec() {
    for case in fixture()["estimate"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let i = &case["input"];
        let est = estimate_optimize(
            i["rows"].as_u64(),
            i["max_candidates"].as_u64(),
            i["row_seconds"]
                .as_f64()
                .unwrap_or(OPTIMIZE_DEFAULT_ROW_SECONDS),
            i["concurrency"].as_u64(),
            i["hourly_usd"].as_f64(),
        );
        let e = &case["expected"];
        assert_eq!(est.hours, e["hours"].as_f64(), "{name}");
        assert_eq!(
            est.needs_approval,
            e["needs_approval"].as_bool().unwrap(),
            "{name}"
        );
        assert_eq!(
            est.candidates_assumed,
            e["candidates_assumed"].as_bool().unwrap(),
            "{name}"
        );
        if let Some(cost) = e.get("cost_usd") {
            assert_eq!(est.cost_usd, cost.as_f64(), "{name}");
        }
    }
}

//! Conformance against `spec/conformance/observe/genai_semconv.json`, the same
//! fixture `packages/python/tests/test_observe_genai.py` reads.

use castia::evaluation::tool_errors::tool_error_gate;
use castia::observe::genai::{
    human_evaluation_attributes, parse_traceparent, request_parameter_attributes,
    server_tool_calls, server_tool_span, system_instructions, text_messages, HumanEvaluation,
    GENAI_PROVIDER, GENAI_SEMCONV_SCHEMA_URL, GENAI_SEMCONV_VERSION,
};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn fixture() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("spec")
        .join("conformance")
        .join("observe")
        .join("genai_semconv.json");
    serde_json::from_str(&fs::read_to_string(path).expect("fixture is readable"))
        .expect("fixture is JSON")
}

fn cases<'a>(fixture: &'a Value, key: &str) -> &'a Vec<Value> {
    fixture[key]
        .as_array()
        .expect("fixture section is an array")
}

#[test]
fn semconv_version_is_pinned() {
    let fixture = fixture();
    assert_eq!(fixture["semconv_version"], GENAI_SEMCONV_VERSION);
    assert_eq!(fixture["schema_url"], GENAI_SEMCONV_SCHEMA_URL);
    assert_eq!(fixture["provider"], GENAI_PROVIDER);
}

#[test]
fn content_shapes_match_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "text_messages") {
        let actual = text_messages(
            case["role"].as_str().unwrap(),
            case["text"].as_str().unwrap(),
            case["finish_reason"].as_str(),
        );
        assert_eq!(actual, case["expected"]);
    }
    for case in cases(&fixture, "system_instructions") {
        assert_eq!(
            system_instructions(case["text"].as_str().unwrap()),
            case["expected"]
        );
    }
}

#[test]
fn request_parameters_match_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "request_parameters") {
        let actual = request_parameter_attributes(case["params"].as_object().unwrap());
        assert_eq!(Value::Object(actual), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn server_tools_match_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "server_tools") {
        let calls = server_tool_calls(case["output"].as_array().unwrap());
        assert_eq!(json!(calls), case["expected_calls"], "{}", case["name"]);
        let spans: Vec<Value> = calls
            .iter()
            .map(|call| {
                server_tool_span(
                    call,
                    None,
                    case["iteration"].as_i64(),
                    case["include_content"].as_bool().unwrap(),
                )
            })
            .collect();
        assert_eq!(json!(spans), case["expected_spans"], "{}", case["name"]);
    }
}

#[test]
fn tool_error_gate_matches_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "tool_error_gate") {
        let gate = tool_error_gate(
            case["records"].as_array().unwrap(),
            case["max_error_rate"].as_f64().unwrap(),
            case["min_calls"].as_u64().unwrap(),
        );
        assert_eq!(gate.to_value(), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn human_evaluations_match_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "human_evaluation") {
        let input = &case["input"];
        let text = |key: &str| input[key].as_str().map(str::to_string);
        let evaluation = HumanEvaluation {
            name: text("name").unwrap_or_default(),
            score: input["score"].as_f64(),
            label: text("label"),
            explanation: text("explanation"),
            response_id: text("response_id"),
            agent_id: text("agent_id"),
            reviewer: text("reviewer"),
        };
        let actual = human_evaluation_attributes(&evaluation);
        if case.get("error").is_some() {
            assert!(actual.is_err(), "{}", case["name"]);
        } else {
            assert_eq!(
                Value::Object(actual.unwrap()),
                case["expected"],
                "{}",
                case["name"]
            );
        }
    }
}

#[test]
fn traceparents_match_fixture() {
    let fixture = fixture();
    for case in cases(&fixture, "traceparent") {
        let actual = parse_traceparent(case["value"].as_str().unwrap())
            .map(|(trace_id, span_id, sampled)| json!([trace_id, span_id, sampled]))
            .unwrap_or(Value::Null);
        assert_eq!(actual, case["expected"], "{}", case["value"]);
    }
}

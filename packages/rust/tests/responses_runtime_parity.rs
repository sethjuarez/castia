use castia::model::ResponsesRuntime;
use castia::protocols::{responses_body, responses_input, CastiaResponsesRuntime};
use serde_json::json;

#[test]
fn responses_input_matches_python_normalizer_cases() {
    let cases = [
        (json!("hello world"), "hello world"),
        (json!(""), ""),
        (json!(null), ""),
        (json!({"role": "user"}), ""),
        (json!(42), ""),
        (json!([{"role": "user", "content": "say hi"}]), "say hi"),
        (
            json!([{"role": "user", "content": [
                {"type": "input_text", "text": "part one "},
                {"type": "input_text", "text": "part two"}
            ]}]),
            "part one part two",
        ),
        (
            json!([
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second"}
            ]),
            "second",
        ),
        (json!([{"content": "no role here"}]), "no role here"),
        (
            json!([
                {"role": "user", "content": "the question"},
                {"role": "assistant", "content": ""}
            ]),
            "the question",
        ),
        (json!(["first", "second"]), "second"),
        (json!([]), ""),
    ];

    for (input, expected) in cases {
        assert_eq!(responses_input(&input), expected);
        assert_eq!(CastiaResponsesRuntime.input_text(&input), expected);
    }
}

#[test]
fn responses_body_matches_python_response_envelope_shape() {
    let mut body = responses_body("hello");

    assert_eq!(body["object"], "response");
    assert_eq!(body["status"], "completed");
    assert_eq!(body["output_text"], "hello");
    let id = body["id"].as_str().expect("id is string");
    assert!(
        regex::Regex::new(r"^resp_[0-9a-f]{16}00[0-9a-f]{32}$")
            .unwrap()
            .is_match(id),
        "{id} is not in the Foundry platform id format"
    );
    body.as_object_mut()
        .expect("body is an object")
        .remove("id");
    assert_eq!(
        body,
        json!({
            "object": "response",
            "status": "completed",
            "output_text": "hello",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hello"}]
                }
            ]
        })
    );
}

#[test]
fn platform_id_colocates_items_on_their_response_partition() {
    let response = castia::protocols::platform_id("resp", None);
    let item = castia::protocols::platform_id("msg", Some(&response));
    assert!(item.starts_with("msg_"));
    assert_eq!(item[4..22], response[5..23]);
    assert_eq!(item.len(), "msg_".len() + 50);

    let fresh = castia::protocols::platform_id("msg", Some("resp_not-a-platform-id"));
    assert!(regex::Regex::new(r"^msg_[0-9a-f]{16}00[0-9a-f]{32}$")
        .unwrap()
        .is_match(&fresh));
}

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
    assert!(body["id"]
        .as_str()
        .expect("id is string")
        .starts_with("resp_"));
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

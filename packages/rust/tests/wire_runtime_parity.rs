use castia::model::{ChatRuntime, InvocationsRuntime, ResponsesRuntime};
use castia::protocols::{
    chat_body, invocations_body, invocations_input, last_user_text, responses_body,
    CastiaChatRuntime, CastiaInvocationsRuntime, CastiaResponsesRuntime,
};
use serde_json::{json, Value};

#[test]
fn chat_runtime_matches_python_helpers() {
    let messages = json!([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"}
    ]);
    assert_eq!(last_user_text(&messages), "second");
    assert_eq!(CastiaChatRuntime.last_user_text(&messages), "second");
    assert_eq!(
        CastiaChatRuntime.last_user_text(&json!([{"role": "assistant", "content": "reply"}])),
        ""
    );

    let body = normalized_id(chat_body("hello"));
    assert_eq!(
        body,
        json!({
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop"
            }]
        })
    );
}

#[test]
fn invocations_runtime_matches_python_helpers() {
    let cases = [
        (json!({"message": "message", "input": "input"}), "message"),
        (json!({"input": "input"}), "input"),
        (json!("hello"), "hello"),
        (json!(null), ""),
        (json!({"message": 42, "input": true}), ""),
    ];

    for (input, expected) in cases {
        assert_eq!(invocations_input(&input), expected);
        assert_eq!(CastiaInvocationsRuntime.input_text(&input), expected);
    }

    assert_eq!(invocations_body("hello"), json!({"output": "hello"}));
    assert_eq!(
        CastiaInvocationsRuntime.body(&"hello".to_string()),
        json!({"output": "hello"})
    );
}

#[test]
fn responses_runtime_body_is_available_through_trait() {
    let body = normalized_id(CastiaResponsesRuntime.output_body(&"hello".to_string()));
    assert_eq!(body, normalized_id(responses_body("hello")));
}

fn normalized_id(mut value: Value) -> Value {
    value
        .as_object_mut()
        .expect("wire body is an object")
        .remove("id");
    value
}

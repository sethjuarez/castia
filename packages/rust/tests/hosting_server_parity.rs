use castia::hosting::{readiness_body, sse_event, wire_endpoints};
use serde_json::json;

#[test]
fn readiness_matches_python_server() {
    assert_eq!(readiness_body(), "{\"status\":\"ok\"}");
}

#[test]
fn sse_event_matches_python_minified_shape() {
    assert_eq!(
        sse_event(
            "response.output_text.delta",
            &json!({"type": "response.output_text.delta", "delta": "hi"})
        ),
        "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"hi\"}\n\n"
    );
}

#[test]
fn sse_event_matches_python_completion_order_and_ascii_escaping() {
    assert_eq!(
        sse_event(
            "response.completed",
            &json!({
                "id": "resp_1",
                "object": "response",
                "status": "completed",
                "output_text": "café 🚀",
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "café 🚀"}],
                }],
            })
        ),
        "event: response.completed\ndata: {\"id\":\"resp_1\",\"object\":\"response\",\"status\":\"completed\",\"output_text\":\"caf\\u00e9 \\ud83d\\ude80\",\"output\":[{\"type\":\"message\",\"role\":\"assistant\",\"content\":[{\"type\":\"output_text\",\"text\":\"caf\\u00e9 \\ud83d\\ude80\"}]}]}\n\n"
    );
}

#[test]
fn wire_endpoints_follow_python_registration_order() {
    assert_eq!(
        wire_endpoints(&[
            "invocations".to_string(),
            "responses".to_string(),
            "chat".to_string(),
            "responses_stream".to_string(),
            "unknown".to_string(),
        ]),
        json!([
            {"protocol": "responses", "method": "POST", "path": "/responses", "streaming": true},
            {"protocol": "chat", "method": "POST", "path": "/chat/completions"},
            {"protocol": "invocations", "method": "POST", "path": "/invocations"},
        ])
    );
    assert_eq!(wire_endpoints(&[]), json!([]));
    assert_eq!(wire_endpoints(&["responses_stream".to_string()]), json!([]));
}

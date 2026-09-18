use castia::runtime::{
    activity_dispatch_plan, activity_result_text, invoke_dispatch_plan, invoke_result_body,
    return_body, stream_chunks, wire_dispatch_plan,
};
use serde_json::json;

#[test]
fn activity_dispatch_skips_empty_text_only_handlers() {
    assert_eq!(
        activity_dispatch_plan(&json!([{"name": "text"}]), ""),
        json!({"skip": true, "injections": []})
    );
}

#[test]
fn activity_dispatch_sources_match_python() {
    assert_eq!(
        activity_dispatch_plan(
            &json!([
                {"name": "text"},
                {"name": "msg", "annotation": "Message"},
                {"name": "activity", "annotation": "Activity"},
                {"name": "model", "annotation": "Activity", "defaultKind": "Depends"},
            ]),
            "hello"
        ),
        json!({
            "skip": false,
            "injections": [
                {"name": "text", "source": "text", "value": "hello"},
                {"name": "msg", "source": "message"},
                {"name": "activity", "source": "activity"},
                {"name": "model", "source": "dependency"},
            ],
        })
    );
    assert_eq!(
        activity_dispatch_plan(&json!([{"name": "activity", "annotation": "Activity"}]), ""),
        json!({"skip": false, "injections": [{"name": "activity", "source": "activity"}]})
    );
}

#[test]
fn invoke_bare_parameter_receives_value() {
    assert_eq!(
        invoke_dispatch_plan(
            &json!([
                {"name": "value"},
                {"name": "msg", "annotation": "Message"},
            ]),
            &json!({"action": "approve"})
        ),
        json!({
            "injections": [
                {"name": "value", "source": "invoke_value", "value": {"action": "approve"}},
                {"name": "msg", "source": "message"},
            ],
        })
    );
}

#[test]
fn wire_dispatch_rejects_activity_only_annotations() {
    assert_eq!(
        wire_dispatch_plan(
            "reply",
            &json!([{"name": "activity", "annotation": "Activity"}]),
            "hello",
            false
        )
        .unwrap_err()
        .to_string(),
        "handler 'reply' asks for Activity, which only exists on the Activity Protocol; a handler served over @app.responses(), @app.chat(), or @app.invocations() must take the input text and Depends(...) only."
    );
    assert_eq!(
        wire_dispatch_plan(
            "reply_stream",
            &json!([{"name": "msg", "annotation": "Message"}]),
            "hello",
            true
        )
        .unwrap_err()
        .to_string(),
        "handler 'reply_stream' asks for Message, which only exists on the Activity Protocol; a streaming wire handler must take the input text and Depends(...) only."
    );
    assert_eq!(
        wire_dispatch_plan(
            "reply_stream",
            &json!([{"name": "activity", "annotation": "Activity"}]),
            "hello",
            true
        )
        .unwrap_err()
        .to_string(),
        "handler 'reply_stream' asks for Activity, which only exists on the Activity Protocol; a streaming wire handler must take the input text and Depends(...) only."
    );
}

#[test]
fn result_shapes_match_python_dispatch() {
    assert_eq!(activity_result_text(&json!("")), None);
    assert_eq!(activity_result_text(&json!("ok")).as_deref(), Some("ok"));
    assert_eq!(activity_result_text(&json!(42)), None);
    assert_eq!(
        invoke_result_body(&json!({"ok": true})),
        json!({"ok": true})
    );
    assert_eq!(invoke_result_body(&json!("ignored")), json!(null));
    assert_eq!(invoke_result_body(&json!([])), json!(null));
    assert_eq!(return_body(&json!(42)), "");
    assert_eq!(return_body(&json!("body")), "body");
    assert_eq!(stream_chunks(&json!(["a", "", 1, "b"])), vec!["a", "b"]);
    assert_eq!(stream_chunks(&json!("done")), vec!["done"]);
    assert!(stream_chunks(&json!("")).is_empty());
    assert!(stream_chunks(&json!(42)).is_empty());
}

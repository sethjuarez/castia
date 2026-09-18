use castia::runtime::{include_plan, registered_protocols, responses_only_projection};
use serde_json::json;

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| value.to_string()).collect()
}

#[test]
fn registered_protocols_match_python_ordering() {
    assert_eq!(
        registered_protocols(1, &[], &strings(&["chat", "responses"])),
        strings(&["activity", "responses", "chat"])
    );
    assert_eq!(
        registered_protocols(0, &strings(&["message/submitAction"]), &[]),
        strings(&["activity"])
    );
    assert_eq!(
        registered_protocols(
            0,
            &[],
            &strings(&["chat", "invocations", "responses_stream", "responses"])
        ),
        strings(&["responses", "invocations", "chat"])
    );
}

#[test]
fn include_plan_merges_or_rejects_duplicate_wire_and_invokes() {
    assert_eq!(
        include_plan(
            &strings(&["responses"]),
            &strings(&["chat"]),
            &strings(&["a"]),
            &strings(&["b"])
        )
        .unwrap(),
        json!({"ok": true, "wire": ["responses", "chat"], "invokes": ["a", "b"]})
    );
    assert!(
        include_plan(&strings(&["responses"]), &strings(&["responses"]), &[], &[])
            .unwrap_err()
            .to_string()
            .contains("protocol 'responses' already has a handler")
    );
    assert!(include_plan(
        &[],
        &[],
        &strings(&["message/submitAction"]),
        &strings(&["message/submitAction"])
    )
    .unwrap_err()
    .to_string()
    .contains("invoke 'message/submitAction' already has a handler"));
}

#[test]
fn responses_only_projection_requires_handler_and_deduplicates_tools() {
    assert_eq!(
        responses_only_projection("hal", true, &strings(&["a", "a", "b"])).unwrap(),
        json!({"name": "hal-optimize", "protocols": ["responses"], "tools": ["a", "b"]})
    );
    assert_eq!(
        responses_only_projection("hal", false, &[])
            .unwrap_err()
            .to_string(),
        "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting."
    );
    assert_eq!(
        responses_only_projection("", true, &[]).unwrap(),
        json!({"name": null, "protocols": ["responses"], "tools": []})
    );
}

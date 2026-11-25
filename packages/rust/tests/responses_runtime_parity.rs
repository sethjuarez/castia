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

#[test]
fn platform_id_extracts_partitions_like_agentserver() {
    use castia::protocols::platform_id;
    // Current format: first 18 body chars, any suffix (agentserver does not check "00").
    let current = format!("caresp_{}{}", "abcdefABCDEF0123zz", "x".repeat(32));
    assert_eq!(
        &platform_id("msg", Some(&current))[4..22],
        "abcdefABCDEF0123zz"
    );
    // Legacy 48-char body: last 16 chars + "00".
    let legacy = format!("resp_{}{}", "y".repeat(32), "0123456789abcdef");
    assert_eq!(
        &platform_id("msg", Some(&legacy))[4..22],
        "0123456789abcdef00"
    );
    assert!(platform_id("caresp", None).starts_with("caresp_"));
}

#[test]
fn resolve_response_id_prefers_header_then_body_then_mint() {
    use castia::protocols::{resolve_response_id, validate_response_id, RESPONSE_ID_HEADER};
    assert_eq!(RESPONSE_ID_HEADER, "x-agent-response-id");
    let header = castia::protocols::platform_id("caresp", None);
    let body = castia::protocols::platform_id("resp", None);

    assert_eq!(
        resolve_response_id(Some(&header), Some(&json!(body)), "caresp", None).unwrap(),
        header
    );
    assert_eq!(
        resolve_response_id(Some("  "), Some(&json!(body)), "caresp", None).unwrap(),
        body
    );
    let minted = resolve_response_id(None, None, "caresp", None).unwrap();
    assert!(minted.starts_with("caresp_") && minted.len() == "caresp_".len() + 50);

    let err = resolve_response_id(Some("resp_short"), None, "caresp", None).unwrap_err();
    assert!(err.starts_with("Malformed identifier"), "{err}");
    assert!(validate_response_id("").is_some());
    assert!(validate_response_id("nodelimiter").is_some());
    assert!(validate_response_id(&format!("_{}", "a".repeat(50))).is_some());
    assert!(validate_response_id(&format!("resp_{}", "a".repeat(48))).is_none());
}

#[test]
fn item_id_uses_agentserver_prefixes() {
    use castia::protocols::item_id;
    let response = castia::protocols::platform_id("caresp", None);
    let cases = [
        (json!({"type": "message"}), "msg_"),
        (json!({"role": "user", "content": "hi"}), "msg_"),
        (json!({"type": "function_call"}), "fc_"),
        (json!({"type": "function_call_output"}), "fco_"),
        (json!({"type": "reasoning"}), "rs_"),
        (json!({"type": "mcp_call"}), "mcp_"),
    ];
    for (item, prefix) in cases {
        let id = item_id(&item, Some(&response)).expect("known type");
        assert!(id.starts_with(prefix), "{id}");
        assert_eq!(id[prefix.len()..prefix.len() + 18], response[7..25]);
    }
    assert!(item_id(&json!({"type": "item_reference", "id": "x"}), None).is_none());
    assert!(item_id(&json!({"type": "unknown"}), None).is_none());
}

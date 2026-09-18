use castia::runtime::{decorate_message, turn_cite};
use serde_json::json;

#[test]
fn turn_cite_auto_increments_position() {
    let first = turn_cite(&json!([]), "First", "", "", &json!(null), "");
    assert_eq!(first["position"], json!(1));
    let second = turn_cite(
        &first["citations"],
        "Second",
        "https://x",
        "",
        &json!(null),
        "",
    );
    assert_eq!(second["position"], json!(2));
    assert_eq!(
        second["citations"][1]["appearance"]["url"],
        json!("https://x")
    );
}

#[test]
fn decorate_message_merges_turn_and_overrides() {
    let turn = json!({
        "aiGenerated": true,
        "citations": [turn_cite(&json!([]), "From tool", "", "", &json!(null), "")["citations"][0].clone()],
    });
    let payload = decorate_message(
        &json!({"type": "message", "text": "hi"}),
        &turn,
        false,
        &json!(null),
        &json!(null),
        Some("default"),
        Some("high"),
    );

    let root = payload["entities"].as_array().unwrap().last().unwrap();
    assert_eq!(root["additionalType"], json!(["AIGeneratedContent"]));
    assert_eq!(root["citation"].as_array().unwrap().len(), 1);
    assert_eq!(
        payload["channelData"],
        json!({"feedbackLoop": {"type": "default"}})
    );
    assert_eq!(payload["importance"], json!("high"));
}

#[test]
fn decorate_message_no_turn_uses_only_overrides() {
    let payload = decorate_message(
        &json!({"type": "message"}),
        &json!(null),
        true,
        &json!(null),
        &json!(null),
        None,
        None,
    );
    let entities = payload["entities"].as_array().unwrap();
    assert_eq!(entities.len(), 1);
    assert_eq!(entities[0]["additionalType"], json!(["AIGeneratedContent"]));
    assert!(payload.get("channelData").is_none());
}

#[test]
fn decorate_message_preserves_existing_entities_and_channel_data() {
    let payload = decorate_message(
        &json!({"type": "message", "entities": [{"type": "mention"}], "channelData": {"existing": true}}),
        &json!({
            "sensitivity": {"@type": "CreativeWork", "name": "Confidential"},
            "feedback": "custom",
            "importance": "urgent",
        }),
        false,
        &json!([{"@type": "Claim", "position": 1, "appearance": {"@type": "DigitalDocument", "name": "Override citation"}}]),
        &json!(null),
        None,
        None,
    );

    assert_eq!(payload["entities"][0]["type"], json!("mention"));
    assert_eq!(
        payload["entities"][1]["usageInfo"]["name"],
        json!("Confidential")
    );
    assert_eq!(
        payload["channelData"],
        json!({"existing": true, "feedbackLoop": {"type": "custom"}})
    );
    assert_eq!(payload["importance"], json!("urgent"));
}

#[test]
fn decorate_message_replaces_non_container_slots_instead_of_losing_decorations() {
    let payload = decorate_message(
        &json!({"type": "message", "entities": null, "channelData": null}),
        &json!(null),
        true,
        &json!(null),
        &json!(null),
        Some("default"),
        None,
    );

    assert_eq!(
        payload["entities"][0]["additionalType"],
        json!(["AIGeneratedContent"])
    );
    assert_eq!(
        payload["channelData"],
        json!({"feedbackLoop": {"type": "default"}})
    );
}

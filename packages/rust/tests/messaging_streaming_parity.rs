use castia::messaging::{
    append_plan, capture_stream_id, connector_endpoint, connector_envelope, connector_ok,
    created_activity_id, delete_activity_request, finish_allowed, finish_text, next_flush,
    next_sequence, post_activity_request, reaction_request, stream_chunk, typing_request,
    update_activity_request, update_allowed,
};
use castia::model::{Activity, LoadContext};
use serde_json::json;

fn parse_activity(value: serde_json::Value) -> Activity {
    Activity::load_from_value(&value, &LoadContext::default())
}

fn rich_activity() -> Activity {
    parse_activity(json!({
        "type": "message",
        "id": "1700000000000",
        "text": "hello",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "conversation": {"id": "19:meeting_abc@thread.v2"},
        "from": {"id": "29:user", "name": "User"},
        "recipient": {"id": "28:agent", "name": "HAL"},
    }))
}

#[test]
fn connector_request_shapes_match_python() {
    let activity = rich_activity();
    assert_eq!(
        connector_endpoint(&activity).unwrap(),
        json!({
            "service_url": "https://smba.trafficmanager.net/teams",
            "conversation_id": "19:meeting_abc@thread.v2",
        })
    );
    assert_eq!(
        connector_envelope(&activity),
        json!({
            "from": {"id": "28:agent", "name": "HAL"},
            "recipient": {"id": "29:user", "name": "User"},
            "conversation": {"id": "19:meeting_abc@thread.v2"},
            "replyToId": "1700000000000",
        })
    );
    let no_turn = parse_activity(json!({
        "conversation": {"id": "chat"},
        "from": {"id": "29:user"},
        "recipient": {"id": "28:agent"},
    }));
    assert_eq!(connector_envelope(&no_turn)["replyToId"], json!(null));
    let post =
        post_activity_request(&activity, &json!({"type": "message", "text": "hi there"})).unwrap();
    assert_eq!(post["method"], json!("POST"));
    assert_eq!(post["json"]["type"], json!("message"));
    assert_eq!(post["json"]["from"]["id"], json!("28:agent"));
    assert_eq!(post["json"]["recipient"]["id"], json!("29:user"));

    let typing = typing_request(&activity).unwrap();
    assert_eq!(typing["json"]["type"], json!("typing"));
    assert!(typing["json"].get("text").is_none());
}

#[test]
fn connector_verb_urls_and_responses_match_python() {
    let activity = rich_activity();
    assert_eq!(
        reaction_request(&activity, "white check", "PUT").unwrap()["url"],
        json!("https://smba.trafficmanager.net/teams/v3/conversations/19:meeting_abc@thread.v2/activities/1700000000000/reactions/white%20check")
    );
    let update = update_activity_request(
        &activity,
        "edit-me",
        &json!({"type": "message", "text": "v2"}),
    )
    .unwrap();
    assert_eq!(update["method"], json!("PUT"));
    assert_eq!(update["json"]["id"], json!("edit-me"));
    assert_eq!(
        delete_activity_request(&activity, "drop-me").unwrap()["method"],
        json!("DELETE")
    );
    assert_eq!(
        created_activity_id(&json!({"statusCode": 201, "body": {"id": "created-id"}})),
        Some("created-id".to_string())
    );
    assert_eq!(
        created_activity_id(&json!({"statusCode": 403, "body": {"id": "ignored"}})),
        None
    );
    assert!(connector_ok(&json!({"statusCode": 204})));
    assert!(!connector_ok(&json!(null)));
}

#[test]
fn streaming_chunks_match_teams_contract() {
    let informative = stream_chunk(
        "informative",
        "Thinking...",
        None,
        0,
        false,
        &json!(null),
        &json!(null),
    );
    assert_eq!(informative["type"], json!("typing"));
    assert_eq!(informative["channelData"]["streamSequence"], json!(1));
    assert!(informative["channelData"].get("streamId").is_none());

    let streaming = stream_chunk(
        "streaming",
        "Hello",
        Some("created-id"),
        1,
        false,
        &json!(null),
        &json!(null),
    );
    assert_eq!(streaming["channelData"]["streamId"], json!("created-id"));
    assert_eq!(streaming["entities"][0]["streamSequence"], json!(2));

    let final_chunk = stream_chunk(
        "final",
        "Hello world",
        Some("created-id"),
        3,
        true,
        &json!(null),
        &json!({"actions": [{"type": "imBack", "title": "Thanks!", "value": "Thanks!"}], "to": []}),
    );
    assert_eq!(final_chunk["type"], json!("message"));
    assert!(final_chunk["channelData"].get("streamSequence").is_none());
    assert_eq!(final_chunk["entities"][0]["streamId"], json!("created-id"));
    assert_eq!(
        final_chunk["suggestedActions"]["actions"][0]["title"],
        json!("Thanks!")
    );
}

#[test]
fn streaming_state_helpers_match_python_noops_and_flushes() {
    assert_eq!(
        capture_stream_id(None, Some("created-id")),
        Some("created-id".to_string())
    );
    assert_eq!(
        capture_stream_id(Some("first-id"), Some("second-id")),
        Some("first-id".to_string())
    );
    assert!(update_allowed(false, ""));
    assert!(!update_allowed(false, "body"));
    assert_eq!(
        append_plan(false, "Hello", " world", 1.0, 2.0, 0.75),
        json!({"text": "Hello world", "flush": true, "nextFlush": 2})
    );
    assert_eq!(
        append_plan(false, "Hello", "", 1.0, 2.0, 0.75),
        json!({"text": "Hello", "flush": false, "nextFlush": 1})
    );
    assert!(finish_allowed(false));
    assert!(!finish_allowed(true));
    assert_eq!(next_sequence(2, false), 3);
    assert_eq!(next_sequence(3, true), 3);
    assert_eq!(next_flush(1.0, 2.0, true), json!(2));
    assert_eq!(next_flush(1.0, 2.0, false), json!(1));
    assert_eq!(finish_text("Hello", None), "Hello");
    assert_eq!(finish_text("Hello", Some("")), "");
}

use castia::hosting::foundry_storage::{
    foundry_storage_enabled, is_retryable_status, storage_error, stored_input_items,
    FoundryStorage, FoundryStorageErrorKind, FOUNDRY_CALL_ID_HEADER, FOUNDRY_STORAGE_SCOPE,
};
use serde_json::json;
use std::collections::HashMap;

fn storage() -> FoundryStorage {
    FoundryStorage::new("https://acct.services.ai.azure.com/api/projects/p/").unwrap()
}

fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(key, _)| key.eq_ignore_ascii_case(name))
        .map(|(_, value)| value.as_str())
}

#[test]
fn settings_match_agentserver_provider() {
    assert_eq!(FOUNDRY_STORAGE_SCOPE, "https://ai.azure.com/.default");
    assert_eq!(
        storage().base_url(),
        "https://acct.services.ai.azure.com/api/projects/p/storage/"
    );
    assert!(FoundryStorage::new("").is_err());
    assert!(FoundryStorage::new("ftp://x").is_err());
    assert!(FoundryStorage::new("not a url").is_err());

    let mut env = HashMap::new();
    assert!(!foundry_storage_enabled(&env));
    assert!(FoundryStorage::from_env(&env).is_err());
    env.insert("FOUNDRY_HOSTING_ENVIRONMENT".into(), "prod".into());
    env.insert(
        "FOUNDRY_PROJECT_ENDPOINT".into(),
        "https://acct.services.ai.azure.com/api/projects/p".into(),
    );
    assert!(foundry_storage_enabled(&env));
    assert_eq!(FoundryStorage::from_env(&env).unwrap(), storage());
}

#[test]
fn create_request_uses_the_stock_envelope_and_call_id() {
    let response = json!({"id": "caresp_x", "object": "response", "status": "completed"});
    let request = storage().create_response(
        &response,
        vec![json!({"id": "msg_1", "type": "message"})],
        vec!["msg_0".into()],
        "tok",
        Some("call-1"),
    );
    assert_eq!(request.method, "POST");
    assert_eq!(
        request.url,
        "https://acct.services.ai.azure.com/api/projects/p/storage/responses?api-version=v1"
    );
    assert_eq!(
        header(&request.headers, "Authorization"),
        Some("Bearer tok")
    );
    assert_eq!(
        header(&request.headers, FOUNDRY_CALL_ID_HEADER),
        Some("call-1")
    );
    assert_eq!(
        header(&request.headers, "Content-Type"),
        Some("application/json; charset=utf-8")
    );
    assert_eq!(
        request.body.unwrap(),
        json!({
            "response": response,
            "input_items": [{"id": "msg_1", "type": "message"}],
            "history_item_ids": ["msg_0"],
        })
    );
}

#[test]
fn read_update_delete_and_items_paths_match_stock() {
    let s = storage();
    let base = "https://acct.services.ai.azure.com/api/projects/p/storage/";
    let update = s.update_response(&json!({"id": "caresp_a"}), "t", None);
    assert_eq!(
        (update.method, update.url.as_str()),
        ("POST", &*format!("{base}responses/caresp_a?api-version=v1"))
    );
    assert!(header(&update.headers, FOUNDRY_CALL_ID_HEADER).is_none());

    let get = s.get_response("caresp_a", "t", Some(""));
    assert_eq!(get.method, "GET");
    assert!(get.body.is_none() && header(&get.headers, "Content-Type").is_none());
    assert!(header(&get.headers, FOUNDRY_CALL_ID_HEADER).is_none());

    assert_eq!(s.delete_response("caresp_a", "t", None).method, "DELETE");
    assert_eq!(
        s.input_items("caresp_a", Some("msg_9"), "t", None).url,
        format!(
            "{base}responses/caresp_a/input_items?api-version=v1&limit=100&order=asc&after=msg_9"
        )
    );
    let batch = s.batch_retrieve(&["msg_1".into()], "t", None);
    assert_eq!(
        batch.url,
        format!("{base}items/batch/retrieve?api-version=v1")
    );
    assert_eq!(batch.body.unwrap(), json!({"item_ids": ["msg_1"]}));
    assert_eq!(
        s.history_item_ids(Some("caresp_p"), None, 50, "t", None)
            .url,
        format!("{base}history/item_ids?api-version=v1&limit=50&previous_response_id=caresp_p")
    );
}

#[test]
fn errors_are_explicit_and_conflicts_detected() {
    let conflict = storage_error(
        409,
        Some(&json!({"error": {"code": "conflict", "message": "x"}})),
    );
    assert_eq!(conflict.kind, FoundryStorageErrorKind::AlreadyExists);
    let exists = storage_error(
        400,
        Some(&json!({"error": {"message": "Response already exists"}})),
    );
    assert_eq!(exists.kind, FoundryStorageErrorKind::AlreadyExists);
    let other = storage_error(500, None);
    assert_eq!(other.kind, FoundryStorageErrorKind::Other);
    assert_eq!(other.status_code, Some(500));
    assert!(other.message.contains("500"));
    assert!(is_retryable_status(429) && is_retryable_status(503) && is_retryable_status(408));
    assert!(!is_retryable_status(404) && !is_retryable_status(400));
}

#[test]
fn input_items_are_normalized_like_stock() {
    let response_id = castia::protocols::platform_id("caresp", None);
    let (items, references) = stored_input_items(
        &[
            json!({"role": "user", "content": "hi", "status": "in_progress"}),
            json!({"type": "item_reference", "id": "msg_ref"}),
            json!({"id": "msg_untyped"}),
            json!({"type": "output_message", "role": "assistant", "content": []}),
            json!({"type": "unknown_kind"}),
            json!("not an object"),
        ],
        &response_id,
    )
    .unwrap();
    assert_eq!(references, vec!["msg_ref", "msg_untyped"]);
    assert_eq!(items.len(), 4);
    let message = items[0].as_ref().unwrap();
    assert_eq!(message["type"], "message");
    assert_eq!(message["status"], "completed");
    assert_eq!(
        message["content"],
        json!([{"type": "input_text", "text": "hi"}])
    );
    let id = message["id"].as_str().unwrap();
    assert_eq!(id[4..22], response_id[7..25]);
    assert!(items[1].is_none() && items[2].is_none());
    assert_eq!(items[3].as_ref().unwrap()["status"], "completed");

    assert!(stored_input_items(&[json!({"type": "item_reference"})], &response_id).is_err());
}

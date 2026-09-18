use castia::lifecycle::{canonical_json, check_public, content_hash, safe_path};
use serde_json::json;

#[test]
fn canonical_json_and_content_hash_match_python_vectors() {
    let value = json!({ "b": [2, 1], "a": "é" });
    assert_eq!(canonical_json(&value).unwrap(), "{\"a\":\"é\",\"b\":[2,1]}");
    assert_eq!(
        content_hash(&value).unwrap(),
        "265cdd44ca612f13fd2b8e14f6913a5513adf3142e6c82317e55ba51948f43f2"
    );
    assert_eq!(
        content_hash(&json!({ "input": "question-0" })).unwrap(),
        "d3d9fa86e6735affbb18ca08a7306a6fe3cd8f6b335422e44ef85591d5b7ddb1"
    );
}

#[test]
fn safe_path_matches_python_path_gate() {
    assert_eq!(safe_path("src\\main.py").unwrap(), "src/main.py");
    for path in [
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\absolute",
        "C:relative",
        "\\\\server\\share",
        "dir/../escape",
        "dir//file",
        "./file",
        "file:stream",
        "CON.txt",
        "trailing.",
        "space ",
    ] {
        assert_eq!(
            safe_path(path).unwrap_err().to_string(),
            "path must be a normalized relative file path"
        );
    }
    assert_eq!(
        safe_path("").unwrap_err().to_string(),
        "relative path must be nonempty text"
    );
}

#[test]
fn check_public_rejects_secret_shapes_and_allows_placeholders() {
    check_public(&json!({
        "apiKey": "${API_KEY}",
        "nested": { "password": "<redacted>" },
    }))
    .unwrap();
    for value in [
        json!({ "api_key": "private" }),
        json!({ "apiKey": "private" }),
        json!({ "clientSecret": "private" }),
        json!({ "authToken": "private" }),
        json!({ "nested": { "password": "private" } }),
    ] {
        assert_eq!(
            check_public(&value).unwrap_err().to_string(),
            "secret-bearing configuration key is not evidence"
        );
    }
    assert_eq!(
        check_public(&json!("Authorization: Bearer abcdefghijklmnopqrstuvwxyz"))
            .unwrap_err()
            .to_string(),
        "credential-shaped content is not evidence"
    );
    assert_eq!(
        check_public(&json!("{\"outer\": {\"password\": \"hunter2\"}}"))
            .unwrap_err()
            .to_string(),
        "secret-bearing configuration assignment is not evidence"
    );
}

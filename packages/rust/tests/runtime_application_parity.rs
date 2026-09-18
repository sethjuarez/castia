use castia::runtime::{
    depends_marker, include_application_plan, registered_tools, resolve_dependency_plan,
    responses_only_application_projection,
};
use serde_json::json;

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| value.to_string()).collect()
}

#[test]
fn registered_tools_matches_python_flattening_and_dedupe() {
    assert_eq!(registered_tools(&json!([])), json!([]));
    assert_eq!(
        registered_tools(&json!([[{"name": "a"}], [{"name": "b"}]])),
        json!([{"name": "a"}, {"name": "b"}])
    );
    assert_eq!(
        registered_tools(
            &json!([[{"name": "dup", "description": "first"}], [{"name": "dup", "description": "second"}]])
        ),
        json!([{"name": "dup", "description": "first"}])
    );
    assert_eq!(
        registered_tools(
            &json!([[{"type": "mcp", "serverLabel": "graph"}, {"name": "search"}], [{"type": "mcp", "serverLabel": "graph"}]])
        ),
        json!([
            {"type": "mcp", "serverLabel": "graph"},
            {"name": "search"},
            {"type": "mcp", "serverLabel": "graph"},
        ])
    );
}

#[test]
fn include_application_plan_merges_tool_provider_counts() {
    assert_eq!(
        include_application_plan(1, 2),
        json!({"toolProviderCount": 3})
    );
}

#[test]
fn responses_only_application_projection_matches_agent_behavior() {
    assert_eq!(
        responses_only_application_projection("hal", None, true, true, &json!([{"name": "a"}]))
            .unwrap(),
        json!({
            "name": "hal-optimize",
            "protocols": ["responses"],
            "wireProtocols": ["responses", "responses_stream"],
            "tools": [{"name": "a"}],
        })
    );
    assert_eq!(
        responses_only_application_projection("hal", Some("custom"), true, false, &json!([]))
            .unwrap(),
        json!({
            "name": "custom",
            "protocols": ["responses"],
            "wireProtocols": ["responses"],
            "tools": [],
        })
    );
    assert_eq!(
        responses_only_application_projection("hal", Some(""), true, false, &json!([])).unwrap(),
        json!({
            "name": "hal-optimize",
            "protocols": ["responses"],
            "wireProtocols": ["responses"],
            "tools": [],
        })
    );
    assert_eq!(
        responses_only_application_projection("", None, true, false, &json!([])).unwrap(),
        json!({
            "name": null,
            "protocols": ["responses"],
            "wireProtocols": ["responses"],
            "tools": [],
        })
    );
    assert_eq!(
        responses_only_application_projection("hal", None, false, true, &json!([]))
            .unwrap_err()
            .to_string(),
        "responses_only() needs an @responses handler to project; this agent registered none. Add @app.responses() (or include a router that does) before projecting."
    );
}

#[test]
fn dependency_plans_match_depends_marker_and_resolve_cache() {
    assert_eq!(
        depends_marker("model", true),
        json!({"kind": "Depends", "dependency": "model", "useCache": true})
    );
    assert_eq!(
        depends_marker("nonce", false),
        json!({"kind": "Depends", "dependency": "nonce", "useCache": false})
    );
    assert_eq!(
        resolve_dependency_plan(&strings(&["model"]), "model", true),
        json!({"action": "cache_hit", "store": false})
    );
    assert_eq!(
        resolve_dependency_plan(&[], "model", true),
        json!({"action": "call_dependency", "store": true})
    );
    assert_eq!(
        resolve_dependency_plan(&strings(&["nonce"]), "nonce", false),
        json!({"action": "cache_hit", "store": false})
    );
}

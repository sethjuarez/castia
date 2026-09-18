use castia::inference::{
    activity_tools, agent_tools, graph_tools, instructions_param, public_tool_spec,
    reasoning_param, tool_spec, try_reasoning_param,
};
use serde_json::json;

#[test]
fn reasoning_param_matches_python_validation_and_normalization() {
    assert_eq!(reasoning_param(None), json!({}));
    assert_eq!(
        reasoning_param(Some(" Medium ")),
        json!({ "reasoning": { "effort": "medium" } })
    );
    assert_eq!(
        try_reasoning_param(Some("extreme"))
            .unwrap_err()
            .to_string(),
        "reasoning_effort must be one of ('minimal', 'low', 'medium', 'high'), got 'extreme'"
    );
}

#[test]
fn instructions_param_matches_python_shape() {
    assert_eq!(instructions_param(None), json!({}));
    assert_eq!(
        instructions_param(Some("be terse")),
        json!({ "instructions": "be terse" })
    );
}

#[test]
fn public_tool_spec_strips_castia_private_metadata() {
    let spec = json!({
        "type": "mcp",
        "server_label": "toolbox",
        "server_url": "https://x/mcp",
        "x-castia-optimizer-tool-definitions": [{ "function": { "name": "x" } }],
    });
    assert_eq!(
        public_tool_spec(&spec, &json!([])),
        json!({
            "type": "mcp",
            "server_label": "toolbox",
            "server_url": "https://x/mcp",
        })
    );
}

#[test]
fn public_tool_spec_does_not_rewrite_flat_function_specs() {
    let spec = json!({
        "type": "function",
        "name": "send_email",
        "description": "Original.",
        "parameters": { "type": "object" },
    });
    let definitions = json!([{
        "type": "function",
        "function": { "name": "send_email", "description": "Rewritten." },
    }]);

    assert_eq!(public_tool_spec(&spec, &definitions), spec);
}

#[test]
fn tool_families_match_python_names_and_order() {
    let names = |tools: Vec<serde_json::Value>| {
        tools
            .into_iter()
            .map(|tool| tool["name"].as_str().unwrap().to_string())
            .collect::<Vec<_>>()
    };

    assert_eq!(names(activity_tools()), ["react_to_message", "cite_source"]);
    assert_eq!(
        names(graph_tools()),
        ["send_email", "reply_email", "create_document", "read_inbox"]
    );
    assert_eq!(
        names(agent_tools()),
        [
            "react_to_message",
            "cite_source",
            "send_email",
            "reply_email",
            "create_document",
            "read_inbox",
        ]
    );
}

#[test]
fn tool_spec_matches_python_responses_function_tool_shape() {
    let tool = json!({
        "name": "send_email",
        "description": "Send.",
        "parameters": { "type": "object" },
        "scopes": ["Mail.Send"],
    });
    assert_eq!(
        tool_spec(&tool),
        json!({
            "type": "function",
            "name": "send_email",
            "description": "Send.",
            "parameters": { "type": "object" },
        })
    );
}

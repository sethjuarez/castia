use castia::optimizing::{apply_optimized_tool_definitions, tools_json};
use serde_json::json;

#[test]
fn tools_json_emits_nested_function_form() {
    let tools = vec![json!({
        "type": "function",
        "name": "send_email",
        "description": "Send an email",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "orig-to"},
                "count": {"type": "integer", "description": "orig-count"}
            },
            "required": ["to"],
            "additionalProperties": false
        }
    })];

    assert_eq!(
        tools_json(&tools),
        vec![json!({
            "type": "function",
            "function": {
                "name": "send_email",
                "description": "Send an email",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "orig-to"},
                        "count": {"type": "integer", "description": "orig-count"}
                    },
                    "required": ["to"],
                    "additionalProperties": false
                }
            }
        })]
    );
}

#[test]
fn tools_json_includes_toolbox_optimizer_sidecar() {
    let tools = vec![json!({
        "type": "mcp",
        "server_label": "contracts",
        "x-castia-optimizer-tool-definitions": [{
            "type": "function",
            "function": {
                "name": "knowledge_base_retrieve",
                "description": "Search contract policy.",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": true}
            }
        }]
    })];

    assert_eq!(
        tools_json(&tools),
        vec![json!({
            "type": "function",
            "function": {
                "name": "knowledge_base_retrieve",
                "description": "Search contract policy.",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": true}
            }
        })]
    );
}

#[test]
fn apply_overlays_nested_function_description_and_parameter_descriptions_only() {
    let tools = vec![json!({
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "orig",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "orig-to"},
                    "count": {"type": "integer", "description": "orig-count"}
                },
                "required": ["to"]
            }
        }
    })];
    let defs = json!([{
        "function": {
            "name": "send_email",
            "description": "REWRITTEN",
            "parameters": {
                "properties": {
                    "to": {"type": "number", "description": "BETTER to"},
                    "count": {"type": "string", "description": "orig-count"}
                },
                "required": []
            }
        }
    }]);

    let applied = apply_optimized_tool_definitions(&tools, &defs);
    let function = &applied[0]["function"];
    assert_eq!(function["description"], "REWRITTEN");
    assert_eq!(
        function["parameters"]["properties"]["to"]["description"],
        "BETTER to"
    );
    assert_eq!(function["parameters"]["properties"]["to"]["type"], "string");
    assert_eq!(
        function["parameters"]["properties"]["count"]["type"],
        "integer"
    );
    assert_eq!(function["parameters"]["required"], json!(["to"]));
}

#[test]
fn apply_accepts_flat_responses_form_and_unknown_names_pass_through() {
    let tools = vec![
        json!({"type":"function","name":"send_email","description":"orig"}),
        json!({"type":"function","name":"read_inbox","description":"orig"}),
    ];
    let defs = json!([{"type":"function","name":"send_email","description":"flat-new"}]);

    let applied = apply_optimized_tool_definitions(&tools, &defs);
    assert_eq!(applied[0]["description"], "flat-new");
    assert_eq!(applied[1], tools[1]);
}

#[test]
fn apply_ignores_empty_optimized_descriptions() {
    let tools = vec![json!({
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Original.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": { "type": "string", "description": "Original recipient." },
                },
            },
        },
    })];
    let definitions = json!([{
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "",
            "parameters": {
                "properties": {
                    "to": { "description": "" },
                },
            },
        },
    }]);

    assert_eq!(
        apply_optimized_tool_definitions(&tools, &definitions),
        tools
    );
}

#[test]
fn apply_overlays_toolbox_sidecar_and_regenerates_server_description() {
    let tools = vec![json!({
        "type": "mcp",
        "server_label": "contracts",
        "server_description": "Contracts toolbox.\nLocal tool guidance:\n- knowledge_base_retrieve: Search contract policy.",
        "x-castia-server-description": "Contracts toolbox.",
        "x-castia-optimizer-tool-definitions": [{
            "type": "function",
            "function": {
                "name": "knowledge_base_retrieve",
                "description": "Search contract policy.",
                "parameters": {
                    "properties": {
                        "query": {"description": "Original query hint."}
                    }
                }
            }
        }]
    })];
    let defs = json!([{
        "function": {
            "name": "knowledge_base_retrieve",
            "description": "REWRITTEN policy search.",
            "parameters": {
                "properties": {
                    "query": {"description": "REWRITTEN query hint."}
                }
            }
        }
    }]);

    let applied = apply_optimized_tool_definitions(&tools, &defs);
    let sidecar = &applied[0]["x-castia-optimizer-tool-definitions"][0]["function"];
    assert_eq!(sidecar["description"], "REWRITTEN policy search.");
    assert_eq!(
        sidecar["parameters"]["properties"]["query"]["description"],
        "REWRITTEN query hint."
    );
    let server_description = applied[0]["server_description"]
        .as_str()
        .expect("server description is string");
    assert!(server_description.contains("Contracts toolbox."));
    assert!(server_description.contains("REWRITTEN policy search."));
    assert!(server_description.contains("REWRITTEN query hint."));
}

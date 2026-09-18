use crate::model::ToolCatalogRuntime;
use serde_json::{json, Value};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaToolCatalogRuntime;

#[async_trait::async_trait]
impl ToolCatalogRuntime for CastiaToolCatalogRuntime {
    fn activity_tool_names(&self) -> Value {
        tool_names(&activity_tools())
    }

    fn agent_tool_names(&self) -> Value {
        tool_names(&agent_tools())
    }

    fn graph_tool_names(&self) -> Value {
        tool_names(&graph_tools())
    }

    fn tool_spec(&self, tool: &Value) -> Value {
        tool_spec(tool)
    }
}

pub fn agent_tools() -> Vec<Value> {
    let mut tools = activity_tools();
    tools.extend(graph_tools());
    tools
}

pub fn activity_tools() -> Vec<Value> {
    vec![
        json!({
            "name": "react_to_message",
            "description": "Add (or remove) an emoji reaction on the message you are responding to. Use to acknowledge lightweight requests without a full reply -- a thumbs-up/like to confirm you'll do something, 'eyes' while you look into it, or 'check' when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reaction": {
                        "type": "string",
                        "description": "Reaction id, e.g. 'like', 'heart', 'laugh', '1f440_eyes' (looking), '2705_whiteheavycheckmark' (done).",
                    },
                    "remove": {
                        "type": "boolean",
                        "description": "Remove the reaction instead of adding it.",
                    },
                },
                "required": [],
                "additionalProperties": false,
            },
            "scopes": [],
        }),
        json!({
            "name": "cite_source",
            "description": "Attribute a claim in your answer to a source. Call once per source BEFORE writing the sentence it supports, then put the returned [n] marker at the end of that sentence. The source appears as a numbered reference under your message; do not invent sources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": { "type": "string", "description": "Source title shown in the reference (<=80 chars)." },
                    "url": { "type": "string", "description": "Optional link to the source." },
                    "snippet": { "type": "string", "description": "Optional hover abstract (<=160 chars)." },
                    "icon": { "type": "string", "description": "Optional document-type glyph, e.g. 'PDF', 'Word', 'Excel'." },
                },
                "required": ["name"],
                "additionalProperties": false,
            },
            "scopes": [],
        }),
    ]
}

pub fn graph_tools() -> Vec<Value> {
    vec![
        json!({
            "name": "send_email",
            "description": "Send a NEW email as the agent's own mailbox identity. Use when composing a fresh message to someone. To respond to a message the agent received, use reply_email instead so it threads correctly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": { "type": "string", "description": "Recipient email address." },
                    "subject": { "type": "string", "description": "Email subject line." },
                    "body": { "type": "string", "description": "Plain-text email body." },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": false,
            },
            "scopes": ["Mail.Send"],
        }),
        json!({
            "name": "reply_email",
            "description": "Reply in-thread to an email the agent received. Use this (not send_email) to respond to an inbox message, so the reply stays in the original conversation thread. Pass the message's id from read_inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": { "type": "string", "description": "The id of the received message to reply to, as returned by read_inbox." },
                    "body": { "type": "string", "description": "Plain-text reply body." },
                },
                "required": ["message_id", "body"],
                "additionalProperties": false,
            },
            "scopes": ["Mail.Send"],
        }),
        json!({
            "name": "create_document",
            "description": "Create or replace a text/markdown document on the agent's own OneDrive. Use when the user asks to save, write, or leave a note or document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Filename or relative path under the agent's OneDrive, e.g. 'summary.md' or 'notes/2026/summary.md'." },
                    "content": { "type": "string", "description": "The document's text content (markdown)." },
                },
                "required": ["path", "content"],
                "additionalProperties": false,
            },
            "scopes": ["Files.ReadWrite"],
        }),
        json!({
            "name": "read_inbox",
            "description": "Read the newest messages in the agent's own mailbox. Use when the user asks what email the agent has received, to check its inbox, or to look for a specific message. Each message includes an id you can pass to reply_email to respond in-thread.",
            "parameters": {
                "type": "object",
                "properties": {
                    "top": { "type": "integer", "description": "How many recent messages to return (1-25)." },
                    "unread_only": { "type": "boolean", "description": "Only return unread messages when true." },
                },
                "required": [],
                "additionalProperties": false,
            },
            "scopes": ["Mail.Read"],
        }),
    ]
}

pub fn tool_spec(tool: &Value) -> Value {
    json!({
        "type": "function",
        "name": tool.get("name").cloned().unwrap_or(Value::Null),
        "description": tool.get("description").cloned().unwrap_or(Value::Null),
        "parameters": tool.get("parameters").cloned().unwrap_or_else(|| json!({})),
    })
}

fn tool_names(tools: &[Value]) -> Value {
    Value::Array(
        tools
            .iter()
            .filter_map(|tool| tool.get("name").and_then(Value::as_str))
            .map(|name| json!(name))
            .collect(),
    )
}

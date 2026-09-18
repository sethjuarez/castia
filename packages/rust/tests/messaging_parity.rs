use castia::messaging::{
    action_chips, adaptive_card, card_action, card_invoke_response, citation, decision_card,
    feedback_channel_data, feedback_payload, mention_entity, message_entity,
    message_invoke_response, require_agentic_user, sensitivity_label, suggested_actions,
    teams_direct_message, teams_group_chat_message, teams_tagged_channel_message,
    ADAPTIVE_CARD_CONTENT_TYPE,
};
use castia::model::{Activity, LoadContext};
use serde_json::json;

fn activity(value: serde_json::Value) -> Activity {
    Activity::load_from_value(&value, &LoadContext::default())
}

#[test]
fn adaptive_card_matches_python_wire_shape() {
    let body = json!([{ "type": "TextBlock", "text": "Deployment complete" }]);
    assert_eq!(
        adaptive_card(&body, &json!({})),
        json!({
            "contentType": ADAPTIVE_CARD_CONTENT_TYPE,
            "content": {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5",
                "body": [{ "type": "TextBlock", "text": "Deployment complete" }],
            },
        })
    );
}

#[test]
fn suggested_actions_and_action_chips_match_python_builders() {
    assert_eq!(
        suggested_actions(&json!(["Yes", { "title": "Nope", "value": "No", "type": "postBack" }])),
        json!({
            "actions": [
                { "type": "imBack", "title": "Yes", "value": "Yes" },
                { "type": "postBack", "title": "Nope", "value": "No" },
            ],
            "to": [],
        })
    );
    assert_eq!(
        suggested_actions(&json!([{ "title": "Object", "value": { "id": "42" } }])),
        json!({
            "actions": [{ "type": "imBack", "title": "Object", "value": { "id": "42" } }],
            "to": [],
        })
    );

    let chips = action_chips(&json!(["Thanks"]), "Anything else?");
    assert_eq!(chips["content"]["body"][0]["text"], "Anything else?");
    assert_eq!(chips["content"]["actions"][0]["type"], "Action.Submit");
    assert_eq!(
        chips["content"]["actions"][0]["data"],
        json!({ "msteams": { "type": "imBack", "value": "Thanks" }, "choice": "Thanks" })
    );
}

#[test]
fn decision_card_uses_execute_actions() {
    let card = decision_card(
        &json!([{ "title": "Approve", "verb": "approve" }, "cancel"]),
        "Deploy?",
    );
    assert_eq!(card["contentType"], ADAPTIVE_CARD_CONTENT_TYPE);
    assert_eq!(card["content"]["body"][0]["weight"], "Bolder");
    assert_eq!(card["content"]["actions"][0]["type"], "Action.Execute");
    assert_eq!(card["content"]["actions"][0]["verb"], "approve");
    assert_eq!(card["content"]["actions"][1]["title"], "cancel");
    let with_options = castia::messaging::decision_card_with_options(
        &json!([]),
        "Deploy?",
        &json!({ "version": "1.4", "msteams": { "width": "Full" } }),
    );
    assert_eq!(with_options["content"]["version"], "1.4");
    assert_eq!(with_options["content"]["msteams"]["width"], "Full");
}

#[test]
fn decoration_entities_match_python_wire_shape() {
    let claim = citation(
        2,
        "Quarterly Report",
        "https://example/report",
        "Q3 numbers",
        &json!(["a", "b", "c", "d"]),
        "PDF",
    );
    assert_eq!(
        claim,
        json!({
            "@type": "Claim",
            "position": 2,
            "appearance": {
                "@type": "DigitalDocument",
                "name": "Quarterly Report",
                "url": "https://example/report",
                "abstract": "Q3 numbers",
                "keywords": ["a", "b", "c"],
                "image": { "@type": "ImageObject", "name": "PDF" },
            },
        })
    );

    assert_eq!(
        sensitivity_label("Confidential", "Internal only"),
        json!({ "@type": "CreativeWork", "name": "Confidential", "description": "Internal only" })
    );
    assert_eq!(
        message_entity(
            true,
            &json!([claim]),
            &sensitivity_label("Confidential", "")
        ),
        json!({
            "type": "https://schema.org/Message",
            "@type": "Message",
            "@context": "https://schema.org",
            "@id": "",
            "additionalType": ["AIGeneratedContent"],
            "citation": [{
                "@type": "Claim",
                "position": 2,
                "appearance": {
                    "@type": "DigitalDocument",
                    "name": "Quarterly Report",
                    "url": "https://example/report",
                    "abstract": "Q3 numbers",
                    "keywords": ["a", "b", "c"],
                    "image": { "@type": "ImageObject", "name": "PDF" },
                },
            }],
            "usageInfo": { "@type": "CreativeWork", "name": "Confidential" },
        })
    );
    assert!(message_entity(false, &json!([]), &json!(null)).is_null());
}

#[test]
fn mention_feedback_and_invoke_helpers_match_python() {
    assert_eq!(
        feedback_channel_data("custom"),
        json!({ "feedbackLoop": { "type": "custom" } })
    );
    assert_eq!(
        mention_entity("8:orgid:aad-123", "Ada"),
        json!({
            "type": "mention",
            "mentioned": { "id": "8:orgid:aad-123", "name": "Ada" },
            "text": "<at>Ada</at>",
        })
    );
    assert_eq!(
        message_invoke_response("done"),
        json!({
            "statusCode": 200,
            "type": "application/vnd.microsoft.activity.message",
            "value": "done",
        })
    );
    assert_eq!(
        card_invoke_response(&decision_card(&json!([]), "Resolved")),
        json!({
            "statusCode": 200,
            "type": ADAPTIVE_CARD_CONTENT_TYPE,
            "value": {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.5",
                "body": [{ "type": "TextBlock", "text": "Resolved", "wrap": true, "weight": "Bolder" }],
                "actions": [],
            },
        })
    );
}

#[test]
fn inbound_invoke_payloads_match_python() {
    let feedback = activity(json!({
        "value": {
            "actionName": "feedback",
            "actionValue": { "reaction": "like", "feedback": "{\"feedbackText\":\"nice\"}" },
        },
    }));
    assert_eq!(
        feedback_payload(&feedback),
        json!({ "reaction": "like", "feedback": "{\"feedbackText\":\"nice\"}" })
    );
    assert_eq!(
        feedback_payload(&activity(json!({}))),
        json!({ "reaction": null, "feedback": null })
    );

    let action = activity(json!({
        "value": { "action": { "type": "Action.Execute", "verb": "approve", "data": { "choice": "approve" } } },
    }));
    assert_eq!(
        card_action(&action),
        json!({ "verb": "approve", "data": { "choice": "approve" } })
    );
}

#[test]
fn identity_gate_matches_python() {
    let allowed = activity(json!({
        "recipient": { "role": "agenticUser", "agenticUserId": "11111111-1111-1111-1111-111111111111" },
    }));
    assert_eq!(
        require_agentic_user(&allowed),
        "11111111-1111-1111-1111-111111111111"
    );

    let rejected = activity(json!({ "recipient": { "role": "agenticAppInstance" } }));
    let panic = std::panic::catch_unwind(|| require_agentic_user(&rejected)).unwrap_err();
    let message = panic.downcast_ref::<String>().expect("panic payload");
    assert!(message.contains("recipient.role='agenticAppInstance'"));
    assert_eq!(
        castia::messaging::try_require_agentic_user(&rejected)
            .unwrap_err()
            .to_string(),
        "this action requires the agent's Agentic-User identity (Frontier preview); recipient.role='agenticAppInstance' carries no mailbox"
    );
}

#[test]
fn teams_surface_predicates_match_python() {
    assert!(teams_direct_message(&activity(json!({
        "type": "message",
        "channelId": "msteams:tenant",
        "conversation": { "conversationType": "personal" },
    }))));
    assert!(teams_group_chat_message(&activity(json!({
        "type": "message",
        "channelId": "msteams",
        "conversation": { "conversationType": "groupChat" },
    }))));
    assert!(teams_tagged_channel_message(&activity(json!({
        "type": "message",
        "channelId": "msteams",
        "conversation": { "conversationType": "channel" },
        "recipient": { "id": "28:agent" },
        "entities": [{ "type": "mention", "mentioned": { "id": "28:agent" }, "text": "<at>HAL</at>" }],
    }))));
    assert!(!teams_tagged_channel_message(&activity(json!({
        "type": "message",
        "channelId": "msteams",
        "conversation": { "conversationType": "channel" },
        "recipient": { "id": "28:agent" },
        "entities": [{ "type": "mention", "mentioned": { "id": "29:user" }, "text": "<at>User</at>" }],
    }))));
}

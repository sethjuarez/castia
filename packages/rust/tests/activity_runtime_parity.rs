use castia::model::{
    Activity, ActivityRuntime, ChannelAccount, ConversationAccount, LoadContext, SaveContext,
};
use castia::protocols::CastiaActivityRuntime;

const AGENTIC_IDENTITY: &str = "agenticAppInstance";

#[test]
fn activity_wire_names_match_python_model_aliases() {
    let activity = Activity::from_json(
        r#"{
          "type": "message",
          "serviceUrl": "https://smba.trafficmanager.net/amer/",
          "channelId": "msteams:tenant",
          "from": {"id": "user-1", "aadObjectId": "aad-1"},
          "recipient": {
            "role": "agenticAppInstance",
            "agenticAppId": "app-1",
            "agenticUserId": "agent-user-1"
          },
          "conversation": {"id": "conv-1", "tenantId": "tenant-1"},
          "replyToId": "activity-1"
        }"#,
        &LoadContext::default(),
    )
    .expect("activity loads from Bot Framework camelCase JSON");

    assert_eq!(
        activity.service_url.as_deref(),
        Some("https://smba.trafficmanager.net/amer/")
    );
    assert_eq!(activity.channel_id.as_deref(), Some("msteams:tenant"));
    assert_eq!(
        activity
            .from
            .as_ref()
            .and_then(|from| from.aad_object_id.as_deref()),
        Some("aad-1")
    );
    assert_eq!(activity.reply_to_id.as_deref(), Some("activity-1"));

    let saved = activity.to_value(&SaveContext::default());
    assert_eq!(saved["serviceUrl"], "https://smba.trafficmanager.net/amer/");
    assert_eq!(saved["channelId"], "msteams:tenant");
    assert_eq!(saved["from"]["aadObjectId"], "aad-1");
    assert_eq!(saved["replyToId"], "activity-1");
}

#[test]
fn activity_runtime_helpers_match_python_activity_semantics() {
    let runtime = CastiaActivityRuntime;
    let activity = Activity {
        channel_id: Some("msteams:tenant".to_string()),
        recipient: Some(ChannelAccount {
            role: Some(AGENTIC_IDENTITY.to_string()),
            agentic_app_id: Some("app-1".to_string()),
            agentic_user_id: Some("agent-user-1".to_string()),
            tenant_id: None,
            ..ChannelAccount::default()
        }),
        conversation: Some(ConversationAccount {
            tenant_id: Some("tenant-from-conversation".to_string()),
            ..ConversationAccount::default()
        }),
        entities: serde_json::json!([
            {"type": "Mention", "text": "<at>Castia</at>", "mentioned": {"id": "bot-1"}},
            {"type": "clientInfo", "locale": "en-US"}
        ]),
        ..Activity::default()
    };

    assert_eq!(runtime.channel(&activity).as_deref(), Some("msteams"));
    assert!(runtime.is_agentic_request(&activity));
    assert_eq!(
        runtime.agentic_instance_id(&activity).as_deref(),
        Some("app-1")
    );
    assert_eq!(
        runtime.agentic_user(&activity).as_deref(),
        Some("agent-user-1")
    );

    let empty_agentic_user = Activity {
        recipient: Some(ChannelAccount {
            role: Some("agenticUser".to_string()),
            agentic_user_id: Some(String::new()),
            ..ChannelAccount::default()
        }),
        ..Activity::default()
    };
    assert_eq!(runtime.agentic_user(&empty_agentic_user).as_deref(), Some(""));
    assert_eq!(
        runtime.agentic_tenant_id(&activity).as_deref(),
        Some("tenant-from-conversation")
    );

    let blank_recipient_tenant = Activity {
        recipient: Some(ChannelAccount {
            role: Some(AGENTIC_IDENTITY.to_string()),
            tenant_id: Some(String::new()),
            ..ChannelAccount::default()
        }),
        conversation: Some(ConversationAccount {
            tenant_id: Some("tenant-from-conversation".to_string()),
            ..ConversationAccount::default()
        }),
        ..Activity::default()
    };
    assert_eq!(
        runtime.agentic_tenant_id(&blank_recipient_tenant).as_deref(),
        Some("tenant-from-conversation")
    );

    let blank_conversation_tenant = Activity {
        recipient: Some(ChannelAccount {
            role: Some(AGENTIC_IDENTITY.to_string()),
            ..ChannelAccount::default()
        }),
        conversation: Some(ConversationAccount {
            tenant_id: Some(String::new()),
            ..ConversationAccount::default()
        }),
        ..Activity::default()
    };
    assert_eq!(runtime.agentic_tenant_id(&blank_conversation_tenant), None);

    let mentions = runtime.mentions(&activity);
    assert_eq!(mentions.len(), 1);
    assert_eq!(mentions[0].text.as_deref(), Some("<at>Castia</at>"));
    assert_eq!(
        mentions[0]
            .mentioned
            .as_ref()
            .and_then(|account| account.id.as_deref()),
        Some("bot-1")
    );
}

#[test]
fn activity_runtime_rejects_non_agentic_recipient_roles() {
    let runtime = CastiaActivityRuntime;
    let activity = Activity {
        recipient: Some(ChannelAccount {
            role: Some("bot".to_string()),
            agentic_app_id: Some("app-1".to_string()),
            agentic_user_id: Some("agent-user-1".to_string()),
            tenant_id: Some("tenant-1".to_string()),
            ..ChannelAccount::default()
        }),
        ..Activity::default()
    };

    assert!(!runtime.is_agentic_request(&activity));
    assert_eq!(runtime.agentic_instance_id(&activity), None);
    assert_eq!(runtime.agentic_user(&activity), None);
    assert_eq!(runtime.agentic_tenant_id(&activity), None);
}

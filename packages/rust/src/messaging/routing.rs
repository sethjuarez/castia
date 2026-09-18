use crate::model::{Activity, RoutingRuntime};

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaRoutingRuntime;

#[async_trait::async_trait]
impl RoutingRuntime for CastiaRoutingRuntime {
    fn teams_direct_message(&self, activity: &Activity) -> bool {
        teams_direct_message(activity)
    }

    fn teams_group_chat_message(&self, activity: &Activity) -> bool {
        teams_group_chat_message(activity)
    }

    fn teams_tagged_channel_message(&self, activity: &Activity) -> bool {
        teams_tagged_channel_message(activity)
    }
}

pub fn teams_direct_message(activity: &Activity) -> bool {
    is_message_on(activity, "msteams")
        && activity
            .conversation
            .as_ref()
            .and_then(|conversation| conversation.conversation_type.as_deref())
            == Some("personal")
}

pub fn teams_group_chat_message(activity: &Activity) -> bool {
    is_message_on(activity, "msteams")
        && activity
            .conversation
            .as_ref()
            .and_then(|conversation| conversation.conversation_type.as_deref())
            == Some("groupChat")
}

pub fn teams_tagged_channel_message(activity: &Activity) -> bool {
    is_message_on(activity, "msteams")
        && activity
            .conversation
            .as_ref()
            .and_then(|conversation| conversation.conversation_type.as_deref())
            == Some("channel")
        && agent_is_mentioned(activity)
}

fn is_message_on(activity: &Activity, channel: &str) -> bool {
    activity.r#type.as_deref() == Some("message")
        && normalized_channel(activity.channel_id.as_deref()).as_deref() == Some(channel)
}

fn normalized_channel(channel_id: Option<&str>) -> Option<String> {
    channel_id
        .map(|channel_id| {
            channel_id
                .split_once(':')
                .map_or(channel_id, |(head, _)| head)
        })
        .map(str::trim)
        .filter(|channel| !channel.is_empty())
        .map(str::to_string)
}

fn agent_is_mentioned(activity: &Activity) -> bool {
    let Some(recipient_id) = activity
        .recipient
        .as_ref()
        .and_then(|recipient| recipient.id.as_deref())
    else {
        return false;
    };

    activity
        .entities
        .as_array()
        .into_iter()
        .flatten()
        .any(|entity| {
            entity
                .get("type")
                .and_then(|value| value.as_str())
                .is_some_and(|kind| kind.eq_ignore_ascii_case("mention"))
                && entity
                    .get("mentioned")
                    .and_then(|mentioned| mentioned.get("id"))
                    .and_then(|id| id.as_str())
                    == Some(recipient_id)
        })
}

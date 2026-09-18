use crate::model::{Activity, ActivityRuntime, LoadContext, Mention};

const AGENTIC_IDENTITY: &str = "agenticAppInstance";
const AGENTIC_USER: &str = "agenticUser";

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaActivityRuntime;

#[async_trait::async_trait]
impl ActivityRuntime for CastiaActivityRuntime {
    fn agentic_instance_id(&self, activity: &Activity) -> Option<String> {
        if !self.is_agentic_request(activity) {
            return None;
        }
        activity
            .recipient
            .as_ref()
            .and_then(|recipient| recipient.agentic_app_id.clone())
    }

    fn agentic_tenant_id(&self, activity: &Activity) -> Option<String> {
        if !self.is_agentic_request(activity) {
            return None;
        }
        activity
            .recipient
            .as_ref()
            .and_then(|recipient| recipient.tenant_id.clone())
            .filter(|tenant_id| !tenant_id.is_empty())
            .or_else(|| {
                activity
                    .conversation
                    .as_ref()
                    .and_then(|conversation| conversation.tenant_id.clone())
                    .filter(|tenant_id| !tenant_id.is_empty())
            })
    }

    fn agentic_user(&self, activity: &Activity) -> Option<String> {
        if !self.is_agentic_request(activity) {
            return None;
        }
        activity
            .recipient
            .as_ref()
            .and_then(|recipient| recipient.agentic_user_id.clone())
    }

    fn channel(&self, activity: &Activity) -> Option<String> {
        activity
            .channel_id
            .as_ref()
            .map(|channel_id| {
                channel_id
                    .split_once(':')
                    .map_or(channel_id.as_str(), |(head, _)| head)
            })
            .map(str::trim)
            .filter(|channel| !channel.is_empty())
            .map(str::to_string)
    }

    fn is_agentic_request(&self, activity: &Activity) -> bool {
        matches!(
            activity
                .recipient
                .as_ref()
                .and_then(|recipient| recipient.role.as_deref()),
            Some(AGENTIC_IDENTITY | AGENTIC_USER)
        )
    }

    fn mentions(&self, activity: &Activity) -> Vec<Mention> {
        let Some(entities) = activity.entities.as_array() else {
            return Vec::new();
        };

        entities
            .iter()
            .filter(|entity| {
                entity
                    .get("type")
                    .and_then(|value| value.as_str())
                    .is_some_and(|kind| kind.eq_ignore_ascii_case("mention"))
            })
            .map(|entity| Mention::load_from_value(entity, &LoadContext::default()))
            .collect()
    }
}

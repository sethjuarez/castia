use crate::model::{Activity, IdentityRuntime};
use std::error::Error;
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgenticIdentityError {
    message: String,
}

impl fmt::Display for AgenticIdentityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl Error for AgenticIdentityError {}

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaIdentityRuntime;

#[async_trait::async_trait]
impl IdentityRuntime for CastiaIdentityRuntime {
    fn agentic_user_id(&self, activity: &Activity) -> Option<String> {
        agentic_user_id(activity)
    }

    fn require_agentic_user(&self, activity: &Activity) -> String {
        require_agentic_user(activity)
    }
}

pub fn agentic_user_id(activity: &Activity) -> Option<String> {
    let recipient = activity.recipient.as_ref()?;
    if recipient.role.as_deref() != Some("agenticUser") {
        return None;
    }
    recipient
        .agentic_user_id
        .as_ref()
        .filter(|value| !value.is_empty())
        .cloned()
}

pub fn require_agentic_user(activity: &Activity) -> String {
    try_require_agentic_user(activity).unwrap_or_else(|error| panic!("{error}"))
}

pub fn try_require_agentic_user(activity: &Activity) -> Result<String, AgenticIdentityError> {
    if let Some(user_id) = agentic_user_id(activity) {
        return Ok(user_id);
    }
    let role = activity
        .recipient
        .as_ref()
        .and_then(|recipient| recipient.role.as_deref());
    let role_text = role
        .map(|value| format!("'{value}'"))
        .unwrap_or_else(|| "None".to_string());
    Err(AgenticIdentityError {
        message: format!(
            "this action requires the agent's Agentic-User identity (Frontier preview); recipient.role={role_text} carries no mailbox"
        ),
    })
}

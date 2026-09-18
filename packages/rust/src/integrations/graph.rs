use crate::model::IntegrationsGraphRuntime;
use percent_encoding::{utf8_percent_encode, AsciiSet, CONTROLS};
use serde_json::{json, Value};

const GRAPH_MESSAGES: &str = "https://graph.microsoft.com/v1.0/me/messages";
const GRAPH_DRIVE_ROOT: &str = "https://graph.microsoft.com/v1.0/me/drive/root:/{path}:/content";
const GRAPH_REPLY_PREFIX: &str = "https://graph.microsoft.com/v1.0/me/messages/";
const SELECT: &str = "id,from,subject,receivedDateTime,isRead,bodyPreview";
const QUERY: &AsciiSet = &CONTROLS.add(b' ').add(b'$').add(b',').add(b':');
const PATH_SEGMENT: &AsciiSet = &CONTROLS
    .add(b' ')
    .add(b'"')
    .add(b'#')
    .add(b'%')
    .add(b'&')
    .add(b'+')
    .add(b':')
    .add(b',')
    .add(b'=')
    .add(b'@')
    .add(b'!')
    .add(b'(')
    .add(b')')
    .add(b'\'')
    .add(b';')
    .add(b'*')
    .add(b'$')
    .add(b'[')
    .add(b']')
    .add(b'\\')
    .add(b'^')
    .add(b'|')
    .add(b'<')
    .add(b'>')
    .add(b'?')
    .add(b'`')
    .add(b'{')
    .add(b'}');

#[derive(Debug, Default, Clone, Copy)]
pub struct CastiaIntegrationsGraphRuntime;

#[async_trait::async_trait]
impl IntegrationsGraphRuntime for CastiaIntegrationsGraphRuntime {
    fn drive_share_invite_payload(&self, requester_object_id: &String) -> Value {
        drive_share_invite_payload(requester_object_id)
    }

    fn drive_share_result(
        &self,
        item_id: &String,
        requester_object_id: &String,
        status: &i32,
        detail: &String,
    ) -> Value {
        drive_share_result(item_id, requester_object_id, *status, detail)
    }

    fn drive_upload_result(
        &self,
        status: &i32,
        granted_scopes: &String,
        web_url: &String,
        shared: &Value,
        detail: &String,
    ) -> Value {
        drive_upload_result(*status, granted_scopes, web_url, shared, detail)
    }

    fn drive_upload_url(&self, path: &String) -> String {
        drive_upload_url(path)
    }

    fn mailbox_messages_url(&self, top: &i32, unread_only: &bool) -> String {
        mailbox_messages_url(*top, *unread_only)
    }

    fn reply_mail_request(&self, message_id: &String, body: &String) -> Value {
        reply_mail_request(message_id, body)
    }

    fn reply_mail_result(&self, status: &i32, granted_scopes: &String, detail: &String) -> Value {
        reply_mail_result(*status, granted_scopes, detail)
    }

    fn send_mail_payload(&self, to: &String, subject: &String, body: &String) -> Value {
        send_mail_payload(to, subject, body)
    }

    fn send_mail_result(&self, status: &i32, granted_scopes: &String, detail: &String) -> Value {
        send_mail_result(*status, granted_scopes, detail)
    }

    fn summarize_mailbox_message(&self, message: &Value) -> Value {
        summarize_mailbox_message(message)
    }
}

pub fn mailbox_messages_url(top: i32, unread_only: bool) -> String {
    let top = top.clamp(1, 25);
    let mut params = vec![
        format!("%24top={top}"),
        format!("%24select={}", encode_query(SELECT)),
        "%24orderby=receivedDateTime+desc".to_string(),
    ];
    if unread_only {
        params.push("%24filter=isRead+eq+false".to_string());
    }
    format!("{GRAPH_MESSAGES}?{}", params.join("&"))
}

pub fn summarize_mailbox_message(message: &Value) -> Value {
    let object = message.as_object();
    let sender = object
        .and_then(|object| object.get("from"))
        .and_then(|value| value.get("emailAddress"))
        .and_then(|value| value.get("address"))
        .and_then(Value::as_str)
        .unwrap_or("<unknown>");
    let preview = object
        .and_then(|object| object.get("bodyPreview"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .chars()
        .take(200)
        .collect::<String>();
    json!({
        "id": string_field(object, "id", ""),
        "from": sender,
        "subject": string_field(object, "subject", "(no subject)"),
        "received": string_field(object, "receivedDateTime", ""),
        "unread": !object
            .and_then(|object| object.get("isRead"))
            .and_then(Value::as_bool)
            .unwrap_or(true),
        "preview": preview,
    })
}

pub fn send_mail_payload(to: &str, subject: &str, body: &str) -> Value {
    json!({
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        },
        "saveToSentItems": true,
    })
}

pub fn send_mail_result(status: i32, granted_scopes: &str, detail: &str) -> Value {
    let ok = status == 202;
    diagnostic("send", ok, status, granted_scopes, detail, 500)
}

pub fn reply_mail_request(message_id: &str, body: &str) -> Value {
    json!({
        "url": format!("{GRAPH_REPLY_PREFIX}{message_id}/reply"),
        "payload": {"comment": body},
    })
}

pub fn reply_mail_result(status: i32, granted_scopes: &str, detail: &str) -> Value {
    let ok = status == 200 || status == 202;
    diagnostic("reply", ok, status, granted_scopes, detail, 500)
}

pub fn drive_upload_url(path: &str) -> String {
    let encoded = path
        .trim_matches('/')
        .split('/')
        .map(|segment| utf8_percent_encode(segment, PATH_SEGMENT).to_string())
        .collect::<Vec<_>>()
        .join("/");
    GRAPH_DRIVE_ROOT.replace("{path}", &encoded)
}

pub fn drive_share_invite_payload(requester_object_id: &str) -> Value {
    json!({
        "recipients": [{"objectId": requester_object_id}],
        "requireSignIn": true,
        "sendInvitation": true,
        "roles": ["write"],
        "message": "Sharing the document you asked me to create.",
    })
}

pub fn drive_upload_result(
    status: i32,
    granted_scopes: &str,
    web_url: &str,
    shared: &Value,
    detail: &str,
) -> Value {
    let ok = status == 200 || status == 201;
    if ok {
        json!({
            "ok": true,
            "stage": "upload",
            "status": status,
            "granted_scopes": granted_scopes,
            "web_url": web_url,
            "shared": shared,
            "detail": "",
        })
    } else {
        json!({
            "ok": false,
            "stage": "upload",
            "status": status,
            "granted_scopes": granted_scopes,
            "web_url": "",
            "detail": truncate_chars(detail, 500),
        })
    }
}

pub fn drive_share_result(
    item_id: &str,
    requester_object_id: &str,
    status: i32,
    detail: &str,
) -> Value {
    if item_id.is_empty() {
        return json!({"ok": false, "detail": "no item id returned from upload"});
    }
    if requester_object_id.is_empty() {
        return json!({"ok": false, "detail": "no requester object id on the activity"});
    }
    let ok = status == 200 || status == 201;
    json!({
        "ok": ok,
        "status": status,
        "recipient": requester_object_id,
        "role": "write",
        "detail": if ok { String::new() } else { truncate_chars(detail, 300) },
    })
}

fn diagnostic(
    stage: &str,
    ok: bool,
    status: i32,
    granted_scopes: &str,
    detail: &str,
    max: usize,
) -> Value {
    json!({
        "ok": ok,
        "stage": stage,
        "status": status,
        "granted_scopes": granted_scopes,
        "detail": if ok { String::new() } else { truncate_chars(detail, max) },
    })
}

fn string_field(
    object: Option<&serde_json::Map<String, Value>>,
    key: &str,
    default: &str,
) -> String {
    object
        .and_then(|object| object.get(key))
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

fn truncate_chars(value: &str, max: usize) -> String {
    value.chars().take(max).collect()
}

fn encode_query(value: &str) -> String {
    utf8_percent_encode(value, QUERY).to_string()
}

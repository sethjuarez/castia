use castia::integrations::{
    drive_share_invite_payload, drive_share_result, drive_upload_result, drive_upload_url,
    mailbox_messages_url, reply_mail_request, reply_mail_result, send_mail_payload,
    send_mail_result, summarize_mailbox_message,
};
use serde_json::json;

#[test]
fn mailbox_query_clamps_and_filters_like_python() {
    assert_eq!(
        mailbox_messages_url(99, false),
        "https://graph.microsoft.com/v1.0/me/messages?%24top=25&%24select=id%2Cfrom%2Csubject%2CreceivedDateTime%2CisRead%2CbodyPreview&%24orderby=receivedDateTime+desc"
    );
    assert_eq!(
        mailbox_messages_url(0, true),
        "https://graph.microsoft.com/v1.0/me/messages?%24top=1&%24select=id%2Cfrom%2Csubject%2CreceivedDateTime%2CisRead%2CbodyPreview&%24orderby=receivedDateTime+desc&%24filter=isRead+eq+false"
    );
}

#[test]
fn mailbox_summary_flattens_graph_messages() {
    assert_eq!(
        summarize_mailbox_message(&json!({
            "id": "m1",
            "from": {"emailAddress": {"address": "sender@example.com"}},
            "subject": "Hi",
            "receivedDateTime": "2026-01-02T03:04:05Z",
            "isRead": false,
            "bodyPreview": "  hello inbox  ",
        })),
        json!({
            "id": "m1",
            "from": "sender@example.com",
            "subject": "Hi",
            "received": "2026-01-02T03:04:05Z",
            "unread": true,
            "preview": "hello inbox",
        })
    );
    assert_eq!(
        summarize_mailbox_message(&json!({})),
        json!({
            "id": "",
            "from": "<unknown>",
            "subject": "(no subject)",
            "received": "",
            "unread": false,
            "preview": "",
        })
    );
}

#[test]
fn mail_payloads_and_results_match_graph_actions() {
    assert_eq!(
        send_mail_payload("person@example.com", "Status", "Done."),
        json!({
            "message": {
                "subject": "Status",
                "body": {"contentType": "Text", "content": "Done."},
                "toRecipients": [{"emailAddress": {"address": "person@example.com"}}],
            },
            "saveToSentItems": true,
        })
    );
    assert_eq!(
        send_mail_result(202, "Mail.Send", "ignored"),
        json!({"ok": true, "stage": "send", "status": 202, "granted_scopes": "Mail.Send", "detail": ""})
    );
    assert_eq!(
        send_mail_result(200, "Mail.Send", "not sendMail accepted"),
        json!({"ok": false, "stage": "send", "status": 200, "granted_scopes": "Mail.Send", "detail": "not sendMail accepted"})
    );
    assert_eq!(
        reply_mail_request("abc 123", "Thanks"),
        json!({
            "url": "https://graph.microsoft.com/v1.0/me/messages/abc 123/reply",
            "payload": {"comment": "Thanks"},
        })
    );
    assert_eq!(
        reply_mail_result(200, "Mail.ReadWrite Mail.Send", "ignored"),
        json!({"ok": true, "stage": "reply", "status": 200, "granted_scopes": "Mail.ReadWrite Mail.Send", "detail": ""})
    );
    assert_eq!(
        reply_mail_result(202, "Mail.ReadWrite Mail.Send", "ignored"),
        json!({"ok": true, "stage": "reply", "status": 202, "granted_scopes": "Mail.ReadWrite Mail.Send", "detail": ""})
    );
}

#[test]
fn drive_upload_and_share_helpers_match_python() {
    assert_eq!(
        drive_upload_url("/Reports/Q1 Plan #1.md"),
        "https://graph.microsoft.com/v1.0/me/drive/root:/Reports/Q1%20Plan%20%231.md:/content"
    );
    assert_eq!(
        drive_upload_url("/Reports/100% done: Sales & Marketing (final).md"),
        "https://graph.microsoft.com/v1.0/me/drive/root:/Reports/100%25%20done%3A%20Sales%20%26%20Marketing%20%28final%29.md:/content"
    );
    assert_eq!(
        drive_share_invite_payload("user-123"),
        json!({
            "recipients": [{"objectId": "user-123"}],
            "requireSignIn": true,
            "sendInvitation": true,
            "roles": ["write"],
            "message": "Sharing the document you asked me to create.",
        })
    );
    assert_eq!(
        drive_upload_result(
            201,
            "Files.ReadWrite",
            "https://share/doc",
            &json!({"ok": true}),
            "ignored"
        ),
        json!({
            "ok": true,
            "stage": "upload",
            "status": 201,
            "granted_scopes": "Files.ReadWrite",
            "web_url": "https://share/doc",
            "shared": {"ok": true},
            "detail": "",
        })
    );
    assert_eq!(
        drive_share_result("", "user-123", 201, "ignored"),
        json!({"ok": false, "detail": "no item id returned from upload"})
    );
    assert_eq!(
        drive_share_result("item-1", "", 201, "ignored"),
        json!({"ok": false, "detail": "no requester object id on the activity"})
    );
}

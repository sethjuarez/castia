namespace Castia.Spec.Integrations;

@doc("Pure Microsoft Graph integration helper contracts. Token minting and HTTP execution remain host-specific.")
interface IntegrationsGraphRuntime {
  @sync
  @vector(#{
    name: "mailbox-url-clamps-top-and-selects-newest",
    stage: "callable",
    input: #{ top: 99, unreadOnly: false },
    expected: "https://graph.microsoft.com/v1.0/me/messages?%24top=25&%24select=id%2Cfrom%2Csubject%2CreceivedDateTime%2CisRead%2CbodyPreview&%24orderby=receivedDateTime+desc",
  })
  @vector(#{
    name: "mailbox-url-adds-unread-filter",
    stage: "callable",
    input: #{ top: 0, unreadOnly: true },
    expected: "https://graph.microsoft.com/v1.0/me/messages?%24top=1&%24select=id%2Cfrom%2Csubject%2CreceivedDateTime%2CisRead%2CbodyPreview&%24orderby=receivedDateTime+desc&%24filter=isRead+eq+false",
  })
  mailboxMessagesUrl(top: int32, unreadOnly: boolean): string;

  @sync
  @vector(#{
    name: "mailbox-message-summary-flattens-defaults",
    stage: "callable",
    input: #{ message: #{ id: "m1", from: #{ emailAddress: #{ address: "sender@example.com" } }, subject: "Hi", receivedDateTime: "2026-01-02T03:04:05Z", isRead: false, bodyPreview: "  hello inbox  " } },
    expected: #{ id: "m1", from: "sender@example.com", subject: "Hi", received: "2026-01-02T03:04:05Z", unread: true, preview: "hello inbox" },
  })
  @vector(#{
    name: "mailbox-message-summary-safe-defaults",
    stage: "callable",
    input: #{ message: #{} },
    expected: #{ id: "", from: "<unknown>", subject: "(no subject)", received: "", unread: false, preview: "" },
  })
  summarizeMailboxMessage(message: unknown): unknown;

  @sync
  @vector(#{
    name: "mail-send-payload-shape",
    stage: "callable",
    input: #{ to: "person@example.com", subject: "Status", body: "Done." },
    expected: #{ message: #{ subject: "Status", body: #{ contentType: "Text", content: "Done." }, toRecipients: #[#{ emailAddress: #{ address: "person@example.com" } }] }, saveToSentItems: true },
  })
  sendMailPayload(to: string, subject: string, body: string): unknown;

  @sync
  @vector(#{
    name: "mail-send-result-accepted",
    stage: "callable",
    input: #{ status: 202, grantedScopes: "Mail.Send", detail: "ignored" },
    expected: #{ ok: true, stage: "send", status: 202, granted_scopes: "Mail.Send", detail: "" },
  })
  @vector(#{
    name: "mail-send-result-error-truncates-detail",
    stage: "callable",
    input: #{ status: 403, grantedScopes: "Mail.Read", detail: "abcdefghijklmnopqrstuvwxyz" },
    expected: #{ ok: false, stage: "send", status: 403, granted_scopes: "Mail.Read", detail: "abcdefghijklmnopqrstuvwxyz" },
  })
  @vector(#{
    name: "mail-send-result-200-is-not-accepted",
    stage: "callable",
    input: #{ status: 200, grantedScopes: "Mail.Send", detail: "not sendMail accepted" },
    expected: #{ ok: false, stage: "send", status: 200, granted_scopes: "Mail.Send", detail: "not sendMail accepted" },
  })
  sendMailResult(status: int32, grantedScopes: string, detail: string): unknown;

  @sync
  @vector(#{
    name: "mail-reply-url-and-payload",
    stage: "callable",
    input: #{ messageId: "abc 123", body: "Thanks" },
    expected: #{ url: "https://graph.microsoft.com/v1.0/me/messages/abc 123/reply", payload: #{ comment: "Thanks" } },
  })
  replyMailRequest(messageId: string, body: string): unknown;

  @sync
  @vector(#{
    name: "mail-reply-result-ok-for-200",
    stage: "callable",
    input: #{ status: 200, grantedScopes: "Mail.ReadWrite Mail.Send", detail: "ignored" },
    expected: #{ ok: true, stage: "reply", status: 200, granted_scopes: "Mail.ReadWrite Mail.Send", detail: "" },
  })
  @vector(#{
    name: "mail-reply-result-ok-for-202",
    stage: "callable",
    input: #{ status: 202, grantedScopes: "Mail.ReadWrite Mail.Send", detail: "ignored" },
    expected: #{ ok: true, stage: "reply", status: 202, granted_scopes: "Mail.ReadWrite Mail.Send", detail: "" },
  })
  @vector(#{
    name: "mail-reply-result-error",
    stage: "callable",
    input: #{ status: 404, grantedScopes: "Mail.ReadWrite", detail: "missing message" },
    expected: #{ ok: false, stage: "reply", status: 404, granted_scopes: "Mail.ReadWrite", detail: "missing message" },
  })
  replyMailResult(status: int32, grantedScopes: string, detail: string): unknown;

  @sync
  @vector(#{
    name: "drive-upload-url-encodes-path-segments",
    stage: "callable",
    input: #{ path: "/Reports/Q1 Plan #1.md" },
    expected: "https://graph.microsoft.com/v1.0/me/drive/root:/Reports/Q1%20Plan%20%231.md:/content",
  })
  @vector(#{
    name: "drive-upload-url-encodes-python-quote-reserved-chars",
    stage: "callable",
    input: #{ path: "/Reports/100% done: Sales & Marketing (final).md" },
    expected: "https://graph.microsoft.com/v1.0/me/drive/root:/Reports/100%25%20done%3A%20Sales%20%26%20Marketing%20%28final%29.md:/content",
  })
  driveUploadUrl(path: string): string;

  @sync
  @vector(#{
    name: "drive-share-invite-payload",
    stage: "callable",
    input: #{ requesterObjectId: "user-123" },
    expected: #{ recipients: #[#{ objectId: "user-123" }], requireSignIn: true, sendInvitation: true, roles: #["write"], message: "Sharing the document you asked me to create." },
  })
  driveShareInvitePayload(requesterObjectId: string): unknown;

  @sync
  @vector(#{
    name: "drive-upload-result-success",
    stage: "callable",
    input: #{ status: 201, grantedScopes: "Files.ReadWrite", webUrl: "https://share/doc", shared: #{ ok: true }, detail: "ignored" },
    expected: #{ ok: true, stage: "upload", status: 201, granted_scopes: "Files.ReadWrite", web_url: "https://share/doc", shared: #{ ok: true }, detail: "" },
  })
  @vector(#{
    name: "drive-upload-result-error-truncates-detail",
    stage: "callable",
    input: #{ status: 507, grantedScopes: "Files.ReadWrite", webUrl: "https://share/doc", shared: #{ ok: true }, detail: "quota exceeded" },
    expected: #{ ok: false, stage: "upload", status: 507, granted_scopes: "Files.ReadWrite", web_url: "", detail: "quota exceeded" },
  })
  driveUploadResult(status: int32, grantedScopes: string, webUrl: string, shared: unknown, detail: string): unknown;

  @sync
  @vector(#{
    name: "drive-share-result-missing-item",
    stage: "callable",
    input: #{ itemId: "", requesterObjectId: "user-123", status: 201, detail: "ignored" },
    expected: #{ ok: false, detail: "no item id returned from upload" },
  })
  @vector(#{
    name: "drive-share-result-success",
    stage: "callable",
    input: #{ itemId: "item-1", requesterObjectId: "user-123", status: 201, detail: "ignored" },
    expected: #{ ok: true, status: 201, recipient: "user-123", role: "write", detail: "" },
  })
  @vector(#{
    name: "drive-share-result-missing-requester",
    stage: "callable",
    input: #{ itemId: "item-1", requesterObjectId: "", status: 201, detail: "ignored" },
    expected: #{ ok: false, detail: "no requester object id on the activity" },
  })
  @vector(#{
    name: "drive-share-result-error",
    stage: "callable",
    input: #{ itemId: "item-1", requesterObjectId: "user-123", status: 403, detail: "forbidden" },
    expected: #{ ok: false, status: 403, recipient: "user-123", role: "write", detail: "forbidden" },
  })
  driveShareResult(itemId: string, requesterObjectId: string, status: int32, detail: string): unknown;
}

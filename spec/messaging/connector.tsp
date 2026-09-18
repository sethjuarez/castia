namespace Castia.Spec.Messaging;

@doc("Pure Bot Framework connector request builders. HTTP and authorization stay outside the seam.")
interface ConnectorRuntime {
  @sync
  @vector(#{
    name: "connector-endpoint-trims-service-url",
    stage: "callable",
    input: #{ activity: #{ serviceUrl: "https://smba.trafficmanager.net/teams/", conversation: #{ id: "19:meeting_abc@thread.v2" } } },
    expected: #{ service_url: "https://smba.trafficmanager.net/teams", conversation_id: "19:meeting_abc@thread.v2" },
  })
  @vector(#{
    name: "connector-endpoint-missing-service-url-is-null",
    stage: "callable",
    input: #{ activity: #{ conversation: #{ id: "chat" } } },
    expected: null,
  })
  connectorEndpoint(activity: Castia.Spec.Protocols.Activity): unknown;

  @sync
  @vector(#{
    name: "connector-envelope-swaps-from-recipient",
    stage: "callable",
    input: #{
      activity: #{ id: "turn", conversation: #{ id: "chat" }, from: #{ id: "29:user", name: "User" }, recipient: #{ id: "28:agent", name: "HAL" } },
    },
    expected: #{ from: #{ id: "28:agent", name: "HAL" }, recipient: #{ id: "29:user", name: "User" }, conversation: #{ id: "chat" }, replyToId: "turn" },
  })
  @vector(#{
    name: "connector-envelope-null-reply-to-id",
    stage: "callable",
    input: #{
      activity: #{ conversation: #{ id: "chat" }, from: #{ id: "29:user" }, recipient: #{ id: "28:agent" } },
    },
    expected: #{ from: #{ id: "28:agent" }, recipient: #{ id: "29:user" }, conversation: #{ id: "chat" }, replyToId: null },
  })
  connectorEnvelope(activity: Castia.Spec.Protocols.Activity): unknown;

  @sync
  @vector(#{
    name: "post-activity-request-merges-envelope-and-payload",
    stage: "callable",
    input: #{
      activity: #{ id: "turn", serviceUrl: "https://smba.trafficmanager.net/teams", conversation: #{ id: "chat" }, from: #{ id: "29:user" }, recipient: #{ id: "28:agent" } },
      payload: #{ type: "message", text: "hi there" },
    },
    expected: #{ method: "POST", url: "https://smba.trafficmanager.net/teams/v3/conversations/chat/activities", json: #{ from: #{ id: "28:agent" }, recipient: #{ id: "29:user" }, conversation: #{ id: "chat" }, replyToId: "turn", type: "message", text: "hi there" } },
  })
  @vector(#{
    name: "post-activity-request-missing-service-url-is-null",
    stage: "callable",
    input: #{
      activity: #{ id: "turn", conversation: #{ id: "chat" }, from: #{ id: "29:user" }, recipient: #{ id: "28:agent" } },
      payload: #{ type: "typing" },
    },
    expected: null,
  })
  postActivityRequest(activity: Castia.Spec.Protocols.Activity, payload: unknown): unknown;

  @sync
  @vector(#{
    name: "typing-request-has-no-text",
    stage: "callable",
    input: #{ activity: #{ id: "turn", serviceUrl: "https://s", conversation: #{ id: "chat" }, recipient: #{ id: "bot" }, from: #{ id: "user" } } },
    expected: #{ method: "POST", url: "https://s/v3/conversations/chat/activities", json: #{ from: #{ id: "bot" }, recipient: #{ id: "user" }, conversation: #{ id: "chat" }, replyToId: "turn", type: "typing" } },
  })
  typingRequest(activity: Castia.Spec.Protocols.Activity): unknown;

  @sync
  @vector(#{
    name: "reaction-request-url-encodes-activity-and-reaction",
    stage: "callable",
    input: #{ activity: #{ id: "turn 1", serviceUrl: "https://s/", conversation: #{ id: "chat" } }, reactionType: "white check", method: "PUT" },
    expected: #{ method: "PUT", url: "https://s/v3/conversations/chat/activities/turn%201/reactions/white%20check", json: null },
  })
  reactionRequest(activity: Castia.Spec.Protocols.Activity, reactionType: string, method: string): unknown;

  @sync
  @vector(#{
    name: "update-request-includes-id-and-payload",
    stage: "callable",
    input: #{ activity: #{ id: "turn", serviceUrl: "https://s", conversation: #{ id: "chat" }, recipient: #{ id: "bot" }, from: #{ id: "user" } }, activityId: "edit-me", payload: #{ type: "message", text: "v2" } },
    expected: #{ method: "PUT", url: "https://s/v3/conversations/chat/activities/edit-me", json: #{ from: #{ id: "bot" }, recipient: #{ id: "user" }, conversation: #{ id: "chat" }, replyToId: "turn", id: "edit-me", type: "message", text: "v2" } },
  })
  updateActivityRequest(activity: Castia.Spec.Protocols.Activity, activityId: string, payload: unknown): unknown;

  @sync
  @vector(#{
    name: "delete-request-uses-activity-id",
    stage: "callable",
    input: #{ activity: #{ serviceUrl: "https://s", conversation: #{ id: "chat" } }, activityId: "drop-me" },
    expected: #{ method: "DELETE", url: "https://s/v3/conversations/chat/activities/drop-me", json: null },
  })
  deleteActivityRequest(activity: Castia.Spec.Protocols.Activity, activityId: string): unknown;

  @sync
  @vector(#{
    name: "created-id-from-success-body",
    stage: "callable",
    input: #{ response: #{ statusCode: 201, body: #{ id: "created-id" } } },
    expected: "created-id",
  })
  @vector(#{
    name: "created-id-from-error-is-null",
    stage: "callable",
    input: #{ response: #{ statusCode: 403, body: #{ id: "ignored" } } },
    expected: null,
  })
  createdActivityId(response: unknown): string | null;

  @sync
  @vector(#{
    name: "connector-ok-success",
    stage: "callable",
    input: #{ response: #{ statusCode: 204 } },
    expected: true,
  })
  @vector(#{
    name: "connector-ok-null-is-false",
    stage: "callable",
    input: #{ response: null },
    expected: false,
  })
  connectorOk(response: unknown): boolean;
}

namespace Castia.Spec.Messaging;

@doc("Pure Teams surface predicates for Activity routing.")
interface RoutingRuntime {
  @sync
  @vector(#{
    name: "teams-direct",
    stage: "callable",
    input: #{ activity: #{ type: "message", channelId: "msteams", conversation: #{ conversationType: "personal" } } },
    expected: true,
  })
  @vector(#{
    name: "teams-direct-channel-suffix",
    stage: "callable",
    input: #{ activity: #{ type: "message", channelId: "msteams:tenant", conversation: #{ conversationType: "personal" } } },
    expected: true,
  })
  @vector(#{
    name: "direct-rejects-non-teams",
    stage: "callable",
    input: #{ activity: #{ type: "message", channelId: "slack", conversation: #{ conversationType: "personal" } } },
    expected: false,
  })
  teamsDirectMessage(activity: Castia.Spec.Protocols.Activity): boolean;

  @sync
  @vector(#{
    name: "teams-group",
    stage: "callable",
    input: #{ activity: #{ type: "message", channelId: "msteams", conversation: #{ conversationType: "groupChat" } } },
    expected: true,
  })
  teamsGroupChatMessage(activity: Castia.Spec.Protocols.Activity): boolean;

  @sync
  @vector(#{
    name: "teams-channel-mention",
    stage: "callable",
    input: #{
      activity: #{
        type: "message",
        channelId: "msteams",
        conversation: #{ conversationType: "channel" },
        recipient: #{ id: "28:agent" },
        entities: #[#{ type: "mention", mentioned: #{ id: "28:agent" }, text: "<at>HAL</at>" }],
      },
    },
    expected: true,
  })
  @vector(#{
    name: "teams-channel-without-mention",
    stage: "callable",
    input: #{
      activity: #{
        type: "message",
        channelId: "msteams",
        conversation: #{ conversationType: "channel" },
        recipient: #{ id: "28:agent" },
        entities: #[#{ type: "mention", mentioned: #{ id: "29:user" }, text: "<at>User</at>" }],
      },
    },
    expected: false,
  })
  teamsTaggedChannelMessage(activity: Castia.Spec.Protocols.Activity): boolean;
}

namespace Castia.Spec.Protocols;

@doc("A user, agent, or app instance on an Activity channel.")
model ChannelAccount {
  id?: string;
  name?: string;
  aadObjectId?: string;
  role?: string;
  agenticUserId?: string;
  agenticAppId?: string;
  tenantId?: string;
}

@doc("The conversation an Activity belongs to.")
model ConversationAccount {
  id?: string;
  name?: string;
  conversationType?: string;
  tenantId?: string;
  isGroup?: boolean;
  aadObjectId?: string;
  role?: string;
}

@doc("A mention entity embedded in an Activity.")
model Mention {
  type?: string;
  mentioned?: ChannelAccount;
  text?: string;
}

@doc("Bot Framework Activity fields Castia consumes from Teams and Foundry turns.")
model Activity {
  type?: string;
  id?: string;
  text?: string;
  serviceUrl?: string;
  channelId?: string;
  from?: ChannelAccount;
  recipient?: ChannelAccount;
  conversation?: ConversationAccount;
  replyToId?: string;
  entities?: Record<unknown>[];
  channelData?: Record<unknown>;
  name?: string;
  value?: unknown;
}

@doc("Portable semantic helpers every Activity runtime must provide.")
interface ActivityRuntime {
  @sync
  @vector(#{
    name: "channel-with-suffix",
    stage: "callable",
    input: #{ activity: #{ channelId: "msteams:tenant" } },
    expected: "msteams",
  })
  @vector(#{
    name: "channel-without-suffix",
    stage: "callable",
    input: #{ activity: #{ channelId: "msteams" } },
    expected: "msteams",
  })
  @vector(#{
    name: "channel-empty-after-trim",
    stage: "callable",
    input: #{ activity: #{ channelId: "  " } },
    expected: null,
  })
  @doc("Return the channel id without any channel-specific suffix.")
  channel(activity: Activity): string | null;

  @sync
  @vector(#{
    name: "mention-entities-only",
    stage: "callable",
    input: #{
      activity: #{
        entities: #[
          #{ type: "Mention", text: "<at>Castia</at>", mentioned: #{ id: "bot-1" } },
          #{ type: "clientInfo" },
        ],
      },
    },
    expected: #[#{ type: "Mention", text: "<at>Castia</at>", mentioned: #{ id: "bot-1" } }],
  })
  @vector(#{
    name: "mention-lowercase-entity",
    stage: "callable",
    input: #{
      activity: #{
        entities: #[#{ type: "mention", text: "<at>Castia</at>", mentioned: #{ id: "bot-1" } }],
      },
    },
    expected: #[#{ type: "mention", text: "<at>Castia</at>", mentioned: #{ id: "bot-1" } }],
  })
  @vector(#{
    name: "mention-absent-entities",
    stage: "callable",
    input: #{ activity: #{} },
    expected: #[],
  })
  @doc("Return only mention entities from an Activity payload.")
  mentions(activity: Activity): Mention[];

  @sync
  @vector(#{
    name: "agentic-recipient-role",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "agenticAppInstance" } } },
    expected: true,
  })
  @vector(#{
    name: "agentic-user-recipient-role",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "agenticUser" } } },
    expected: true,
  })
  @vector(#{
    name: "bot-recipient-role",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "bot" } } },
    expected: false,
  })
  @vector(#{
    name: "missing-recipient-role",
    stage: "callable",
    input: #{ activity: #{} },
    expected: false,
  })
  @doc("Whether the Activity recipient represents a Foundry agentic identity.")
  isAgenticRequest(activity: Activity): boolean;

  @sync
  @vector(#{
    name: "agentic-instance-id",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticAppInstance", agenticAppId: "app-1" },
      },
    },
    expected: "app-1",
  })
  @vector(#{
    name: "non-agentic-instance-id",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "bot", agenticAppId: "app-1" } } },
    expected: null,
  })
  @doc("Return recipient.agenticAppId when the Activity is agentic.")
  agenticInstanceId(activity: Activity): string | null;

  @sync
  @vector(#{
    name: "agentic-user-id",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticUser", agenticUserId: "agent-user-1" },
      },
    },
    expected: "agent-user-1",
  })
  @vector(#{
    name: "non-agentic-user-id",
    stage: "callable",
    input: #{ activity: #{ recipient: #{ role: "bot", agenticUserId: "agent-user-1" } } },
    expected: null,
  })
  @doc("Return recipient.agenticUserId when the Activity is agentic.")
  agenticUser(activity: Activity): string | null;

  @sync
  @vector(#{
    name: "agentic-tenant-prefers-recipient",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticAppInstance", tenantId: "tenant-recipient" },
        conversation: #{ tenantId: "tenant-conversation" },
      },
    },
    expected: "tenant-recipient",
  })
  @vector(#{
    name: "agentic-tenant-skips-empty-recipient",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticAppInstance", tenantId: "" },
        conversation: #{ tenantId: "tenant-conversation" },
      },
    },
    expected: "tenant-conversation",
  })
  @vector(#{
    name: "agentic-tenant-falls-back-to-conversation",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticAppInstance" },
        conversation: #{ tenantId: "tenant-conversation" },
      },
    },
    expected: "tenant-conversation",
  })
  @vector(#{
    name: "agentic-tenant-skips-empty-conversation",
    stage: "callable",
    input: #{
      activity: #{
        recipient: #{ role: "agenticAppInstance" },
        conversation: #{ tenantId: "" },
      },
    },
    expected: null,
  })
  @doc("Return the agentic tenant id from recipient first, then conversation.")
  agenticTenantId(activity: Activity): string | null;
}

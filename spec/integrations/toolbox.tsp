namespace Castia.Spec.Integrations;

@doc("Pure Foundry Toolbox MCP helper contracts. Token minting stays host-specific.")
interface IntegrationsToolboxRuntime {
  @sync
  @vector(#{
    name: "platform-endpoint-env-normalizes-name",
    stage: "callable",
    input: #{ name: "My.Box 2" },
    expected: "TOOLBOX_MY_BOX_2_MCP_ENDPOINT",
  })
  platformEndpointEnv(name: string): string;

  @sync
  @vector(#{
    name: "compose-toolbox-endpoint-unversioned",
    stage: "callable",
    input: #{ projectEndpoint: "https://acct.services.ai.azure.com/api/projects/proj/", name: "hal-smoke", version: null },
    expected: "https://acct.services.ai.azure.com/api/projects/proj/toolboxes/hal-smoke/mcp?api-version=v1",
  })
  @vector(#{
    name: "compose-toolbox-endpoint-versioned",
    stage: "callable",
    input: #{ projectEndpoint: "https://acct.services.ai.azure.com/api/projects/proj", name: "hal-smoke", version: "3" },
    expected: "https://acct.services.ai.azure.com/api/projects/proj/toolboxes/hal-smoke/versions/3/mcp?api-version=v1",
  })
  composeToolboxEndpoint(projectEndpoint: string, name: string, version: string | null): string;

  @sync
  @vector(#{
    name: "resolve-toolbox-full-url-wins",
    stage: "callable",
    input: #{ env: #{ TOOLBOX_ENDPOINT: "https://explicit/mcp", FOUNDRY_PROJECT_ENDPOINT: "https://acct.services.ai.azure.com/api/projects/proj", TOOLBOX_NAME: "ignored" } },
    expected: "https://explicit/mcp",
  })
  @vector(#{
    name: "resolve-toolbox-platform-native-before-compose",
    stage: "callable",
    input: #{ env: #{ TOOLBOX_NAME: "hal-smoke", TOOLBOX_HAL_SMOKE_MCP_ENDPOINT: "https://platform/mcp", FOUNDRY_PROJECT_ENDPOINT: "https://acct.services.ai.azure.com/api/projects/proj" } },
    expected: "https://platform/mcp",
  })
  @vector(#{
    name: "resolve-toolbox-composes-from-project-and-name",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_PROJECT_ENDPOINT: "https://acct.services.ai.azure.com/api/projects/proj", TOOLBOX_NAME: "hal-smoke" } },
    expected: "https://acct.services.ai.azure.com/api/projects/proj/toolboxes/hal-smoke/mcp?api-version=v1",
  })
  @vector(#{
    name: "resolve-toolbox-mcp-endpoint-alias",
    stage: "callable",
    input: #{ env: #{ TOOLBOX_MCP_ENDPOINT: "https://alias/mcp" } },
    expected: "https://alias/mcp",
  })
  @vector(#{
    name: "resolve-toolbox-none-when-unset",
    stage: "callable",
    input: #{ env: #{} },
    expected: null,
  })
  @vector(#{
    name: "resolve-toolbox-none-when-name-empty",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_PROJECT_ENDPOINT: "https://acct.services.ai.azure.com/api/projects/proj", TOOLBOX_NAME: "" } },
    expected: null,
  })
  resolveToolboxEndpoint(env: unknown): string | null;

  @sync
  @vector(#{
    name: "toolbox-mcp-tool-basic-shape",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp" } },
    expected: #{ type: "mcp", serverLabel: "toolbox", serverUrl: "https://x/mcp", requireApproval: "never" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-null-without-endpoint",
    stage: "callable",
    input: #{ config: #{} },
    expected: null,
  })
  @vector(#{
    name: "toolbox-mcp-tool-require-approval-override",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", requireApproval: "always" } },
    expected: #{ type: "mcp", serverLabel: "toolbox", serverUrl: "https://x/mcp", requireApproval: "always" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-auth-and-allowed-tools",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", allowedTools: #["web", "files"], token: "tok", headers: #{ `x-custom`: "1" }, projectConnectionId: "conn-1" } },
    expected: #{ type: "mcp", serverLabel: "toolbox", serverUrl: "https://x/mcp", requireApproval: "never", allowedTools: #["web", "files"], projectConnectionId: "conn-1", headers: #{ Authorization: "Bearer tok", `x-custom`: "1" } },
  })
  @vector(#{
    name: "toolbox-mcp-tool-overrides-emit-sidecar",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", serverLabel: "contracts", allowedTools: #["kb-conn___knowledge_base_retrieve"], descriptions: #{ knowledge_base_retrieve: "Search contract and billing policy sources." }, paramGuidance: #{ knowledge_base_retrieve: #{ query: "A natural-language contract or billing-policy question." } } } },
    expected: #{ type: "mcp", serverLabel: "contracts", serverUrl: "https://x/mcp", requireApproval: "never", allowedTools: #["kb-conn___knowledge_base_retrieve"], serverDescription: "Local tool guidance:\n- kb-conn___knowledge_base_retrieve: Search contract and billing policy sources.\n  - query: A natural-language contract or billing-policy question.", optimizerToolDefinitions: #[#{ type: "function", function: #{ name: "kb-conn___knowledge_base_retrieve", description: "Search contract and billing policy sources.", parameters: #{ type: "object", properties: #{ query: #{ description: "A natural-language contract or billing-policy question." } }, required: #[], additionalProperties: true } } }] },
  })
  @vector(#{
    name: "toolbox-mcp-tool-overrides-require-allowed-tools",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", descriptions: #{ knowledge_base_retrieve: "Search contracts." } } },
    expectedError: #{ message: "allowed_tools is required when toolbox descriptions or param_guidance are provided; castia cannot validate override names without a selected toolbox tool list" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-rejects-unknown-override",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", allowedTools: #["knowledge_base_retrieve"], descriptions: #{ stale_tool: "Search contracts." } } },
    expectedError: #{ message: "unknown toolbox descriptions key 'stale_tool'; expected one of: knowledge_base_retrieve" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-rejects-ambiguous-bare-override",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", allowedTools: #["one___search", "two___search"], descriptions: #{ search: "Ambiguous." } } },
    expectedError: #{ message: "ambiguous toolbox descriptions key 'search'; matches: one___search, two___search" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-rejects-non-string-allowed-tool",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", allowedTools: #["web", 1] } },
    expectedError: #{ message: "allowed_tools must be a list of strings" },
  })
  @vector(#{
    name: "toolbox-mcp-tool-empty-description-uses-fallback",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://x/mcp", allowedTools: #["web"], descriptions: #{ web: "" } } },
    expected: #{ type: "mcp", serverLabel: "toolbox", serverUrl: "https://x/mcp", requireApproval: "never", allowedTools: #["web"], serverDescription: "Local tool guidance:\n- web: Call the federated toolbox tool 'web'.", optimizerToolDefinitions: #[#{ type: "function", function: #{ name: "web", description: "Call the federated toolbox tool 'web'.", parameters: #{ type: "object", properties: #{}, required: #[], additionalProperties: true } } }] },
  })
  toolboxMcpTool(config: unknown): unknown;

  @sync
  @vector(#{
    name: "knowledge-base-tool-defaults",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://s/knowledgebases/kb/mcp", searchToken: "tok" } },
    expected: #{ type: "mcp", serverLabel: "knowledge-base", serverUrl: "https://s/knowledgebases/kb/mcp", requireApproval: "never", allowedTools: #["knowledge_base_retrieve"], headers: #{ `x-ms-query-source-authorization`: "tok" } },
  })
  @vector(#{
    name: "knowledge-base-tool-description-override",
    stage: "callable",
    input: #{ config: #{ endpoint: "https://s/knowledgebases/kb/mcp", descriptions: #{ knowledge_base_retrieve: "Search the KB." } } },
    expected: #{ type: "mcp", serverLabel: "knowledge-base", serverUrl: "https://s/knowledgebases/kb/mcp", requireApproval: "never", allowedTools: #["knowledge_base_retrieve"], serverDescription: "Local tool guidance:\n- knowledge_base_retrieve: Search the KB.", optimizerToolDefinitions: #[#{ type: "function", function: #{ name: "knowledge_base_retrieve", description: "Search the KB.", parameters: #{ type: "object", properties: #{}, required: #[], additionalProperties: true } } }] },
  })
  knowledgeBaseMcpTool(config: unknown): unknown;
}

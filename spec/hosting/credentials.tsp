namespace Castia.Spec.Hosting;

@doc("Pure hosting credential helpers. Managed identity acquisition and HTTP POSTs stay host-specific.")
interface HostingCredentialsRuntime {
  @sync
  @vector(#{
    name: "bearer-prefixes-token",
    stage: "callable",
    input: #{ token: "abc123" },
    expected: "Bearer abc123",
  })
  bearer(token: string): string;

  @sync
  @vector(#{
    name: "local-run-when-digital-worker-flag-present",
    stage: "callable",
    input: #{ env: #{ AGENT_DIGITAL_WORKER: "1", FOUNDRY_AGENT_TENANT_ID: "tenant" } },
    expected: true,
  })
  @vector(#{
    name: "local-run-when-hosted-tenant-absent",
    stage: "callable",
    input: #{ env: #{} },
    expected: true,
  })
  @vector(#{
    name: "not-local-when-hosted-tenant-present",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant" } },
    expected: false,
  })
  @vector(#{
    name: "not-local-when-hosted-tenant-present-but-empty",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "" } },
    expected: false,
  })
  @vector(#{
    name: "not-local-when-digital-worker-flag-empty-and-tenant-present",
    stage: "callable",
    input: #{ env: #{ AGENT_DIGITAL_WORKER: "", FOUNDRY_AGENT_TENANT_ID: "tenant" } },
    expected: false,
  })
  isLocalRun(env: unknown): boolean;

  @sync
  @vector(#{
    name: "agentic-identity-from-env-prefers-instance-client-id",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant", FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID: "blueprint", FOUNDRY_AGENT_INSTANCE_CLIENT_ID: "instance", FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID: "default" } },
    expected: #{ tenant_id: "tenant", blueprint_client_id: "blueprint", instance_client_id: "instance" },
  })
  @vector(#{
    name: "agentic-identity-from-env-falls-back-to-default-instance",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant", FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID: "blueprint", FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID: "default" } },
    expected: #{ tenant_id: "tenant", blueprint_client_id: "blueprint", instance_client_id: "default" },
  })
  @vector(#{
    name: "agentic-identity-from-env-empty-instance-falls-back-to-default-instance",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant", FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID: "blueprint", FOUNDRY_AGENT_INSTANCE_CLIENT_ID: "", FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID: "default" } },
    expected: #{ tenant_id: "tenant", blueprint_client_id: "blueprint", instance_client_id: "default" },
  })
  @vector(#{
    name: "agentic-identity-from-env-none-when-incomplete",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_TENANT_ID: "tenant", FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID: "" } },
    expected: null,
  })
  agenticIdentityFromEnv(env: unknown): unknown;

  @sync
  @vector(#{
    name: "tenant-token-endpoint",
    stage: "callable",
    input: #{ tenantId: "contoso" },
    expected: "https://login.microsoftonline.com/contoso/oauth2/v2.0/token",
  })
  tenantTokenEndpoint(tenantId: string): string;

  @sync
  @vector(#{
    name: "instance-token-request-shape",
    stage: "callable",
    input: #{ instanceClientId: "instance", agentAssertion: "assertion" },
    expected: #{ client_id: "instance", grant_type: "client_credentials", client_assertion_type: "urn:ietf:params:oauth:client-assertion-type:jwt-bearer", client_assertion: "assertion", scope: "api://AzureAdTokenExchange/.default" },
  })
  instanceTokenRequest(instanceClientId: string, agentAssertion: string): unknown;

  @sync
  @vector(#{
    name: "user-fic-token-request-shape",
    stage: "callable",
    input: #{ instanceClientId: "instance", agentAssertion: "assertion", instanceToken: "instance-token", agenticUserId: "user", scope: "https://graph.microsoft.com/.default" },
    expected: #{ client_id: "instance", grant_type: "user_fic", client_assertion_type: "urn:ietf:params:oauth:client-assertion-type:jwt-bearer", client_assertion: "assertion", user_federated_identity_credential: "instance-token", user_id: "user", scope: "https://graph.microsoft.com/.default" },
  })
  @vector(#{
    name: "user-fic-token-request-requires-agentic-user-id",
    stage: "callable",
    input: #{ instanceClientId: "instance", agentAssertion: "assertion", instanceToken: "instance-token", agenticUserId: "", scope: "https://graph.microsoft.com/.default" },
    expectedError: #{ message: "agentic_user_id is required" },
  })
  userFicTokenRequest(instanceClientId: string, agentAssertion: string, instanceToken: string, agenticUserId: string, scope: string): unknown;

  @sync
  @vector(#{
    name: "bot-connector-credential-prefers-default-instance",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID: "default", FOUNDRY_AGENT_INSTANCE_CLIENT_ID: "instance" } },
    expected: #{ credential: "managed_identity", client_id: "default", scope: "https://api.botframework.com/.default" },
  })
  @vector(#{
    name: "bot-connector-credential-falls-back-to-default-chain",
    stage: "callable",
    input: #{ env: #{} },
    expected: #{ credential: "default", client_id: null, scope: "https://api.botframework.com/.default" },
  })
  @vector(#{
    name: "bot-connector-credential-empty-default-falls-back-to-instance",
    stage: "callable",
    input: #{ env: #{ FOUNDRY_AGENT_DEFAULT_INSTANCE_CLIENT_ID: "", FOUNDRY_AGENT_INSTANCE_CLIENT_ID: "instance" } },
    expected: #{ credential: "managed_identity", client_id: "instance", scope: "https://api.botframework.com/.default" },
  })
  botConnectorCredential(env: unknown): unknown;

  @sync
  @vector(#{
    name: "hosting-scope-constants",
    stage: "callable",
    input: #{},
    expected: #{ botframework: "https://api.botframework.com/.default", graph: "https://graph.microsoft.com/.default", apxProduction: "5a807f24-c9de-44ee-a3a7-329e88a00ffc/.default" },
  })
  hostingScopes(): unknown;

  @sync
  @vector(#{
    name: "token-response-extracts-access-token",
    stage: "callable",
    input: #{ status: 200, body: #{ access_token: "token" }, text: "{\"access_token\":\"token\"}" },
    expected: "token",
  })
  @vector(#{
    name: "token-response-rejects-non-200-with-truncated-text",
    stage: "callable",
    input: #{ status: 403, body: #{ error: "forbidden" }, text: "forbidden" },
    expectedError: #{ message: "token endpoint returned 403: forbidden" },
  })
  @vector(#{
    name: "token-response-rejects-missing-access-token",
    stage: "callable",
    input: #{ status: 200, body: #{ token_type: "Bearer" }, text: "{\"token_type\":\"Bearer\"}" },
    expectedError: #{ message: "token endpoint response had no access_token" },
  })
  tokenResponseAccessToken(status: int32, body: unknown, text: string): string;
}

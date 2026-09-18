namespace Castia.Spec.Delivery;

@doc("Pure azd deployment validation helpers. Shell execution and manifest IO remain host-specific.")
interface DeliveryAzdRuntime {
  @sync
  @vector(#{
    name: "deployment-input-validates-approved-project",
    stage: "callable",
    input: #{ service: "smoke", environment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", timeout: 900.0 },
    expected: #{ service: "smoke", environment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", subscriptionId: "11111111-1111-1111-1111-111111111111", timeout: 900.0 },
  })
  @vector(#{
    name: "deployment-input-rejects-service-expression",
    stage: "callable",
    input: #{ service: "../smoke", environment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", timeout: 900.0 },
    expectedError: #{ message: "service must be a plain azure.yaml service name" },
  })
  @vector(#{
    name: "deployment-input-rejects-endpoint-resource-project-mismatch",
    stage: "callable",
    input: #{ service: "smoke", environment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/other", timeout: 900.0 },
    expectedError: #{ message: "project endpoint and ARM resource must name the same project" },
  })
  validateDeploymentInput(service: string, environment: string | null, projectEndpoint: string, projectResourceId: string, timeout: float64): unknown;

  @sync
  @vector(#{
    name: "environment-values-match-approved-context",
    stage: "callable",
    input: #{ values: #{ AZURE_ENV_NAME: "dev", FOUNDRY_PROJECT_ENDPOINT: "https://test.services.ai.azure.com/api/projects/project", AZURE_AI_PROJECT_ID: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", AZURE_SUBSCRIPTION_ID: "11111111-1111-1111-1111-111111111111" }, requestedEnvironment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", subscriptionId: "11111111-1111-1111-1111-111111111111" },
    expected: "dev",
  })
  @vector(#{
    name: "environment-values-block-endpoint-mismatch-before-resource-command",
    stage: "callable",
    input: #{ values: #{ AZURE_ENV_NAME: "dev", FOUNDRY_PROJECT_ENDPOINT: "https://other.services.ai.azure.com/api/projects/project", AZURE_AI_PROJECT_ID: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", AZURE_SUBSCRIPTION_ID: "11111111-1111-1111-1111-111111111111" }, requestedEnvironment: null, projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", subscriptionId: "11111111-1111-1111-1111-111111111111" },
    expectedError: #{ message: "stored azd FOUNDRY_PROJECT_ENDPOINT must match the approved project endpoint" },
  })
  validateEnvironmentValues(values: unknown, requestedEnvironment: string | null, projectEndpoint: string, projectResourceId: string, subscriptionId: string): unknown;

  @sync
  @vector(#{
    name: "verify-payload-produces-redacted-receipt",
    stage: "callable",
    input: #{ payload: #{ name: "smoke", version: "3", status: "active", definition: #{ kind: "hosted", environmentVariables: #{ AZURE_AI_MODEL_DEPLOYMENT_NAME: "model", OPTIMIZATION_CANDIDATE_ID: "candidate" }, codeConfiguration: #{ contentHash: "hash" } } }, service: "smoke", projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", expectedModel: "model", expectedCandidate: "candidate", expectedVersion: null },
    expected: #{ projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", agentName: "smoke", agentVersion: "3", `model`: "model", candidateId: "candidate", contentHash: "hash" },
  })
  @vector(#{
    name: "verify-payload-rejects-candidate-mismatch",
    stage: "callable",
    input: #{ payload: #{ name: "smoke", version: "3", status: "active", definition: #{ kind: "hosted", environmentVariables: #{ AZURE_AI_MODEL_DEPLOYMENT_NAME: "model", OPTIMIZATION_CANDIDATE_ID: "candidate" }, codeConfiguration: #{ contentHash: "hash" } } }, service: "smoke", projectEndpoint: "https://test.services.ai.azure.com/api/projects/project", projectResourceId: "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/group/providers/Microsoft.CognitiveServices/accounts/test/projects/project", expectedModel: null, expectedCandidate: "different", expectedVersion: null },
    expectedError: #{ message: "deployed candidate differs from the expected candidate" },
  })
  verifyPayload(payload: unknown, service: string, projectEndpoint: string, projectResourceId: string, expectedModel: string | null, expectedCandidate: string | null, expectedVersion: string | null): unknown;

  @sync
  @vector(#{
    name: "azd-command-error-redacts-output",
    stage: "callable",
    input: #{ command: #["deploy", "smoke", "--no-prompt", "--secret", "token"], returnCode: 1, stdout: "SECRET-STDOUT", stderr: "SECRET-STDERR" },
    expected: "azd deploy smoke --no-prompt exited 1; inspect deployment logs and verify remote state before retrying",
  })
  commandErrorMessage(command: string[], returnCode: int32, stdout: string, stderr: string): string;
}

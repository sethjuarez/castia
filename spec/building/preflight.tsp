namespace Castia.Spec.Building;

@doc("Pure preflight diagnostic/report shaping. Filesystem import and Python execution stay host-specific.")
interface BuildPreflightRuntime {
  @sync
  @vector(#{
    name: "preflight-report-ok-is-false-with-failures",
    stage: "callable",
    input: #{ root: "/tmp/agent", appTarget: "main:app", protocols: #["responses"], diagnostics: #[#{ check: "configuration.FOUNDRY_PROJECT_ENDPOINT", status: "pass", message: "Required setting is present." }, #{ check: "registration", status: "fail", message: "Cannot load/compile Agent (RuntimeError)." }] },
    expected: #{ root: "/tmp/agent", appTarget: "main:app", ok: false, scope: "offline", protocols: #["responses"], diagnostics: #[#{ check: "configuration.FOUNDRY_PROJECT_ENDPOINT", status: "pass", message: "Required setting is present." }, #{ check: "registration", status: "fail", message: "Cannot load/compile Agent (RuntimeError)." }] },
  })
  @vector(#{
    name: "preflight-report-warnings-and-skipped-are-ok",
    stage: "callable",
    input: #{ root: "/tmp/agent", appTarget: "main:app", protocols: #["responses", "invocations"], diagnostics: #[#{ check: "configuration.FOUNDRY_PROJECT_ENDPOINT", status: "pass", message: "Required setting is present." }, #{ check: "azd.AZURE_LOCATION", status: "warning", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "cloud", status: "skipped", message: "Credentials, RBAC, model availability and provisioning not checked." }] },
    expected: #{ root: "/tmp/agent", appTarget: "main:app", ok: true, scope: "offline", protocols: #["responses", "invocations"], diagnostics: #[#{ check: "configuration.FOUNDRY_PROJECT_ENDPOINT", status: "pass", message: "Required setting is present." }, #{ check: "azd.AZURE_LOCATION", status: "warning", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "cloud", status: "skipped", message: "Credentials, RBAC, model availability and provisioning not checked." }] },
  })
  preflightReport(root: string, appTarget: string, protocols: string[], diagnostics: unknown): unknown;

  @sync
  @vector(#{
    name: "required-env-diagnostics-do-not-echo-values",
    stage: "callable",
    input: #{ environment: #{ FOUNDRY_PROJECT_ENDPOINT: "SECRET-TOKEN" }, requiredEnv: #["FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_MODEL_DEPLOYMENT_NAME"] },
    expected: #[#{ check: "configuration.FOUNDRY_PROJECT_ENDPOINT", status: "pass", message: "Required setting is present." }, #{ check: "configuration.AZURE_AI_MODEL_DEPLOYMENT_NAME", status: "fail", message: "Required setting is missing." }, #{ check: "cloud", status: "skipped", message: "Credentials, RBAC, model availability and provisioning not checked." }],
  })
  requiredEnvDiagnostics(environment: unknown, requiredEnv: string[]): unknown;

  @sync
  @vector(#{
    name: "deployment-context-advisory-warnings",
    stage: "callable",
    input: #{ environment: #{}, deployment: false },
    expected: #[#{ check: "azd.AZURE_LOCATION", status: "warning", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.AZURE_AI_PROJECT_ID", status: "warning", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.AZURE_SUBSCRIPTION_ID", status: "warning", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.tenant", status: "skipped", message: "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication." }, #{ check: "azd.environment", status: "skipped", message: "Only supplied/process settings were checked; the selected azd environment was not read or modified." }],
  })
  @vector(#{
    name: "deployment-context-required-failures",
    stage: "callable",
    input: #{ environment: #{}, deployment: true },
    expected: #[#{ check: "azd.AZURE_LOCATION", status: "fail", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.AZURE_AI_PROJECT_ID", status: "fail", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.AZURE_SUBSCRIPTION_ID", status: "fail", message: "Code deployment needs this setting from your existing Foundry project in the selected azd environment and supplied/process environment. This check does not provision resources." }, #{ check: "azd.tenant", status: "skipped", message: "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication." }, #{ check: "azd.environment", status: "skipped", message: "Only supplied/process settings were checked; the selected azd environment was not read or modified." }],
  })
  @vector(#{
    name: "deployment-context-bad-arm-id",
    stage: "callable",
    input: #{ environment: #{ AZURE_LOCATION: "private-region", AZURE_SUBSCRIPTION_ID: "private-sub", AZURE_AI_PROJECT_ID: "https://project.invalid" }, deployment: true },
    expected: #[#{ check: "azd.AZURE_LOCATION", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_AI_PROJECT_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_SUBSCRIPTION_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.project_arm_id", status: "fail", message: "AZURE_AI_PROJECT_ID must be the project ARM resource ID, not the project endpoint URL." }, #{ check: "azd.tenant", status: "skipped", message: "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication." }, #{ check: "azd.environment", status: "skipped", message: "Only supplied/process settings were checked; the selected azd environment was not read or modified." }],
  })
  @vector(#{
    name: "deployment-context-valid-arm-id-omits-arm-warning",
    stage: "callable",
    input: #{ environment: #{ AZURE_LOCATION: "private-region", AZURE_SUBSCRIPTION_ID: "private-sub", AZURE_AI_PROJECT_ID: "/subscriptions/private-sub/resourceGroups/private-group/providers/Microsoft.CognitiveServices/accounts/private-account/projects/private-project" }, deployment: true },
    expected: #[#{ check: "azd.AZURE_LOCATION", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_AI_PROJECT_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_SUBSCRIPTION_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.tenant", status: "skipped", message: "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication." }, #{ check: "azd.environment", status: "skipped", message: "Only supplied/process settings were checked; the selected azd environment was not read or modified." }],
  })
  @vector(#{
    name: "deployment-context-bad-arm-id-is-advisory-when-not-deploying",
    stage: "callable",
    input: #{ environment: #{ AZURE_LOCATION: "private-region", AZURE_SUBSCRIPTION_ID: "private-sub", AZURE_AI_PROJECT_ID: "https://project.invalid" }, deployment: false },
    expected: #[#{ check: "azd.AZURE_LOCATION", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_AI_PROJECT_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.AZURE_SUBSCRIPTION_ID", status: "pass", message: "Deployment setting is present; resource existence is not checked." }, #{ check: "azd.project_arm_id", status: "warning", message: "AZURE_AI_PROJECT_ID must be the project ARM resource ID, not the project endpoint URL." }, #{ check: "azd.tenant", status: "skipped", message: "Tenant and login are not verified offline. azd resolves the tenant from the authenticated AZURE_SUBSCRIPTION_ID; ensure that login targets the intended tenant. AZURE_TENANT_ID alone is not authentication." }, #{ check: "azd.environment", status: "skipped", message: "Only supplied/process settings were checked; the selected azd environment was not read or modified." }],
  })
  deploymentContextDiagnostics(environment: unknown, deployment: boolean): unknown;
}

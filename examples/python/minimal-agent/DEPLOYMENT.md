# Deploy to an existing Foundry project

This scaffold creates local files only. It does not create a project, model deployment, registry, identity, or infrastructure.

Before code deployment, select your existing azd environment and populate its context with values verified against that existing project:

| Setting | Meaning |
| --- | --- |
| `AZURE_LOCATION` | The existing project's Azure region; required for code deploy. |
| `AZURE_AI_PROJECT_ID` | Full project ARM resource ID, not an endpoint URL. |
| `AZURE_SUBSCRIPTION_ID` | Subscription containing the project. |
| `FOUNDRY_PROJECT_ENDPOINT` | Existing project's data-plane endpoint. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Existing model deployment name. |

The ARM ID has the shape `/subscriptions/<subscription>/resourceGroups/<group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>`.

Authenticate azd in the intended tenant with access to that subscription. azd resolves the tenant from the subscription; setting `AZURE_TENANT_ID` alone is not authentication. This project does not verify credentials or RBAC.

Run `python -m castia build check --deployment` after exporting the same nonsecret context into your shell. The check reads process settings, not azd's persisted environment, and does not load `.env.example`. Without `--deployment`, missing deployment context is advisory so offline protocol development can continue. Passing this gate is not a live deployment guarantee.

The default manifest uses Python 3.13 with remote dependency build; no local Docker/ACR setup is needed for this mode. The included Dockerfile is an explicit alternative; see the commented manifest instructions.

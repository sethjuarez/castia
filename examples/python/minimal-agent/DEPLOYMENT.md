# Deploy to an existing Foundry project

This scaffold creates local files only. It does not create a project, model deployment, registry, identity, or infrastructure.

## Run locally first

Copy `.env.example` to `.env`, fill `FOUNDRY_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, then run the app from the agent root:

```powershell
uv sync --project .
uv run --directory . python main.py
```

The entrypoint validates `.env` and `.agent_configs/baseline` before serving.
Use the Foundry Agent Playground health check against `http://localhost:8088`
before sending a model prompt.

Before code deployment, select your existing azd environment and populate its context with values verified against that existing project:

| Setting | Meaning |
| --- | --- |
| `AZURE_LOCATION` | The existing project's Azure region; required for code deploy. |
| `AZURE_AI_PROJECT_ID` | Full project ARM resource ID, not an endpoint URL. |
| `AZURE_SUBSCRIPTION_ID` | Subscription containing the project. |
| `FOUNDRY_PROJECT_ENDPOINT` | Existing project's data-plane endpoint for local/process checks; do not put it in hosted `azure.yaml` env. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Existing model deployment name. |

The ARM ID has the shape `/subscriptions/<subscription>/resourceGroups/<group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>`.

Authenticate azd in the intended tenant with access to that subscription. azd resolves the tenant from the subscription; setting `AZURE_TENANT_ID` alone is not authentication. This project does not verify credentials or RBAC.

Run `python -m castia build check --deployment` after exporting the same nonsecret context into your shell. The check reads process settings, not azd's persisted environment, and does not load `.env.example`. Without `--deployment`, missing deployment context is advisory so offline protocol development can continue. Passing this gate is not a live deployment guarantee.

Foundry hosted agents reserve all `FOUNDRY_*` and `AGENT_*` container variables. Keep `FOUNDRY_PROJECT_ENDPOINT` in `.env` for local dev or in host-side process/azd context for Castia checks; do not declare it under `services.<agent>.env` in `azure.yaml`.

`pyproject.toml` is the canonical dependency source. Foundry hosted code deploy
currently uses Oryx, so `requirements.txt` is also present as a compatibility
entrypoint and mirrors runtime dependencies directly. For this `[tool.uv]
package = false` app, never use `-e .`; editable install asks pip to build the
non-package app during remote build. Before deploying, make sure `main.py` boots
with the published Castia dependency pinned in both `pyproject.toml` and
`requirements.txt`; if local code uses a newer SDK API, publish and bump that
pin or keep the code backwards compatible with the pinned version.

The default manifest uses Python 3.13 with remote dependency build; no local Docker/ACR setup is needed for this mode. The included Dockerfile is an explicit alternative; see the commented manifest instructions.

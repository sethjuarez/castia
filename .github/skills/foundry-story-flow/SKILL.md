# Foundry Story Flow

Use this repo-local playbook when exercising the simplest hosted-agent story:
make the agent work locally, make the same agent work in Foundry, then guide the
Microsoft 365 Copilot/Teams publish-and-hire check.

## Goal

Keep the user path small:

1. Choose or create one Foundry project with a deployed model.
2. Local: run and test the agent.
3. Foundry: deploy and test the hosted agent.
4. Teams: guide the Microsoft 365 Copilot/Teams publish-and-hire check.

Do not lead with Castia, metadata, protocol plumbing, fingerprints, or Azure
resource taxonomy. Surface those only as troubleshooting details.

## Agent identity

Treat `folder + azd service name` as the local agent identity.

- Discover agents from `azure.yaml` services with `host: azure.ai.agent`.
- If one agent is discovered, select it automatically.
- If multiple agents are discovered, ask the user to choose.
- A name alone is a bootstrap hint, not a durable hosted binding.

## First-run contract

Before local testing can work, the agent needs a Foundry project with a deployed
model. Ask for the minimum Foundry target:

- Foundry project endpoint, for example
  `https://<account>.services.ai.azure.com/api/projects/<project>`
- model deployment name, for example `gpt-5.5`

Use this same project for both local model calls and hosted deployment unless the
user explicitly overrides it.

If the user does not already have a project and deployed model, guide them to
create those first before starting the local run.

Persist these values only to developer-local state:

```powershell
azd env set FOUNDRY_PROJECT_ENDPOINT "<project-endpoint>"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "<model-deployment-name>"
```

Do not commit project endpoints, subscriptions, resource groups, tenant IDs,
connection strings, keys, tokens, or user-specific auth state.

## Local step

Do not start local testing until the first-run Foundry target is known.

Show the exact command for the selected agent folder. For the Python example:

```powershell
cd examples\python\minimal-agent
$env:FOUNDRY_PROJECT_ENDPOINT="<project-endpoint>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME="<model-deployment-name>"
python main.py
```

Then test:

- `GET /readiness`
- `POST /responses`
- streaming `POST /responses` with `{ "stream": true }`

Local is complete when the agent answers.

## Foundry step

Before deploying, verify that the selected agent boots with the same dependency
surface the hosted container will install. Do not rely only on editable local SDK
tests if `requirements.txt` pins a published SDK version. If agent code uses a
new local SDK API, either publish/bump the deployed dependency or add a
backwards-compatible fallback before deploy.

Start with `azd deploy <service> --no-prompt` from the selected agent folder.

If `azd` reports that infrastructure has not been provisioned, do not tell the
user the Foundry project is missing. Say the repo still needs to be connected to
the existing project for hosted deployment.

The preferred recovery is:

1. Ensure the first-run Foundry values are in the azd env.
2. Resolve any missing Azure management-plane values from the project endpoint
   and logged-in Azure context when possible.
3. Ask only for values that cannot be discovered safely.
4. Run `azd provision --no-prompt`.
5. Run `azd deploy <service> --no-prompt` again.

Foundry is complete when the hosted agent/version is resolved and the same smoke
prompt works against the hosted Responses endpoint.

If hosted testing fails with `session_not_ready` or `424 FailedDependency`, read
the hosted session logs before changing deployment settings. A common cause is a
container startup crash caused by dependency drift between local source and the
version pinned in the agent's deployment requirements.

## Teams step

Do not automate tenant permission changes, Teams installation, organization
publication, or admin approval. Guide the user through the Microsoft 365
Copilot/Teams publish-and-hire check:

Show three large gates:

1. **Publish in Foundry** — confirm the intended hosted version is active, then
   use **Publish** -> **Teams and Microsoft 365 Copilot**. Review app metadata
   and choose personal testing or organization distribution.
2. **Approve request in A365** — complete the Microsoft 365 publish request or
   admin approval flow for the selected tenant scope.
3. **Hire in Teams** — install or hire the agent from Teams, send the same
   smoke-test prompt, and confirm the Teams answer matches hosted Foundry
   behavior.

Mark Teams tested only after the user confirms it answered.

Teams is complete when the user confirms the smoke prompt worked there.

## UX rules

- Main path labels: `Local`, `Foundry`, `Teams`.
- Keep one primary action visible.
- Hide settings, logs, commands, and derived values behind details/settings.
- Use honest states:
  - `Needs Foundry project`
  - `Local ready`
  - `Prepare deploy`
  - `Deploy it`
  - `Hosted ready`
  - `Hire it`
  - `Tested`
- Never claim a hosted deployment or Teams test succeeded unless it was verified.

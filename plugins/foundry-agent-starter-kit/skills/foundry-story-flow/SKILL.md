---
name: foundry-story-flow
description: "Build and verify the local to Foundry to Teams story flow for a Castia-based Microsoft Foundry hosted agent, with approval gates for live work."
---

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

For a new Python hosted agent in this story flow, prefer a Castia SDK
implementation by default. Use another implementation only when the user asks
for it or the target repo already has a non-Castia agent that should be
preserved.

Do not lead the user with metadata, protocol plumbing, fingerprints, or Azure
resource taxonomy. Surface those only as troubleshooting details.

## Build step

When the repo does not already contain a hosted agent and the user asks to
build, scaffold, or try the full story flow, create a Castia-based Python agent
unless explicitly told otherwise.

If the `castia-lifecycle` skill is available, use it before creating files. Its
new-agent golden path is authoritative for the Castia file layout and code
shape.

Minimum shape:

- `main.py` defines a Castia `Agent` and registers `@app.responses()`.
- `.env.example` names `FOUNDRY_PROJECT_ENDPOINT` and
  `AZURE_AI_MODEL_DEPLOYMENT_NAME`; `.env` is ignored and user-filled.
- If Teams validation is in scope, also register Activity/Teams handling using
  Castia primitives.
- `azure.yaml` declares one `host: azure.ai.agent` service with
  `codeConfiguration` for Python remote build.
- `.agent_configs/baseline/` contains the baseline instructions/metadata.
- The entrypoint loads `.env` and fails before serving if `.env` or baseline
  instructions are missing/incomplete.
- `eval.yaml` and any seed data should be present only if they are part of the
  starter flow being exercised.

Prefer `uv` for local setup and dependency execution. In a monorepo/source
checkout, use the local Castia source package or editable install; in a clean
starter repo, depend on the published `castia[deploy,optimize,test]` package
unless the user specifically wants to test unpublished local SDK changes.

Do not scaffold or keep an Agent Framework sample as the implementation for a
Castia story-flow starter. If an external scaffold command creates one, replace
it before local testing or deployment.

If `.env` is missing or incomplete, ask for the Foundry project endpoint and use
the Foundry Agent Playground `bootstrap_env` canvas action when it is available.
That action writes only non-secret values to gitignored `.env` files and
preserves existing values unless overwrite is explicitly requested. If the
canvas action is unavailable, tell the user to copy `.env.example` to `.env`,
fill the project endpoint and model deployment name, and refresh.

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

Persist these values first to developer-local `.env` only:

```powershell
# Preferred when the Foundry Agent Playground canvas is available:
# invoke bootstrap_env with projectEndpoint and, if needed, modelDeployment.

# Fallback:
Copy-Item .env.example .env
# Fill FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME in .env.
```

Set the same values in the azd environment only when the user explicitly moves
from local testing to hosted deployment.

Do not commit project endpoints, subscriptions, resource groups, tenant IDs,
connection strings, keys, tokens, or user-specific auth state.

## Local step

Do not start local testing until the first-run Foundry target is known.

Show the exact command for the selected agent folder. For the Castia Python example:

```powershell
cd <agent-root>
uv sync --project .
uv run --directory . python main.py
```

Then test:

- `GET /readiness`
- `POST /responses`
- streaming `POST /responses` with `{ "stream": true }`

Local is complete when the agent answers.

## Foundry step

Before deploying, verify that the selected agent boots with the same dependency
surface the hosted container will install. For scaffolded Castia agents,
`pyproject.toml` is canonical and `requirements.txt` should contain only `-e .`
as the Foundry remote-build shim. If agent code uses a new local SDK API, either
publish/bump the Castia dependency pinned in `pyproject.toml` or add a
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

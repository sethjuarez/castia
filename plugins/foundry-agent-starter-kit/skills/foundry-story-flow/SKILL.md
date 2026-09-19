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

**Required canvas gate:** if the selected agent root does not already have a
resolved Foundry project endpoint from `.env` or `azd env get-values`, do not ask
for it in chat and do not call an agent-facing bootstrap action. Open the Foundry
Agent Playground and use **Start local**. The canvas opens the in-canvas endpoint
dialog, writes only non-secret derived values to gitignored `.env` files, and
then starts the local agent. Do not infer, guess, create, or silently select a
Foundry project.

## Agent identity

Treat `folder + azd service name` as the local agent identity.

- Discover agents from `azure.yaml` services with `host: azure.ai.agent`.
- If one agent is discovered, select it automatically.
- If multiple agents are discovered, ask the user to choose.
- A name alone is a bootstrap hint, not a durable hosted binding.

## First-run contract

Before local testing can work, the agent needs a Foundry project with a deployed
model. If project values are missing, use the Foundry Agent Playground **Start
local** button as the only endpoint-entry path. The canvas dialog collects a
project endpoint such as
`https://<account>.services.ai.azure.com/api/projects/<project>` and writes the
developer-local `.env` values needed for local mode.

Use this same project for both local model calls and hosted deployment unless the
user explicitly overrides it.

If the user does not already have a project and deployed model, guide them to
create those first before starting the local run.

Persist these values first to developer-local `.env` only through the Foundry
Agent Playground **Start local** dialog. Do not ask for the endpoint in chat,
do not invoke an agent-facing bootstrap action, and do not tell the user to edit
`.env` manually unless they explicitly reject the canvas flow.

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

## Deployed-agent trace handoff

After a hosted smoke test or any deployed-agent invocation, preserve any
`traceId`, `conversationId`, `sessionId`, `responseId`, agent name, version, and
endpoint returned by `azd ai agent invoke`, the Playground, or Foundry. If the
user asks why a deployed agent failed, what happened during an invocation, or
how to inspect production behavior, follow
[`castia-lifecycle/hosted-invoke-traces.md`](../castia-lifecycle/hosted-invoke-traces.md)
before writing KQL.

Trace lookup rules:

1. Prefer `azd ai agent monitor --tail 120` first to confirm the hosted process
   received the request and whether readiness, `/responses`, framework, or
   server errors occurred.
2. If a `traceId` is available, treat it as the App Insights `operation_Id` and
   query `requests`, `dependencies`, `traces`, `exceptions`, and `customEvents`
   using the templates in `hosted-invoke-traces.md`.
3. If no `traceId` is available, search recent `requests` by Foundry agent name
   and then drill into the returned `operation_Id`.
4. Resolve the App Insights resource from `azd env get-values` first
   (`APPLICATIONINSIGHTS_CONNECTION_STRING` or related observability values).
   If azd does not expose it, use the Foundry trace skill's App Insights
   resolution workflow before querying. App Insights telemetry can lag behind
   hosted logs; if logs show the request but the first KQL query returns no
   rows, wait briefly and retry before concluding the trace is absent.
5. Never conclude the hosted agent returned no answer solely because terminal
   output or telemetry lacks literal answer text; verify the hosted HTTP,
   AgentServer, model, tool, and response-body layers first.

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

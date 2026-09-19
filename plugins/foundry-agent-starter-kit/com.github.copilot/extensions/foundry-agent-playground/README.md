# Foundry Agent Playground

Copilot canvas extension for chatting with an agent through
the Responses protocol. The guided story is intentionally small. Choose one
Foundry project with a deployed model, make it work locally, make it work in
Foundry, then guide the Microsoft 365 Copilot/Teams publish and hire check.

Open the canvas with this input.

```json
{
  "canvasId": "foundry-agent-playground",
  "instanceId": "minimal-agent-chat",
  "input": {
    "endpoint": "http://127.0.0.1:8088"
  }
}
```

## What it tests

The chat view talks to the selected target.

- `GET /readiness`
- `POST /responses` with `{ "input": "..." }`
- `POST /responses` with `{ "input": "...", "stream": true }` when the agent supports SSE streaming

The UI includes these parts.

- agent discovery from `azure.yaml` services that use `host: azure.ai.agent`;
- an agent picker keyed by folder plus azd service name;
- a guided Local -> Foundry -> Teams step rail with one primary action at a time;
- a first-run `.env` bootstrap. Paste an existing Foundry project endpoint and
  the canvas writes only non-secret derived values into gitignored `.env` files;
- Local and Foundry target modes with separate endpoint state;
- readiness status and response latency;
- transcript counters for total, passing, and failing turns;
- raw request/response JSON for protocol debugging;
- chat keyboard input. Enter sends, Shift+Enter adds a newline;
- advanced settings for local endpoint and bootstrap refreshes.

## First-run `.env` bootstrap

Use **Create .env** when a new local session has `.env.example` files but no
filled `.env` files yet. The canvas accepts a Foundry project endpoint such as:

```text
https://<account>.services.ai.azure.com/api/projects/<project>
```

It derives and writes these non-secret values:

- `FOUNDRY_PROJECT_ENDPOINT`, `AZURE_AI_PROJECT_ENDPOINT`, and
  `AZURE_AIPROJECT_ENDPOINT`;
- `AZURE_AI_ACCOUNT_NAME` and `AZURE_AI_PROJECT_NAME`;
- `AZURE_AI_MODEL_DEPLOYMENT_NAME`, defaulting to `gpt-6-astra`;
- optional toolbox values, defaulting to `TOOLBOX_NAME=contract-toolbox` and
  `TOOLBOX_CONTRACT_TOOLBOX_MCP_ENDPOINT=<project>/toolboxes/contract-toolbox/mcp?api-version=v1`.

Safety rules:

- secrets are never requested or written;
- existing values are preserved unless **Overwrite existing values** is checked;
- only files named `.env` can be written;
- `.env.example` is never modified;
- every target must be ignored by git, verified with `git check-ignore`.

Targets are discovered from `.env.example` siblings, hosted agent roots found in
`azure.yaml`, and common `modules\agents` layouts. Agents can also trigger the
same behavior through the `bootstrap_env` canvas action with
`projectEndpoint`, optional `modelDeployment`, `toolboxName`, `overwrite`,
`dryRun`, and `targetPaths`.

The deploy view runs from the selected agent folder.

- `azd env set FOUNDRY_PROJECT_ENDPOINT <endpoint>` and
  `azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME <deployment>` bind the selected
  azd environment to the same Foundry project used for local model calls;
- when Azure CLI is signed in, the canvas derives deployment context from the
  project endpoint and writes values such as `AZURE_SUBSCRIPTION_ID`,
  `AZURE_LOCATION`, and `AZURE_AI_PROJECT_ID` into the local azd environment;
- `azd env get-values` discovers the Foundry project, hosted agent name, version,
  and protocol endpoints for that agent service;
- `azd deploy <service> --no-prompt` streams deployment output into the canvas
  after a confirmation in the canvas;
- if deploy reports that infrastructure has not been provisioned, the primary
  action becomes "Prepare deploy" and runs `azd provision --no-prompt`;
- after deployment, the canvas refreshes hosted metadata so the Hosted target can
  show the latest version and Responses endpoint if azd exposes one.

For scaffolded Castia agents, `pyproject.toml` is the canonical dependency
source. `requirements.txt` exists only as the Foundry remote-build entrypoint
and should contain `-e .`, so pip installs the selected local project and reads
the dependency list from `pyproject.toml`. If local agent code uses a newer SDK
API than the Castia dependency pinned in `pyproject.toml`, the hosted session can
fail readiness even though deployment succeeds.

The Teams view keeps the Microsoft 365 handoff lightweight.

- shows whether the hosted agent/version has been resolved;
- presents three large gates. **Publish in Foundry**, **Approve request in
  A365**, and **Hire in Teams**;
- calls out the active version, Microsoft 365 app metadata, tenant scope,
  approval, Teams install/hire action, and smoke test confirmation;
- lets the user mark the Teams test complete without automating tenant,
  publication, approval, or Teams permission changes.

Transcript state is in memory for the open canvas instance. Closing or reloading
the extension clears the transcript, deploy log, and Teams confirmation state.

## Distribution

This canvas ships inside the Foundry Agent Starter Kit plugin under
`com.github.copilot/extensions/foundry-agent-playground`.

Use the app's extension sharing flow only when you need to test the canvas by
itself, outside the plugin.

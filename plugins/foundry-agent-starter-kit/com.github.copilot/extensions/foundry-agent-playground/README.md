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
- a first-run `.env` bootstrap for an existing Foundry project endpoint, owned
  by the **Start local** in-canvas endpoint dialog rather than chat prompts or
  agent-invoked bootstrap actions;
- a deterministic local-start guard: if the user clicks **Start local** before
  Foundry project values are present, the plugin opens an in-canvas question box
  for the project endpoint, bootstraps `.env`, and then starts the local agent;
- Local and Foundry target modes with separate endpoint state;
- readiness status and response latency;
- transcript counters for total, passing, and failing turns;
- raw request/response JSON for protocol debugging;
- chat keyboard input. Enter sends, Shift+Enter adds a newline;
- a refresh affordance for values the agent has already bootstrapped into `.env`.

## First-run `.env` bootstrap

When a new local session has `.env.example` files but no filled `.env` files
yet, clicking **Start local** opens an in-canvas question box asking which Foundry
project to use. The plugin then bootstraps gitignored `.env` files from a
project endpoint such as:

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
- existing values are preserved unless the agent explicitly passes
  `overwrite: true`;
- only files named `.env` can be written;
- `.env.example` is never modified;
- every target must be ignored by git, verified with `git check-ignore`.

Targets are discovered from `.env.example` siblings, hosted agent roots found in
`azure.yaml`, and common `modules\agents` layouts. The project endpoint is
entered only through the Start local dialog; the SDK canvas action surface does
not expose a separate endpoint-bootstrap action.

The Foundry step keeps hosted state visible in the main playground context.

- `azd env set FOUNDRY_PROJECT_ENDPOINT <endpoint>` and
  `azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME <deployment>` bind the selected
  azd environment to the same Foundry project used for local model calls;
- when Azure CLI is signed in, the canvas derives deployment context from the
  project endpoint and writes values such as `AZURE_SUBSCRIPTION_ID`,
  `AZURE_LOCATION`, and `AZURE_AI_PROJECT_ID` into the local azd environment;
- the loaded Foundry project is queried directly for the selected hosted agent
  and its deployed versions/endpoints; the current hosted release, status, and
  Responses endpoint are shown alongside the chat transcript without requiring
  a deploy view; `azd env get-values` remains a fallback cache when remote
  discovery cannot run;
- `azd deploy <service> --no-prompt` streams deployment output into the canvas
  after a confirmation in the canvas;
- when no hosted release is discovered, the Foundry step guides the user toward
  a first deploy; when one exists, deploy remains an explicit "Deploy new
  version" action for intentional updates;
- if deploy reports that infrastructure has not been provisioned, the primary
  action becomes "Prepare deploy" and runs `azd provision --no-prompt`;
- after deployment, the canvas refreshes hosted metadata from the Foundry project
  so the Hosted target can show the latest version and Responses endpoint even
  when azd env outputs are missing.

For scaffolded Castia agents, `pyproject.toml` is the canonical dependency
source. `package = false` apps should not carry a runtime `requirements.txt`
containing `-e .`; current Foundry remote build resolves dependencies from
`pyproject.toml`. If a requirements file is kept for external compatibility, it
must list runtime dependencies directly. If local agent code uses a newer SDK API
than the Castia dependency pinned in `pyproject.toml`, the hosted session can
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

# Foundry Agent Starter Kit

Build, test, and deploy Microsoft Foundry agents from Python repos.

This package collects the reusable Copilot guidance for Foundry
agents. It is named for the user job, not for the SDK. Castia remains the Python
package underneath the flow.

## What is included

| Path | Purpose |
| --- | --- |
| `skills/castia-lifecycle` | Build, test, evaluate, optimize, and deploy on the Castia rails. |
| `skills/castia-optimizer` | Keep optimizer work separate from azd deployment. |
| `skills/foundry-story-flow` | Run the local -> Foundry -> Teams demo path with approval gates. |
| `com.github.copilot/commands` | Build and test commands installed with the plugin. |
| `prompts/` | Build and test prompts for manual repo setup. |
| `com.github.copilot/extensions/foundry-agent-playground` | Playground canvas for local and hosted agent tests. |

## Install

Install from the Castia marketplace in Copilot App when that marketplace is
available to your environment:

1. Open **Customize**.
2. Open **Plugins**.
3. Add the `sethjuarez/castia` marketplace if it is not listed.
4. Install **Foundry Agent Starter Kit**.

For command line setup, install the plugin from the repository subdirectory:

```powershell
copilot plugin marketplace add sethjuarez/castia
copilot plugin install foundry-agent-starter-kit@castia
```

This is Copilot plugin distribution, not Agency distribution. Do not use Agency
plugin commands or Agency marketplaces for the public Castia path.

The packaged canvas is the only supported Foundry Agent Playground. Do not copy
the extension into a scenario repository; installing or updating this plugin
keeps the playground implementation and its action contract consistent across
projects.

If a scenario repository already contains
`.github\extensions\agent-playground`, remove that directory and install or
update this plugin instead. The supported canvas ID is now
`foundry-agent-playground`; update saved canvas references that still use the
old `agent-playground` ID.

## Canvas-only testing

Use this when you need to test or share the canvas before the plugin marketplace
path is available. The canvas folder is:

```text
plugins\foundry-agent-starter-kit\com.github.copilot\extensions\foundry-agent-playground
```

Open it as **Foundry Agent Playground** after installing the extension through
Copilot App's extension sharing/install flow.

## Build

The plugin directory is the installed product payload referenced by the Castia
marketplace. Browser assets that the canvas serves must be committed under this
directory so `copilot plugin install` and `copilot plugin update` receive a
self-contained plugin.

After changing renderer source, rebuild the committed browser asset:

```powershell
Push-Location plugins\foundry-agent-starter-kit
npm ci
npm run build
npm test
Pop-Location
```

CI runs the same build, checks the generated
`com.github.copilot\extensions\foundry-agent-playground\renderer\dist\playground-client.js`
asset is fresh, and fails if the committed plugin payload is stale.

## Manual repo setup

Use this only when you want the prompts and skills committed to a scenario repo
instead of installed as a plugin:

```powershell
New-Item -ItemType Directory -Force .github\prompts, .github\skills
Copy-Item ..\castia\plugins\foundry-agent-starter-kit\prompts\*.prompt.md .github\prompts\
Copy-Item ..\castia\plugins\foundry-agent-starter-kit\skills\* .github\skills\ -Recurse
```

## Use

1. Open a scenario repo in Copilot App.
2. Ask Copilot to build a Foundry agent for that repo.
3. Open **Foundry Agent Playground**.
4. Click **Start local**. If the repo has no project endpoint yet, the canvas
   opens its in-canvas endpoint dialog, then saves non-secret derived values into
   gitignored local `.env` files and starts the agent.
5. Check readiness before sending a prompt. The Playground discovers the
   selected agent's supported protocols and exposes a protocol toggle for
   Responses, Activity, and Invocations when endpoints are available. Activity
   Protocol testing posts Bot Framework Activities and captures local connector
   egress so replies, typing, reactions, updates, and deletes render in the
   transcript instead of appearing as a bare `200` ack. Agent answers can include
   fenced `mermaid` flowchart/graph blocks; the custom canvas renders simple
   nodes, common labels, subgraph grouping, and solid or dotted edges inline as
   diagrams and keeps the original Markdown available for copy/export.
6. When Copilot drives or validates the Playground, it must focus the same open
   canvas instance and call its canvas actions (`set_target` when needed,
   `health_check`, `set_protocol`, `send_response`, `send_activity`,
   `send_invocation`, `get_protocol_state`, `get_transcript_state`) so the
   side-panel transcript and operation log update for the user. Do not use
   Playwright, browser navigation, or direct canvas URL automation for Playground
   smoke, layout, or collaboration validation; those paths do not exercise the
   shipped side-panel canvas.
7. Deploy or publish only after explicit approval.

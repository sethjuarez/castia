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

## Canvas-only testing

Use this when you need to test or share the canvas before the plugin marketplace
path is available. The canvas folder is:

```text
plugins\foundry-agent-starter-kit\com.github.copilot\extensions\foundry-agent-playground
```

Open it as **Foundry Agent Playground** after installing the extension through
Copilot App's extension sharing/install flow.

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
5. Check readiness before sending a prompt.
6. Deploy or publish only after explicit approval.

# Foundry Agent Starter Kit

Build, test, and deploy Microsoft Foundry agents from Python repos.

This plugin packages the reusable Copilot guidance for Castia-backed Foundry
agents. It is named for the user job, not for the SDK. Castia remains the Python
package underneath the flow.

## What is included

| Path | Purpose |
| --- | --- |
| `skills/castia-lifecycle` | Build, test, evaluate, optimize, and deploy on the Castia rails. |
| `skills/castia-optimizer` | Keep optimizer work separate from azd deployment. |
| `skills/foundry-story-flow` | Run the local -> Foundry -> Teams demo path with approval gates. |
| `prompts/` | Build and test prompts for scenario repos. |
| `extensions/foundry-agent-playground` | Source for the playground canvas used to test local and hosted agents. |

## Install

For a plugin install:

```powershell
agency plugin install github:sethjuarez/castia:plugins/foundry-agent-starter-kit --engine copilot
```

For repo-local use, copy the skills and prompts into the scenario repo:

```powershell
New-Item -ItemType Directory -Force .github\prompts, .github\skills
Copy-Item ..\castia\plugins\foundry-agent-starter-kit\prompts\*.prompt.md .github\prompts\
Copy-Item ..\castia\plugins\foundry-agent-starter-kit\skills\* .github\skills\ -Recurse
```

Install the playground canvas from the extension folder or a shared extension
gist:

```text
https://github.com/sethjuarez/castia/tree/main/plugins/foundry-agent-starter-kit/extensions/foundry-agent-playground
```

## Use

1. Open a scenario repo in Copilot App.
2. Ask Copilot to build a Foundry agent for that repo.
3. Fill `.env` from `.env.example`.
4. Start the agent with `uv run --directory <agent-root> python main.py`.
5. Open **Foundry Agent Playground**.
6. Check readiness before sending a prompt.
7. Deploy or publish only after explicit approval.

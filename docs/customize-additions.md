# Customize additions plan

Castia should be installable from the docs site into GitHub Copilot App's Customize surface. The site is the front door. The installable thing is a Copilot plugin that carries the Castia canvas, skills, prompts, and repo setup guidance.

## What we will ship

| Addition | What it does |
| --- | --- |
| Castia Agent Lab canvas | Local chat, traces, tool calls, and validation output for a scenario agent |
| `castia-lifecycle` skill | Build, test, evaluate, optimize, and deploy guidance with the right approval boundaries |
| `castia-optimizer` skill | Optimizer-specific rails for baselines, candidates, status, cancel, and apply |
| Build prompt | Creates or updates the smallest useful scenario agent |
| Test prompt | Runs local proof and records one short transcript |
| Scenario repo instructions | Tells a fresh session the target use case, model policy, test command, and live-work gates |

The canvas is the missing product piece. The prompts and skills can ship first because they already help a new session succeed.

## Website install path

The future docs page should have one clear path:

1. Install the Castia Copilot plugin from Customize.
2. Copy the scenario starter files into the target repo.
3. Open the target repo in Copilot App.
4. Run the build prompt.
5. Try the local agent in the Castia Agent Lab canvas.
6. Deploy only after the local proof looks good.

The website should not provision Azure resources or grant permissions. It should point the user to Customize and explain what will be installed.

## Plugin shape

The plugin should bundle:

```text
plugins/castia-agent-lab/
|-- plugin.json
|-- extensions/
|   `-- castia-agent-lab/
|       `-- extension.mjs
|-- skills/
|   |-- castia-lifecycle/
|   `-- castia-optimizer/
`-- prompts/
    |-- build-castia-agent.prompt.md
    `-- test-castia-agent.prompt.md
```

The extension should open a local browser surface that talks to a scenario agent endpoint. The first version can require the user or Copilot to start the local agent. A later version can discover common Castia scaffold paths and start the process when the repo allows it.

## Canvas scope

The Castia Agent Lab should stay smaller than a full Foundry project manager.

It should show:

- Agent endpoint and health.
- Prompt input and response output.
- Tool calls and MCP server events.
- Local readiness checks.
- Test and eval command results.
- A deployment readiness summary.

It should not create Azure resources, grant roles, publish to Teams, or spend money without a separate approval step in the Copilot session.

## Repo setup contract

A scenario repo should carry the durable context:

- What the agent is supposed to do.
- Which data and tests prove it.
- How to run the local app.
- Which Foundry project and model deployment to use, if already chosen.
- Which actions need approval.

The docs site can provide copy buttons and templates. The repo must own the final values.

## Open decisions

- Whether the plugin marketplace lives in this repo or in a separate website repo.
- Whether the canvas starts the local agent or only connects to one.
- How the plugin installer copies prompts into a target repo.
- How much Teams packaging belongs in Castia versus the scenario repo.
- Which static docs framework hosts the public site long term.

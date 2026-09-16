# Copilot App starter for Castia scenarios

Castia should stay small and reusable. The demo should live in another repository with a specific use case and the data to test it.

That scenario repo needs enough context for a fresh Copilot App session to succeed without a long preface from the user. Give the agent the path and the checks it must run.

## What goes in the scenario repo

Start with these files.

| Path | Purpose |
| --- | --- |
| `.github/copilot-instructions.md` | Durable rules for the repo, including model, cloud, and approval boundaries |
| `.github/prompts/build-castia-agent.prompt.md` | A one click prompt for building the first agent |
| `.github/prompts/test-castia-agent.prompt.md` | A one click prompt for trying the local agent and reporting evidence |
| `README.md` | The human story, quickstart, and expected demo path |
| `tests/` or `evals/` | The proof that the agent works for the chosen use case |

Keep the scenario narrow. "Build a support triage agent for this inbox" is better than "build a useful agent."

## Suggested session contract

Put this in the scenario repo instructions and tailor the names.

```md
# Copilot instructions

This repository is a Castia scenario repo. It demonstrates one Foundry hosted agent end to end.

Use Castia from the package version pinned by this repo. Do not edit Castia itself from this workspace.

The default path is:

1. Build or update the local agent.
2. Run the local tests or evals.
3. Try the agent locally through the Castia canvas if it is available.
4. Ask before provisioning, deploying, granting permissions, publishing to Teams, or spending money.
5. After deployment, record the endpoint, model deployment, and Teams handoff steps in the repo.

Do not assume a model deployment name. Discover it or ask.

Prefer GPT-5 class models or newer when the Foundry project has them. If it does not, use the best available model only after saying what changed.
```

## First prompt

Use [`.github/prompts/build-castia-agent.prompt.md`](../.github/prompts/build-castia-agent.prompt.md) as the starter prompt. Keep that file canonical so the docs do not drift from the thing Copilot actually runs.

## Where the icon fits

Use [`assets/castia-mark.svg`](assets/castia-mark.svg) from this repo for repo docs and install cards. Copy the SVG into the scenario repo rather than hotlinking it from GitHub.

On the website, place the mark next to the install step.

```md
Install the Castia Copilot extras, then open the scenario repo and run the starter prompt.
```

# Copilot instructions for castia

castia is a polyglot monorepo of SDKs for Microsoft Foundry hosted agents.
Language packages live under `packages/<lang>/` (today: `packages/python`), and
cross-SDK protocol facts are pinned under `spec/`.

## Commits and releases (important)

- **Always use [Conventional Commits](https://www.conventionalcommits.org/)** for
  every commit and every PR title. Releases are automated by release-please and
  are driven entirely by these messages. A non-conforming message breaks release
  automation.
- Use a **package scope**: `feat(python): ...`, `fix(python): ...`. Omit the scope
  only for cross-cutting changes (spec, root docs, CI).
- Common types: `feat` (minor bump), `fix` (patch), and non-releasing
  `docs`/`chore`/`refactor`/`test`/`ci`/`build`. Mark breaking changes with `!`
  or a `BREAKING CHANGE:` footer.
- We squash-merge, so the **PR title must itself be a valid Conventional Commit**.
- Do **not** hand-edit `[project].version` in `pyproject.toml`, `CHANGELOG.md`, or
  `.release-please-manifest.json` — release-please owns those.

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full policy and the release
flow.

## Python package conventions

- Tooling is `uv` + `hatchling`. There is no `pip` in the venvs — use `uv pip`.
- Validate from `packages/python`: `uv pip install -e ".[deploy,optimize,test]"`,
  then `.venv/bin/python -m pytest -q -W error`, `uvx ruff check .`,
  `uv build`, and `git --no-pager diff --check`.
- Keep `import castia` cheap: heavy Azure/OpenAI imports stay lazy, inside the
  methods that need them.
- Optimizer ownership is split deliberately: castia owns `.agent_configs`,
  toolbox optimizer sidecars, and `python -m castia optimize run/status/cancel/apply`;
  `azd` remains the deployment rail for hosted agents. The daily
  `.github/workflows/foundry-optimizer-live.yml` job is the preview-service drift
  canary and is billable when enabled.

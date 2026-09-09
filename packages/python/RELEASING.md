# Releasing `castia` (Python)

Releases are automated by [release-please](https://github.com/googleapis/release-please)
and published to PyPI with **Trusted Publishing** (OIDC) — no API token is stored
in the repository. You should never build or upload from a laptop.

## One-time PyPI setup (before the first publish)

`castia` is not on PyPI yet, so register a **pending** trusted publisher:

1. Go to <https://pypi.org/manage/account/publishing/>.
2. Add a new pending publisher with exactly:
   - **PyPI project name:** `castia`
   - **Owner:** `sethjuarez`
   - **Repository name:** `castia`
   - **Workflow name:** `release-please.yml`
   - **Environment name:** `pypi`
3. (Recommended) In the GitHub repo, create a **`pypi` environment**
   (Settings → Environments) and add required reviewers so a human approves each
   publish.

After the first successful publish, the pending publisher becomes a normal
trusted publisher automatically.

## The steady-state flow

1. Merge Conventional-Commit PRs into `main` (see [`CONTRIBUTING.md`](../../CONTRIBUTING.md)).
2. release-please maintains an open **release PR** titled like
   `chore(main): release python 0.2.0`. It bumps `pyproject.toml`'s version and
   updates `CHANGELOG.md`.
3. **Merging that release PR** creates the tag `python-v<version>` and a GitHub
   release, then the `release-please` workflow's `publish-python` job builds the
   wheel + sdist and uploads them to PyPI.

You do not push tags by hand; merging the release PR is the release action.

## First release (0.1.0) bootstrap

`.release-please-manifest.json` starts the `packages/python` component at
`0.0.0`. The commit that introduces release-please carries a
`Release-As: 0.1.0` footer, which makes release-please open its first release PR
at **0.1.0** (matching the version already in `pyproject.toml`) regardless of the
commit types before it. Merging that first release PR publishes `castia 0.1.0`.

No sticky configuration is left behind: after 0.1.0 ships, the manifest advances
to `0.1.0` and subsequent versions are derived normally from Conventional
Commits.

## Versioning scheme

- Per-language tags: the Python SDK releases as `python-v<version>` (e.g.
  `python-v0.1.0`). A future Rust SDK would release independently as
  `rust-v<version>`.
- Pre-1.0: `feat` → minor, `fix` → patch, breaking changes are **not**
  auto-promoted to a major (`bump-minor-pre-major`).

## Verifying a release build locally (optional)

You never publish locally, but you can reproduce what CI builds:

```bash
cd packages/python
uv build
uvx twine check dist/*
```

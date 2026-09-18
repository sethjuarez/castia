# Releasing `castia` (Python)

Python releases use [release-please](https://github.com/googleapis/release-please)
and PyPI Trusted Publishing. Publish from the GitHub workflow, never from a
developer machine. Do not edit the package version, `CHANGELOG.md`, or
`.release-please-manifest.json` by hand.

## Release flow

1. Open a PR targeting `main` with a Conventional Commit title. Use
   `feat(python)!: ...` when shipping features with breaking import changes.
   The squash title determines the release; branch commit messages alone do not.
2. Wait for both Python CI jobs and review the code, documentation, and migration
   guidance before merging.
3. release-please opens or updates the Python release PR with the calculated
   version and changelog. Review that PR separately.
4. Merging the release PR creates `python-v<version>` and a GitHub release.
   The same workflow checks out that tag, builds the distribution, checks the
   installed wheel, and publishes through the `pypi` environment.

Merging a feature PR does not immediately publish a package. Merging the
release PR authorizes publication, subject to any environment approval.
Do not create release tags manually.

Before 1.0, features increment the minor version and fixes increment the patch.
Breaking changes increment the minor version because
`bump-minor-pre-major` is enabled. Future language packages will have separate
release PRs and tags.

## Quality gates

`python-ci` runs on Python 3.11 and 3.13. Both jobs install the `deploy`,
`optimize`, and `test` extras, then run:

- Ruff and the full test suite with warnings treated as errors.
- An sdist build, followed by a wheel built from that sdist.
- Strict Twine metadata checks.
- Installation into a separate environment, followed by dependency checks,
  isolated CLI help and Responses smoke checks, and package architecture and
  offline agent tests against the installed wheel.

The artifact check rejects imports from an editable source checkout. It also
checks the installed version, package root files, lazy imports, and public
exports. The publish job repeats the metadata and installed-artifact smoke
checks on the release tag before uploading.

CI uses offline tests. Live Foundry checks require an approved project and
explicit authorization for billable calls. Record the SDK revision, model,
target, selected coverage, failures, and actual tool calls where relevant.
See [LIFECYCLE.md](LIFECYCLE.md) for the live runner.
Do not describe a local consumer calling Foundry as a new hosted deployment,
or controlled candidate fixtures as optimizer-generated improvements.

SFT, DPO, and RFT submission remain provisional until real training jobs
validate their wire contracts. A release check must not submit training or
deploy a trained checkpoint as an incidental smoke test.

## Reproduce the checks on Windows

From `packages/python`, with `uv` installed and fresh test environments:

```powershell
uv venv --python 3.13
uv pip install -e ".[deploy,optimize,test]"
uvx ruff check .
.\.venv\Scripts\python.exe -m pytest -q -W error
$artifacts = Join-Path ([System.IO.Path]::GetTempPath()) ("castia-release-" + [guid]::NewGuid())
uv build --sdist --out-dir $artifacts
$sdist = Get-ChildItem "$artifacts\*.tar.gz"
uv build --wheel $sdist.FullName --out-dir $artifacts
uvx twine check --strict "$artifacts\*"
uv venv .wheel-venv --python 3.13
$wheel = Get-ChildItem "$artifacts\*.whl"
$wheelUri = ([System.Uri]$wheel.FullName).AbsoluteUri
uv pip install --python .wheel-venv\Scripts\python.exe "castia[deploy,optimize,test] @ $wheelUri"
uv pip check --python .wheel-venv\Scripts\python.exe
.\.wheel-venv\Scripts\python.exe -I -W error scripts\check_distribution.py
.\.wheel-venv\Scripts\python.exe -I -m pytest -q -W error tests\test_package_architecture.py tests\test_building_harness.py
git --no-pager diff --check
```

Use a fresh artifact directory for release verification so old distributions
cannot be mistaken for the current build. Repeat the source and artifact tests
with Python 3.11. The workflows contain the equivalent Linux commands.

## Trusted Publishing setup and recovery

`castia` is already published on PyPI. Its trusted publisher must match:

| Setting | Value |
|---|---|
| PyPI project | `castia` |
| GitHub owner / repository | `sethjuarez` / `castia` |
| Workflow | `release-please.yml` |
| Environment | `pypi` |

Required reviewers on the GitHub `pypi` environment can gate publication.
Do not add API tokens to the repository to work around an OIDC failure.

If publication fails, inspect the failed workflow and fix the reported cause.
Check whether PyPI already has that version before attempting recovery;
published version files cannot be replaced. Do not rerun an entire successful
release workflow blindly. A second release-please invocation may not emit the
same release-created output. Preserve the tag and inspect the failed publish
job's retry options instead.

# Contributing to castia

## Where code belongs

Use the Python [capability packages](packages/python/README.md#package-organization)
for implementation changes. The flat modules directly under `src/castia` are
compatibility imports; do not add behavior there. Internal imports use the
capability paths. Keep public imports working, including module identity for
callers that patch dependencies.

Keep CLI handlers with their capability. The root CLI only composes commands.
Keep Azure/OpenAI imports at the operation that needs them so importing the
runtime does not initialize optional tooling or clients.

Shared wire shapes and behavior belong in [`spec`](spec/README.md). When
introducing Typra, keep the schema source in the shared spec and emitted files
separate from handwritten code. Runtime adapters own credentials, HTTP clients,
storage, and local process execution. Every new SDK must exercise the same
behavioral fixtures; matching generated fields does not prove runtime parity.

Run the Python tests after moving modules. The architecture tests cover both
import paths, patch propagation, public exports, and imports that must stay
lazy. Build the distribution as well so module moves do not leave files out of
the installed package.

## Commit messages: Conventional Commits (required)

Releases are automated with
[release-please](https://github.com/googleapis/release-please), which derives the
next version and the changelog from commit messages. Every commit that lands on
`main` **must** follow the [Conventional Commits](https://www.conventionalcommits.org/)
format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

### Types and how they affect the version

| Type | Example | Version bump |
| --- | --- | --- |
| `feat` | `feat(python): add adaptive-card builder` | **minor** (`0.x` → `0.(x+1)`) |
| `fix` | `fix(python): correct entity serialization` | **patch** (pre-1.0: patch) |
| `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style` | `docs: document eval CLI` | no release on its own |
| Breaking change | `feat(python)!: rename Router` or a `BREAKING CHANGE:` footer | **major** (post-1.0) |

Notes:
- Pre-1.0, a `feat` is a **minor** bump and breaking changes are **not** promoted
  to a major automatically (`bump-minor-pre-major` is on).
- **Scope by package** so release-please attributes the change to the right
  component: use `python` (e.g. `feat(python): ...`). Future SDKs get their own
  scope (e.g. `rust`). Cross-cutting changes (spec, CI, root docs) may omit the
  scope.

### Squash-merge titles matter

We squash-merge PRs, so the **PR title becomes the commit on `main`** — it must
be a valid Conventional Commit. release-please reads that squashed message, not
the individual branch commits.

## Release flow (maintainers)

1. Land Conventional-Commit PRs on `main`.
2. release-please keeps an open **release PR** per package that accumulates the
   version bump + `CHANGELOG.md`. Review it as the release notes.
3. Merging the release PR tags the release (`python-v<version>`) and the
   `release-please` workflow builds and publishes the Python package to PyPI via
   Trusted Publishing.

See [`packages/python/RELEASING.md`](packages/python/RELEASING.md) for the
one-time PyPI Trusted Publisher setup and the first-release bootstrap.

# castia spec

The language-neutral source of truth every castia SDK implements. Keeping the
protocol contracts and conformance fixtures here — rather than duplicated inside
each SDK — is what keeps the Python, Rust, and future implementations honest as
they evolve independently.

## Layout

```
spec/
├─ main.tsp       # aggregate TypeSpec generation root
├─ protocols/     # protocol contracts each SDK speaks
├─ messaging/     # portable Teams message builders, invokes, identity and routing seams
├─ optimizing/    # optimizer configuration contracts and seams
└─ conformance/   # language-neutral fixtures / golden cases the SDKs test against
```

## TypeSpec and Typra

Typra is configured from this folder. Install/update the local generator
dependencies once, then regenerate the committed output:

```powershell
Set-Location spec
npm install
npm run generate
```

`main.tsp` is only the aggregate generation root. Contract ownership is split
into capability namespaces that mirror the Python package map:

| TypeSpec namespace | Source file | Python reference package |
| --- | --- | --- |
| `Castia.Spec.Protocols` | `protocols/activity.tsp` | `castia.protocols`, `castia.messaging` |
| `Castia.Spec.Messaging` | `messaging/*.tsp` | `castia.messaging`, `castia.hosting.identity` |
| `Castia.Spec.Optimizing` | `optimizing/config.tsp` | `castia.optimizing` |

Typra currently emits Rust models and compile-only interface scaffolds into the
Rust SDK package under `../packages/rust/src/model/`, with generated tests under
`../packages/rust/tests/generated/`. The Rust package also includes parity tests
that prove the generated Activity wire model can reproduce the current Python
Activity alias and helper semantics. Run:

```powershell
Set-Location packages\rust
cargo test
cargo test --features serde
```

`@sample` and `@vector` are useful for examples and callable seam checks, but
they do not replace `conformance/`. Keep stateful behavioral contracts there:
optimizer precedence, lifecycle hashing, immutable writes, deployment locking,
fail-closed acceptance, cancellation, and other rules that need multi-step
fixtures or nontrivial oracles.

The optimizer config source is modeled as a string for now. Typra's Rust target
currently emits mismatched enum variant names/tests for snake_case string-union
wire values such as `inline_config`; the portable source-precedence behavior is
still pinned by `conformance/optimization/candidate_precedence.json`.

Some Teams schema.org payload keys such as `@type`, `@context`, `@id`, and
Adaptive Card `$schema` cannot be represented directly in the current inline
TypeSpec vector literals. Generated vectors cover the portable callable seam and
the Rust handwritten parity tests assert the exact Teams wire shape until Typra
adds a first-class escape hatch for those JSON keys.

## Protocols

castia agents speak three Foundry wire protocols. Contract documents belong
under `protocols/`, describing the request/response shapes and semantics an SDK
must honor. That directory is currently a placeholder; the implementation and
Python tests contain behavior still to be extracted.

- **Activity** — Teams / Bot Framework message and invoke turns.
- **`responses`** — the OpenAI `responses` wire shape.
- **`invocations`** — Foundry invoke envelopes (tool execution, agent-to-agent).

## Conformance

`conformance/` holds registry-neutral fixtures (JSON golden cases: an input
payload plus the expected builder output / decision) that every SDK runs in its
own test suite. A new protocol behavior should land as a fixture here first, then
be implemented in each SDK against it, so parity is verifiable rather than
assumed.

The current shared fixtures cover rubric dimensions, optimizer candidate
precedence, RFT grader validation, and lifecycle records. They do not yet cover
every capability in the Python SDK. RFT service acceptance remains provisional
as recorded in the grader fixtures.

## Ownership before adding runtimes

| Layer | Owner | Examples |
| --- | --- | --- |
| Shared contracts | `spec/` | Wire fields, aliases, configuration shapes, durable record formats |
| Shared behavior | `spec/conformance/` | Validation failures, precedence rules, canonical serialization, acceptance decisions |
| Native implementation | `packages/<lang>/` | Routing, model execution, evaluation, optimizer and delivery operations |
| Host adapters | Each SDK's capability packages | Credentials, HTTP clients, telemetry exporters, storage, subprocesses |

The Python [package map](../packages/python/README.md#package-organization)
locates each capability. A future SDK should preserve those responsibilities
while using its own language's module conventions.

TypeSpec schema inputs belong in the shared spec. Generated runtime output
belongs with the SDK package that builds it. Each SDK keeps generated types
separate from handwritten behavior and host adapters, with reproducible
generation checks. Avoid putting Python decorators, SDK clients, credentials, or
callbacks into a portable data contract.

Parity needs two checks. Generated models must preserve the same data, and the
real implementations must produce the same behavior for shared fixtures.
Serialization-only tests cannot establish that routing, recall, cancellation,
or other runtime behavior exists. Track uncovered behavior explicitly when
adding another runtime.

There is no published spec version or per-runtime conformance declaration yet.
Extract contracts incrementally and record their coverage before claiming a
runtime implements the whole lifecycle.

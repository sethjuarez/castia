# castia spec

The language-neutral source of truth every castia SDK implements. Keeping the
protocol contracts and conformance fixtures here — rather than duplicated inside
each SDK — is what keeps the Python, Rust, and future implementations honest as
they evolve independently.

## Layout

```
spec/
├─ protocols/     # the wire contracts each SDK speaks
└─ conformance/   # language-neutral fixtures / golden cases the SDKs test against
```

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

Typra is not configured in this repository yet. When generation is added,
schema inputs belong in the shared spec. Each SDK should keep generated types
separate from handwritten behavior and host adapters, with a reproducible
generation check. Avoid putting Python decorators, SDK clients, credentials,
or callbacks into a portable data contract.

Parity needs two checks. Generated models must preserve the same data, and the
real implementations must produce the same behavior for shared fixtures.
Serialization-only tests cannot establish that routing, recall, cancellation,
or other runtime behavior exists. Track uncovered behavior explicitly when
adding another runtime.

There is no published spec version or per-runtime conformance declaration yet.
Extract contracts incrementally and record their coverage before claiming a
runtime implements the whole lifecycle.

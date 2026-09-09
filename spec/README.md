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

castia agents speak three Foundry wire protocols. Each gets a contract document
under `protocols/` describing the request/response shapes and semantics an SDK
must honor:

- **Activity** — Teams / Bot Framework message and invoke turns.
- **`responses`** — the OpenAI `responses` wire shape.
- **`invocations`** — Foundry invoke envelopes (tool execution, agent-to-agent).

## Conformance

`conformance/` holds registry-neutral fixtures (JSON golden cases: an input
payload plus the expected builder output / decision) that every SDK runs in its
own test suite. A new protocol behavior should land as a fixture here first, then
be implemented in each SDK against it, so parity is verifiable rather than
assumed.

The spec is versioned independently of the SDKs; each SDK declares which spec
version it conforms to.

> Status: scaffolding. Protocol contracts and fixtures are extracted here
> incrementally from the validated Python implementation in
> [`../packages/python`](../packages/python).

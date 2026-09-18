# castia Rust

Experimental Rust SDK/runtime work for Castia.

This crate is not published and should not be treated as a stable public API.
It exists to prove that the shared TypeSpec/Typra contracts in `../../spec`
can govern another language implementation without copying Python's package
shape line-for-line.

## What is here

- Generated TypeSpec/Typra models, traits, and vector conformance tests under
  `src/model` and `tests/generated`.
- Handwritten Rust implementations for portable behavior seams across
  protocols, runtime, messaging, inference helpers, integrations, evaluation,
  optimizing, lifecycle, observation, build/delivery planning, hosting helpers,
  and fine-tuning preparation/job-management helpers.
- An optional `serde` feature used by generated/native serialization checks.

## What is not here yet

- A stable crates.io release.
- A full idiomatic Rust application framework equivalent to Python's
  FastAPI-style `Agent`.
- Complete Azure SDK clients, CLI workflows, Teams publishing, or automatic
  execution of billable optimizer/fine-tuning operations.

The current hosted proof lives in `../../examples/rust/minimal-agent`. It serves
`/readiness` and `/responses` with Axum and can be deployed as a Foundry hosted
agent container for smoke validation.

## Validate

```powershell
Set-Location packages\rust
cargo test
cargo test --features serde
```

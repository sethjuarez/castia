# Conformance fixture: optimizer candidate-injection precedence

`candidate_precedence.json` is the language-neutral golden for **how a hosted
agent resolves its active configuration** when the Microsoft Foundry Agent
Optimizer is in play. It was validated live: setting
`OPTIMIZATION_CONFIG='{"instructions":"…","model":"gpt-4o-mini"}'` made the
running agent report `source=env:OPTIMIZATION_CONFIG` with the overridden
values, proving the code path is byte-identical between the baseline and an
injected candidate.

## The contract every castia SDK must honor

Resolution is **first-wins** over an ordered list of sources:

1. **`inline_config`** — `OPTIMIZATION_CONFIG`, an inline JSON blob.
2. **`resolver_api`** — `OPTIMIZATION_CANDIDATE_ID` + `OPTIMIZATION_RESOLVE_ENDPOINT`.
3. **`local_agent_configs`** — the on-disk `.agent_configs/` baseline directory.
4. **`none`** — nothing resolved; the SDK bridge degrades to environment
   defaults (env model, no instructions).

The load must be **best-effort and non-fatal**: a missing optimizer package, a
resolution error, or `none` all fall through to environment defaults so the
agent runs identically with or without the optimizer installed.

### The `config_dir` rule

An explicit `config_dir` (or the `OPTIMIZATION_LOCAL_DIR` override) affects
**only** the `local_agent_configs` source — it never changes candidate
injection via `inline_config` or `resolver_api`. This matters because the local
directory otherwise resolves against the entrypoint's location, which differs
between `python app.py` and `python -m castia`; anchoring it explicitly keeps
the baseline resolvable no matter how the process is launched.

## Why this is pinned here

The resolution itself lives in Foundry's `azure-ai-agentserver-optimization`
runtime, not in castia — the SDK only *bridges* to it (see
`packages/python/src/castia/optimization.py`, `load_agent_config`). Pinning the
precedence and the `config_dir` rule as a language-neutral fixture is exactly
the cross-SDK fact a future Rust (or other) SDK must reimplement identically.
The Python SDK reads this fixture in
`packages/python/tests/test_spec_optimization.py` so the documented contract
cannot silently rot.

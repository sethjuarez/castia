# Conformance fixture: rubric dimensions

`rubric_dimensions.json` is the language-neutral golden for the **rubric
dimensions file** that Microsoft Foundry's `azd ai agent eval generate` writes
and that every castia SDK must read. It was validated against a live
`eval generate` run, not reverse-engineered.

## The shape a conforming reader must accept

- The file is a **bare JSON list** — it is *not* wrapped in a `dimensions:` key.
- Each entry is `{ id, description, weight }`.
  - **`id`** is a stable slug (e.g. `correct_outcome`), and is the canonical
    key. `name` is accepted only as a legacy alias; when both are present, `id`
    wins.
  - **`weight`** is a number.
- The trailing catch-all entry may carry **`always_applicable: true`**.

These two facts — the **`id`-not-`name`** key and the **bare-list** (no
`dimensions:` wrapper) form — are what a reimplementation in any language must
get right. The Python SDK reads this exact fixture in
`packages/python/tests/test_evalsuite.py` so the SDK and the spec cannot drift.

# Generated Castia metadata

This directory contains Typra review metadata and the JSON contract AST.
Regenerate it from `spec/` with:

```powershell
npm run generate
```

The TypeSpec source of truth lives directly under `spec/` in capability
directories such as `protocols/` and `optimizing/`. Runtime generated output
belongs in the package that builds it, such as `packages/rust/src/model/`.
Castia runtime behavior still lives in each language package and must pass
`spec/conformance/` fixtures.

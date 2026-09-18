# Rust examples

These examples are checked-in proving grounds for the Rust SDK as the TypeSpec
and Typra contracts move toward Python parity.

## Minimal agent

[`minimal-agent`](minimal-agent) is a small Rust smoke app that exercises the
same pure seams the Python minimal agent uses for the Responses path:

1. validate startup settings;
2. normalize a Responses `input` payload;
3. build a Castia Responses-shaped reply body;
4. optionally call a real Foundry project through the OpenAI Responses endpoint.

From the repository root:

```powershell
Set-Location examples\rust\minimal-agent
cargo run -- --offline
```

For a live model call, copy `.env.example` to `.env`, fill the project endpoint
and deployment name, sign in with `az login`, then run:

```powershell
cargo run -- --live --prompt "Say hello from the Rust minimal agent."
```

The live smoke only calls the configured model deployment. It does not create,
submit, cancel, or mutate optimizer/fine-tuning jobs.

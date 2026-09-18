# Rust examples

These examples are checked-in proving grounds for the experimental Rust SDK as
the TypeSpec and Typra contracts move toward Python parity. They are not a
published Rust package contract yet.

## Minimal agent

[`minimal-agent`](minimal-agent) is a small Rust smoke app that exercises the
same pure seams the Python minimal agent uses for the Responses path:

1. validate startup settings;
2. normalize a Responses `input` payload;
3. build a Castia Responses-shaped reply body;
4. optionally call a real Foundry project through the OpenAI Responses endpoint;
5. serve `/readiness` and `/responses` for hosted-agent smoke deployment.

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

For a local hosted-protocol smoke:

```powershell
$env:HOST = "127.0.0.1"
$env:PORT = "8088"
cargo run -- --serve
```

Then post to `http://127.0.0.1:8088/responses` with an OpenAI Responses-shaped
body such as `{"input":"hello"}`.

The `minimal-agent/azure.yaml` file uses a container hosted-agent deployment
with remote ACR build. It is intended for explicit smoke validation against a
selected Foundry project, not as a polished Rust framework starter.

# Agent Playground

Project-scoped Copilot canvas extension for chatting with an agent through
the Responses protocol. It is local-first so the minimal Python example can
validate the workflow before any Foundry deployment, but the endpoint can point
at any reachable agent base URL.

Open the canvas with:

```json
{
  "canvasId": "agent-playground",
  "instanceId": "minimal-agent-chat",
  "input": {
    "endpoint": "http://127.0.0.1:8088"
  }
}
```

## What it tests

The canvas talks to:

- `GET /readiness`
- `POST /responses` with `{ "input": "..." }`
- `POST /responses` with `{ "input": "...", "stream": true }` when the agent supports SSE streaming

The UI includes:

- endpoint configuration for local or hosted agents;
- readiness status and response latency;
- transcript counters for total, passing, and failing turns;
- raw request/response JSON for protocol debugging;
- chat-style keyboard input: Enter sends, Shift+Enter adds a newline.

Transcript state is in memory for the open canvas instance. Closing or reloading
the extension clears the transcript.

## Sharing

When this is ready to share outside the repo, use the app's extension sharing
flow or the `share_extension` tool to publish this folder as an installable
extension gist.

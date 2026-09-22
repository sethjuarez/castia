# Foundry Agent Playground architecture

The playground is a packaged Copilot canvas extension for testing Castia/Foundry agents through the Responses protocol. It should stay a compact local-to-Foundry workflow, not become a general canvas framework.

## Current implementation problems

- Readiness, selected-agent identity, protocol support, and configuration have historically collapsed into one `ok`/failed readiness shape.
- UI clicks and Copilot canvas actions have grown as separate paths, which makes equivalent user/Copilot operation semantics harder to guarantee.
- Runtime state is mostly process memory today, so extension reloads lose operation context unless the canvas can rediscover it.
- Long-running lifecycle commands have limited operation identity, cancellation, and phase diagnostics.

## Product state model

The user-facing model has four states:

1. **Opened / Local setup** - a selected agent is visible, Local is the primary action, and missing Foundry project configuration opens the in-canvas endpoint dialog.
2. **Running / Local mode** - the selected local agent is starting or ready, readiness diagnostics are scoped to that selected agent, and Local chat is the primary path.
3. **Foundry mode** - hosted discovery/deploy state is shown only when useful, and the same transcript workflow can target the hosted Responses endpoint.
4. **State preserved across Local and Foundry unless explicitly cleared** - Local and Foundry transcripts remain target-scoped. Switching selected agents is an explicit boundary and must clear stale readiness, transcript, and target-specific state from the previous agent.

Readiness is not identity. A reachable endpoint can still be invalid for the selected agent, missing required configuration, or missing required protocol support. Backend state and UI should keep these dimensions separate:

- `readinessStatus`
- `identity`
- `protocolSupport`
- `configurationStatus`

Healthy endpoint details stay hidden unless they provide a useful diagnostic.

## User and Copilot command equivalence

UI clicks and canvas actions should call the same logical command handlers where feasible. The current packaged extension exposes agent-facing actions such as `set_target`, `health_check`, `send_response`, `get_transcript_state`, `clear_transcript`, `select_agent`, and `start_local`; matching iframe routes should route through the same state transitions.

Copilot gets structured diagnostics through the bridge rather than scraping renderer text. Diagnostic action/state surfaces should remain deterministic:

- `get_activity_state`
- `get_operation_state`
- `get_latest_diagnostics`
- `get_foundry_state`
- `get_next_actions`

These are intended next-action bridges, not renderer-only UI labels.

## Command concurrency

Commands should use first-in-wins concurrency:

- Disable incompatible UI controls while an operation is running.
- Duplicate commands should return the current operation state rather than starting a second operation.
- Long-running operations should have a stable operation id and phase.

## Phase-aware cancellation

Deploy cancellation should be phase-aware:

- Before irreversible remote Foundry phases, cancellation can stop the local command.
- After remote transfer or registration begins, cancellation becomes wait-for-settle and reports the eventual remote state.

This prevents the canvas from claiming an operation was canceled when Foundry may still be registering or serving a version.

## Session-scoped persistence

Runtime logical state belongs in session-scoped JSON/JSONL files, not repo files:

- `state.json` stores the current durable logical snapshot.
- `operations.jsonl` stores append-only operation history.
- Runtime handles such as child processes, HTTP servers, SSE clients, and cancellation tokens remain ephemeral and are reattached or marked stale after reload.

Committed repo files should only be source/docs/assets. Gitignored `.env` files are allowed for non-secret Foundry values that the user provides through the Start local endpoint dialog.

## Implementation status

Implemented now:

- Structured readiness dimensions for endpoint readiness, selected-agent identity, protocol support, and configuration status.
- Shared command handlers for agent selection and local startup across UI routes and Copilot canvas actions.
- Session-scoped `state.json` and `operations.jsonl` persistence under Copilot session files storage.
- First-in-wins operation tracking for deploy/provision lifecycle commands.
- Phase-aware cancellation requests: stop before irreversible remote phases, wait-for-settle after remote registration begins.
- Deterministic diagnostic bridge actions for activity, operations, diagnostics, Foundry state, and next actions.
- Principled module folders for canvas actions, protocol clients, domain helpers, renderer files, HTTP route helpers, state helpers, and tests while preserving the SDK entrypoint at the extension root.

Still intentionally future/optional:

- A full renderer migration to Svelte + TypeScript.
- A wholesale HTTP route split beyond the extracted route primitives and shared canvas action factory.
- XState for lifecycle-heavy state machines.

## Possible future architecture

Future cleanup can move toward:

- A backend reducer with shared command handlers.
- Modular `routes`, `actions`, `domain`, and `state` files.
- An optional Svelte + TypeScript renderer if the UI grows beyond simple DOM updates.
- No Redux; the state surface is small and session-scoped.
- Consider XState only for lifecycle-heavy local runner or deploy state, where explicit phases and cancellation semantics pay for the extra model.

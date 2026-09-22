import assert from "node:assert/strict";
import test from "node:test";
import {
    clearProjectEndpointPrompt,
    createPlaygroundCommands,
    setProjectEndpointPrompt,
} from "../commands/playground-commands.mjs";
import { beginOperation, emptyOperationState, enterIrreversiblePhase } from "../domain/operations.mjs";

function commandFactory(overrides = {}) {
    return createPlaygroundCommands({
        selectAgent: async (_state, agentId) => ({ id: agentId, displayName: "Agent One", serviceName: "agent-one" }),
        refreshHostedContext: async () => {},
        snapshotState: () => ({ snapshot: true }),
        syncLocalBootstrapForStart: async () => ({ ok: true, source: "test" }),
        startLocalAgent: async () => {},
        recordOperation: () => {},
        ...overrides,
    });
}

test("project endpoint prompt helpers set and clear in-canvas prompt state", () => {
    const state = { selectedAgentId: "agent-1" };

    const prompt = setProjectEndpointPrompt(state, "missing_test_config");

    assert.equal(prompt.open, true);
    assert.equal(prompt.reason, "missing_test_config");
    assert.equal(prompt.selectedAgentId, "agent-1");
    assert.match(prompt.message, /Foundry project endpoint/);

    clearProjectEndpointPrompt(state);

    assert.equal(state.projectEndpointPrompt, null);
});

test("commandSelectAgent selects, refreshes, records activity, and returns snapshot", async () => {
    let refreshed = false;
    const state = {};
    const commands = commandFactory({
        selectAgent: async (_state, agentId) => ({ id: agentId, displayName: "Agent One", serviceName: "agent-one" }),
        refreshHostedContext: async () => {
            refreshed = true;
        },
    });

    const result = await commands.commandSelectAgent(state, "agent-1", { actor: "Copilot" });

    assert.equal(refreshed, true);
    assert.deepEqual(result, { snapshot: true });
    assert.equal(state.activity.at(-1).kind, "select_agent");
    assert.equal(state.activity.at(-1).actor, "Copilot");
});

test("commandStartLocal opens endpoint prompt when local configuration is missing", async () => {
    const state = {};
    const commands = commandFactory({
        syncLocalBootstrapForStart: async () => ({ ok: false, source: "missing" }),
    });

    const result = await commands.commandStartLocal(state, { actor: "You" });

    assert.equal(result.ok, false);
    assert.equal(result.needsProjectEndpoint, true);
    assert.equal(result.projectEndpointPrompt.open, true);
    assert.deepEqual(result.state, { snapshot: true });
    assert.equal(state.activity.at(-1).kind, "start_local");
    assert.equal(state.activity.at(-1).status, "blocked");
});

test("commandStartLocal starts configured local agent and returns snapshot", async () => {
    let started = false;
    const state = {
        selectedAgentId: "agent-1",
        agents: [{ id: "agent-1", displayName: "Agent One", serviceName: "agent-one" }],
        localEndpoints: { "agent-1": "http://127.0.0.1:8088" },
    };
    const commands = commandFactory({
        startLocalAgent: async () => {
            started = true;
        },
    });

    const result = await commands.commandStartLocal(state, { actor: "Copilot" });

    assert.equal(started, true);
    assert.equal(result.ok, true);
    assert.deepEqual(result.state, { snapshot: true });
    assert.equal(state.target, "local");
    assert.equal(state.activity.at(-1).kind, "start_local");
    assert.equal(state.activity.at(-1).status, "running");
});

test("commandCancelOperation aborts and records stop-requested cancellation", () => {
    let aborted = false;
    const records = [];
    const state = {
        deployment: {
            abortController: {
                abort: () => {
                    aborted = true;
                },
            },
        },
        operations: emptyOperationState(),
    };
    beginOperation(state, { kind: "deploy", actor: "You", phase: "starting", cancellable: true });
    const commands = commandFactory({
        recordOperation: (_state, event, details) => records.push({ event, details }),
    });

    const result = commands.commandCancelOperation(state, { actor: "Copilot" });

    assert.equal(result.accepted, true);
    assert.equal(result.mode, "stop_requested");
    assert.equal(aborted, true);
    assert.deepEqual(records, [{ event: "cancel_requested", details: { mode: "stop_requested" } }]);
    assert.equal(state.activity.at(-1).kind, "cancel_operation");
});

test("commandCancelOperation skips abort and records wait-for-settle after irreversible phase", () => {
    let aborted = false;
    const records = [];
    const state = {
        deployment: {
            abortController: {
                abort: () => {
                    aborted = true;
                },
            },
        },
        operations: emptyOperationState(),
    };
    beginOperation(state, { kind: "deploy", actor: "You", phase: "starting", cancellable: true });
    enterIrreversiblePhase(state, "remote_registration");
    const commands = commandFactory({
        recordOperation: (_state, event, details) => records.push({ event, details }),
    });

    const result = commands.commandCancelOperation(state, { actor: "Copilot" });

    assert.equal(result.accepted, true);
    assert.equal(result.mode, "wait_for_settle");
    assert.equal(aborted, false);
    assert.deepEqual(records, [{ event: "cancel_requested", details: { mode: "wait_for_settle" } }]);
    assert.equal(state.activity.at(-1).status, "warn");
});

test("commandCancelOperation skips side effects when cancellation is rejected", () => {
    let aborted = false;
    let recorded = false;
    const state = {
        deployment: {
            abortController: {
                abort: () => {
                    aborted = true;
                },
            },
        },
        operations: emptyOperationState(),
    };
    const commands = commandFactory({
        recordOperation: () => {
            recorded = true;
        },
    });

    const result = commands.commandCancelOperation(state, { actor: "Copilot" });

    assert.equal(result.accepted, false);
    assert.equal(aborted, false);
    assert.equal(recorded, false);
    assert.equal(state.activity, undefined);
});

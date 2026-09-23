import assert from "node:assert/strict";
import test from "node:test";
import { createCanvasActions } from "../actions/canvas-actions.mjs";

test("canvas action factory exposes the expected action contract", () => {
    const actions = createCanvasActions({});
    assert.deepEqual(actions.map((action) => action.name), [
        "set_endpoint",
        "select_agent",
        "set_target",
        "set_protocol",
        "start_local",
        "stop_local",
        "configure_project_endpoint",
        "health_check",
        "send_response",
        "send_activity",
        "send_invocation",
        "get_protocol_state",
        "get_transcript_state",
        "get_activity_state",
        "get_operation_state",
        "get_latest_diagnostics",
        "get_foundry_state",
        "get_next_actions",
        "get_telemetry_state",
        "cancel_operation",
        "clear_transcript",
    ]);
    assert.equal(actions.every((action) => typeof action.handler === "function"), true);

    const sendResponse = actions.find((action) => action.name === "send_response");
    assert.deepEqual(sendResponse.inputSchema.required, ["input"]);
    assert.equal(sendResponse.inputSchema.additionalProperties, false);

    const cancelOperation = actions.find((action) => action.name === "cancel_operation");
    assert.equal(cancelOperation.inputSchema.additionalProperties, false);
});

test("configure_project_endpoint action requires the Start local prompt to be open", async () => {
    class TestCanvasError extends Error {
        constructor(code, message) {
            super(message);
            this.code = code;
        }
    }
    const actions = createCanvasActions({
        CanvasError: TestCanvasError,
        instanceState: () => ({ projectEndpointPrompt: null }),
    });
    const configure = actions.find((action) => action.name === "configure_project_endpoint");

    await assert.rejects(
        configure.handler({ input: { projectEndpoint: "https://example.services.ai.azure.com/api/projects/demo" } }),
        (error) => error.code === "project_endpoint_prompt_not_open",
    );
});

test("configure_project_endpoint action submits prompt and starts local mode", async () => {
    let configured = false;
    let started = false;
    const actions = createCanvasActions({
        instanceState: () => ({ projectEndpointPrompt: { open: true } }),
        commandBootstrapLocalEnv: async () => {
            configured = true;
            return { result: { written: [".env"] }, state: { step: "configured" } };
        },
        commandStartLocal: async () => {
            started = true;
            return { ok: true, state: { step: "started" } };
        },
        broadcastSnapshot: () => {},
    });
    const configure = actions.find((action) => action.name === "configure_project_endpoint");

    const result = await configure.handler({
        input: { projectEndpoint: "https://example.services.ai.azure.com/api/projects/demo" },
    });

    assert.equal(configured, true);
    assert.equal(started, true);
    assert.deepEqual(result, {
        result: { written: [".env"] },
        startLocal: { ok: true, state: { step: "started" } },
        state: { step: "started" },
    });
});

test("stop_local action stops local agent and returns updated state", async () => {
    let stopped = false;
    let broadcasted = false;
    const state = { activity: [] };
    const actions = createCanvasActions({
        instanceState: () => state,
        stopLocalAgent: async () => {
            stopped = true;
            state.localRun = { running: false };
        },
        snapshotState: (value) => ({ ...value, snapshotted: true }),
        broadcastSnapshot: () => {
            broadcasted = true;
        },
    });
    const stopLocal = actions.find((action) => action.name === "stop_local");

    const result = await stopLocal.handler({});

    assert.equal(stopped, true);
    assert.equal(broadcasted, true);
    assert.equal(state.activity.at(-1).kind, "stop_local");
    assert.equal(state.activity.at(-1).status, "completed");
    assert.deepEqual(result.localRun, { running: false });
    assert.equal(result.snapshotted, true);
});

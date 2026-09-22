import assert from "node:assert/strict";
import test from "node:test";
import { createCanvasActions } from "../actions/canvas-actions.mjs";

test("canvas action factory exposes the expected action contract", () => {
    const actions = createCanvasActions({});
    assert.deepEqual(actions.map((action) => action.name), [
        "set_endpoint",
        "select_agent",
        "set_target",
        "start_local",
        "health_check",
        "send_response",
        "get_transcript_state",
        "get_activity_state",
        "get_operation_state",
        "get_latest_diagnostics",
        "get_foundry_state",
        "get_next_actions",
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

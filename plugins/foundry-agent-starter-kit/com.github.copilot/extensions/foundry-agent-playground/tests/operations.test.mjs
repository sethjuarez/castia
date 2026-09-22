import assert from "node:assert/strict";
import { test } from "node:test";
import {
    beginOperation,
    completeOperation,
    emptyOperationState,
    enterIrreversiblePhase,
    requestOperationCancel,
    updateOperation,
} from "../domain/operations.mjs";

test("beginOperation uses first-in-wins while an operation is active", () => {
    const state = { operations: emptyOperationState() };
    const first = beginOperation(state, { kind: "deploy", actor: "Copilot" });
    const duplicate = beginOperation(state, { kind: "deploy", actor: "You" });

    assert.equal(first.started, true);
    assert.equal(duplicate.started, false);
    assert.equal(duplicate.duplicate, true);
    assert.equal(duplicate.operation.id, first.operation.id);
});

test("operation cancellation is stop_requested before irreversible phases", () => {
    const state = { operations: emptyOperationState() };
    beginOperation(state, { kind: "deploy" });

    const cancellation = requestOperationCancel(state);

    assert.equal(cancellation.accepted, true);
    assert.equal(cancellation.mode, "stop_requested");
    assert.equal(state.operations.active.status, "cancel_requested");
});

test("operation cancellation waits for settle after irreversible phases", () => {
    const state = { operations: emptyOperationState() };
    beginOperation(state, { kind: "deploy" });
    enterIrreversiblePhase(state, "remote_registration");

    const cancellation = requestOperationCancel(state);

    assert.equal(cancellation.accepted, true);
    assert.equal(cancellation.mode, "wait_for_settle");
    assert.equal(state.operations.active.irreversible, true);
});

test("completed operations move active operation into history", () => {
    const state = { operations: emptyOperationState() };
    beginOperation(state, { kind: "provision" });
    updateOperation(state, { phase: "run_azd" });

    const completed = completeOperation(state, { status: "completed", exitCode: 0 });

    assert.equal(completed.status, "completed");
    assert.equal(state.operations.active, null);
    assert.equal(state.operations.history.at(-1).exitCode, 0);
});

import assert from "node:assert/strict";
import { test } from "node:test";
import {
    activityState,
    foundryState,
    latestDiagnostics,
    nextActions,
    operationState,
} from "../state/diagnostics.mjs";

test("diagnostics bridge exposes activity, operation, foundry, and next actions", () => {
    const state = {
        target: "local",
        selectedAgentId: "agent-1",
        activity: [{ id: "act-1", summary: "Started." }],
        operations: { active: { id: "op-1", status: "running" }, history: [] },
        projectEndpointPrompt: { open: true, message: "Foundry project endpoint is required." },
        lastHealth: {
            ok: false,
            body: "Endpoint belongs to other-agent.",
            identity: { status: "mismatch", expected: "agent-1", actual: "other-agent" },
        },
        foundryConnection: { projectEndpoint: null },
        hosted: { responsesEndpoint: null },
        deployment: { running: false, process: { pid: 1 }, abortController: {} },
        teams: {},
    };

    assert.equal(activityState(state).latest.id, "act-1");
    assert.equal(operationState(state).active.id, "op-1");
    assert.equal(foundryState(state).deployment.process, undefined);
    assert.equal(foundryState(state).deployment.abortController, undefined);
    assert.deepEqual(
        latestDiagnostics(state).diagnostics.map((diagnostic) => diagnostic.code),
        ["project_endpoint_required", "readiness_failed"],
    );
    assert.equal(nextActions(state).actions[0].id, "enter_foundry_project_endpoint");
});

import assert from "node:assert/strict";
import { test } from "node:test";
import {
    activityState,
    foundryState,
    latestDiagnostics,
    nextActions,
    operationState,
    telemetryState,
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

test("telemetry bridge exposes App Insights discovery context and KQL hints", () => {
    const state = {
        selectedAgentId: "examples\\python\\minimal-agent:minimal-agent",
        foundryConnection: {
            projectEndpoint: "https://example.services.ai.azure.com/api/projects/proj",
            projectId: "/subscriptions/sub-1/resourceGroups/rg-foundry/providers/Microsoft.CognitiveServices/accounts/fdry/projects/proj",
            subscriptionId: "sub-1",
            accountName: "fdry",
            projectName: "proj",
        },
        hosted: {
            agentName: "minimal-agent",
            version: "15",
            status: "active",
            responsesEndpoint: "https://example/agents/minimal-agent/endpoint/protocols/openai/responses?api-version=v1",
        },
    };

    const telemetry = telemetryState(state);
    assert.equal(telemetry.status, "ready_to_discover");
    assert.equal(telemetry.observability.connectionCategory, "AppInsights");
    assert.match(telemetry.observability.discoveryCommand, /az cognitiveservices account connection list/);
    assert.match(telemetry.observability.discoveryCommand, /--resource-group rg-foundry/);
    assert.match(telemetry.traceLookup.kqlTemplates.findTrace, /azure\.ai\.agentserver\.x-request-id|customDimensions/);
    assert.equal(nextActions(state).actions.at(-1).id, "find_telemetry");
});

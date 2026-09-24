import assert from "node:assert/strict";
import { test } from "node:test";
import { emptyFoundryConnection, emptyHostedContext, emptyLocalRun } from "../state/snapshot.mjs";
import { rehydratePlaygroundState } from "../state/rehydration.mjs";
import { emptyOperationState } from "../domain/operations.mjs";

const agent = {
    id: "agent-1",
    serviceName: "agent-one",
    displayName: "Agent One",
    root: "examples\\python\\agent-one",
    rootLabel: "examples\\python\\agent-one",
    envPrefix: "AGENT_ONE",
};

const policyAgent = {
    id: "agent-2",
    serviceName: "agent-two",
    displayName: "Agent Two",
    root: "examples\\python\\agent-two",
    rootLabel: "examples\\python\\agent-two",
    envPrefix: "AGENT_TWO",
};

function freshState() {
    return {
        target: "local",
        activeProtocol: "responses",
        agents: [agent, policyAgent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8088" },
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
        foundryConnection: emptyFoundryConnection(),
        envBootstrap: null,
        deployment: {
            running: false,
            exitCode: null,
            startedAt: null,
            completedAt: null,
            command: null,
            needsProvision: false,
            log: [],
        },
        localRun: emptyLocalRun(),
        teams: {},
        messages: [],
        lastHealth: null,
        lastHealthByTarget: {},
        projectEndpointPrompt: null,
        operations: emptyOperationState(),
        eventClients: new Set(),
        runtimeStore: { statePath: "C:\\session\\state.json" },
    };
}

test("rehydratePlaygroundState restores durable state and resets volatile runtime state", () => {
    const state = freshState();
    const snapshot = {
        target: "hosted",
        activeProtocol: "activity",
        selectedAgentId: policyAgent.id,
        localEndpoints: {
            [policyAgent.id]: "http://127.0.0.1:8123/",
            missing: "http://127.0.0.1:9999",
        },
        foundryConnection: {
            projectEndpoint: "https://example.services.ai.azure.com/api/projects/demo",
            modelDeployment: "gpt-4.1",
            lastDiscoveryMessage: "Loaded.",
            token: "must-not-restore",
        },
        hostedByAgent: {
            [policyAgent.id]: {
                responsesEndpoint: "https://example.test/responses",
                activityEndpoint: "https://example.test/activity",
                apiKey: "must-not-restore",
            },
        },
        envBootstrap: {
            previewedAt: "2026-09-24T00:00:00Z",
            values: { FOUNDRY_PROJECT_ENDPOINT: "https://secret.example" },
            targets: [{ path: ".env", writable: true }],
        },
        localRun: {
            running: true,
            process: { pid: 1234 },
            log: ["token=abc"],
        },
        deployment: {
            running: true,
            process: { pid: 4567 },
            abortController: {},
            log: ["raw deployment output"],
        },
        messages: [{ id: "turn-1", target: "hosted", input: "hello", response: { ok: true, body: { output_text: "hi" } } }],
        activity: [
            { id: "act-running", status: "running", kind: "send_response", summary: "running" },
            { id: "act-done", status: "completed", kind: "send_response", summary: "done" },
        ],
        operations: {
            active: { id: "op-1", kind: "deploy", status: "running", phase: "deploying", cancellable: true },
            history: [
                { id: "op-old", kind: "deploy", status: "completed" },
                { id: "op-running", kind: "deploy", status: "running" },
            ],
        },
        lastHealth: { ok: true, status: 200 },
        lastHealthByTarget: { local: { ok: true } },
        projectEndpointPrompt: { open: true },
    };

    const result = rehydratePlaygroundState(state, snapshot);

    assert.deepEqual(result, { restored: true });
    assert.equal(state.selectedAgentId, policyAgent.id);
    assert.equal(state.target, "hosted");
    assert.equal(state.activeProtocol, "activity");
    assert.equal(state.localEndpoints[policyAgent.id], "http://127.0.0.1:8123");
    assert.equal(state.localEndpoints.missing, undefined);
    assert.equal(state.foundryConnection.projectEndpoint, "https://example.services.ai.azure.com/api/projects/demo");
    assert.equal(state.foundryConnection.token, undefined);
    assert.equal(state.hosted.responsesEndpoint, "https://example.test/responses");
    assert.equal(state.hosted.apiKey, undefined);
    assert.deepEqual(state.envBootstrap, {
        previewedAt: "2026-09-24T00:00:00Z",
        targets: [{ path: ".env", writable: true }],
    });
    assert.deepEqual(state.messages.map((message) => message.id), ["turn-1"]);
    assert.equal(state.localRun.running, false);
    assert.equal(state.localRun.process, null);
    assert.deepEqual(state.localRun.log, []);
    assert.deepEqual(state.deployment.log, []);
    assert.equal(state.lastHealth, null);
    assert.deepEqual(state.lastHealthByTarget, {});
    assert.equal(state.projectEndpointPrompt, null);
    assert.equal(state.operations.active, null);
    assert.deepEqual(state.operations.history.map((operation) => [operation.id, operation.status]), [
        ["op-old", "completed"],
        ["op-1", "interrupted"],
    ]);
    assert.deepEqual(state.activity.map((entry) => entry.kind), ["send_response", "restore_state"]);
    assert.equal(state.activity.at(-1).status, "warn");
});

test("rehydratePlaygroundState gives open input precedence over saved selection and endpoint", () => {
    const state = freshState();

    rehydratePlaygroundState(state, {
        selectedAgentId: policyAgent.id,
        localEndpoints: { [policyAgent.id]: "http://127.0.0.1:8123" },
    }, {
        input: {
            agentId: agent.id,
            endpoint: "http://127.0.0.1:9000/",
        },
    });

    assert.equal(state.selectedAgentId, agent.id);
    assert.equal(state.localEndpoints[agent.id], "http://127.0.0.1:9000");
});

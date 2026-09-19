import assert from "node:assert/strict";
import { test } from "node:test";
import {
    clearMessagesForTarget,
    emptyFoundryConnection,
    emptyHostedContext,
    normalizeEndpoint,
    selectedAgent,
    stateSnapshot,
} from "./state.mjs";

const agent = {
    id: "examples\\python\\minimal-agent:minimal-agent",
    serviceName: "minimal-agent",
    displayName: "minimal-agent",
    root: "examples\\python\\minimal-agent",
    rootLabel: "examples\\python\\minimal-agent",
    envPrefix: "AGENT_MINIMAL_AGENT",
};

test("normalizeEndpoint trims trailing slashes and validates http endpoints", () => {
    assert.equal(normalizeEndpoint(" http://127.0.0.1:8088/// "), "http://127.0.0.1:8088");
    assert.throws(() => normalizeEndpoint("file:///tmp/agent"), /Endpoint must be an http\(s\) URL/);
});

test("stateSnapshot filters transcript by active target and strips process handles", () => {
    const state = {
        target: "local",
        agents: [agent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8100" },
        foundryConnection: emptyFoundryConnection(),
        envBootstrap: null,
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
        deployment: null,
        localEnv: null,
        localRun: { status: "running", process: { pid: 1234 } },
        teams: null,
        lastHealth: null,
        messages: [
            { id: "local-1", target: "local", response: { ok: true, status: 200, durationMs: 10 } },
            { id: "hosted-1", target: "hosted", response: { ok: true, status: 200, durationMs: 100 } },
        ],
    };

    const snapshot = stateSnapshot(state);

    assert.equal(snapshot.endpoint, "http://127.0.0.1:8100");
    assert.deepEqual(snapshot.visibleMessages.map((message) => message.id), ["local-1"]);
    assert.equal(snapshot.localRun.process, undefined);
    assert.deepEqual(snapshot.stats, {
        total: 1,
        completed: 1,
        failed: 0,
        averageMs: 10,
        lastStatus: 200,
    });
});

test("clearMessagesForTarget clears only the selected target transcript", () => {
    const state = {
        messages: [
            { id: "local-1", target: "local" },
            { id: "hosted-1", target: "hosted" },
            { id: "hosted-2", target: "hosted" },
        ],
    };

    clearMessagesForTarget(state, "hosted");

    assert.deepEqual(state.messages.map((message) => message.id), ["local-1"]);
});

test("selectedAgent falls back to the default minimal agent", () => {
    assert.equal(selectedAgent({ agents: [], selectedAgentId: null }).serviceName, "minimal-agent");
});

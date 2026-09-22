import assert from "node:assert/strict";
import { test } from "node:test";
import {
    addActivity,
    clearMessagesForTarget,
    copyableAnswerText,
    emptyFoundryConnection,
    emptyHostedContext,
    emptyLocalRun,
    latestCopyTarget,
    normalizeEndpoint,
    responseDisplayText,
    responsePendingLabel,
    selectedAgent,
    selectedLocalEndpoint,
    setSelectedAgent,
    switchSelectedAgent,
    stateSnapshot,
    transcriptState,
    updateActivity,
} from "./state/snapshot.mjs";

const agent = {
    id: "examples\\python\\minimal-agent:minimal-agent",
    serviceName: "minimal-agent",
    displayName: "minimal-agent",
    root: "examples\\python\\minimal-agent",
    rootLabel: "examples\\python\\minimal-agent",
    envPrefix: "AGENT_MINIMAL_AGENT",
};

const policyAgent = {
    id: "modules\\agents\\contract-policy-expert:contract-policy-expert",
    serviceName: "contract-policy-expert",
    displayName: "contract-policy-expert",
    root: "modules\\agents\\contract-policy-expert",
    rootLabel: "modules\\agents\\contract-policy-expert",
    envPrefix: "AGENT_CONTRACT_POLICY_EXPERT",
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
        activity: [{ id: "act-1", actor: "Copilot", kind: "health_check", status: "completed", summary: "Readiness OK.", at: "2026-09-21T00:00:00Z" }],
    };

    const snapshot = stateSnapshot(state);

    assert.equal(snapshot.endpoint, "http://127.0.0.1:8100");
    assert.deepEqual(snapshot.visibleMessages.map((message) => message.id), ["local-1"]);
    assert.equal(snapshot.localRun.process, undefined);
    assert.equal(snapshot.activity.length, 1);
    assert.equal(snapshot.transcriptState.copyButton.enabled, false);
    assert.deepEqual(snapshot.stats, {
        total: 1,
        completed: 1,
        failed: 0,
        averageMs: 10,
        lastStatus: 200,
    });
});

test("activity entries are append-only and patchable", () => {
    const state = {};
    const entry = addActivity(state, {
        actor: "Copilot",
        kind: "send_response",
        status: "running",
        summary: "Prompt sent.",
    });

    updateActivity(state, entry.id, { status: "completed", summary: "Response completed." });

    assert.equal(state.activity.length, 1);
    assert.equal(state.activity[0].actor, "Copilot");
    assert.equal(state.activity[0].status, "completed");
    assert.equal(state.activity[0].summary, "Response completed.");
    assert.ok(state.activity[0].updatedAt);
});

test("transcriptState exposes visible prompts, pending label, latest copy target, and health banner", () => {
    const state = {
        target: "local",
        agents: [agent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8100" },
        localRun: emptyLocalRun(),
        lastHealth: { ok: true, status: 200, body: "agent=minimal-agent" },
        messages: [
            { input: "first", target: "local", createdAt: "2026-09-21T00:00:00Z", response: { ok: true, status: 200, body: { output_text: "first answer" } } },
            { input: "second", target: "local", response: { ok: false, status: "waiting", streaming: true, body: { output_text: "" }, delivery: { active: true } } },
        ],
    };

    const transcript = transcriptState(state);

    assert.deepEqual(transcript.prompts, ["first", "second"]);
    assert.deepEqual(transcript.answers, ["first answer", ""]);
    assert.equal(transcript.pendingLabel, "Thinking...");
    assert.equal(transcript.latestCopyTarget.text, "first answer");
    assert.equal(transcript.copyButton.enabled, true);
    assert.deepEqual(transcript.healthBanner, {
        ok: true,
        status: 200,
        body: "agent=minimal-agent",
        readinessStatus: null,
        identity: null,
        protocolSupport: null,
        configurationStatus: null,
    });
});

test("copy helpers skip pending, failed, and diagnostic-only responses", () => {
    assert.equal(responseDisplayText({ body: { output_text: "answer" } }), "answer");
    assert.equal(responsePendingLabel({ status: "waiting", streaming: true, body: { output_text: "" } }), "Thinking...");
    assert.equal(copyableAnswerText({ ok: false, status: 500, body: { error: "failed" } }), "");
    assert.equal(copyableAnswerText({ ok: true, status: 200, body: '{ "trace": "abc123" }' }), "");
    assert.equal(copyableAnswerText({ ok: true, status: 200, body: { output_text: "copy me" } }), "copy me");
    assert.equal(latestCopyTarget([
        { response: { ok: true, status: 200, body: { output_text: "first" } } },
        { response: { ok: false, status: "waiting", streaming: true, body: { output_text: "" } } },
    ]).text, "first");
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

    clearMessagesForTarget(state, "local");

    assert.deepEqual(state.messages.map((message) => message.id), []);
});

test("selectedAgent falls back to the default minimal agent", () => {
    assert.equal(selectedAgent({ agents: [], selectedAgentId: null }).serviceName, "minimal-agent");
});

test("setSelectedAgent keeps local endpoints keyed by agent id", () => {
    const state = {
        agents: [agent, policyAgent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8095" },
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
    };

    setSelectedAgent(state, policyAgent.id);

    assert.equal(selectedAgent(state).serviceName, "contract-policy-expert");
    assert.equal(selectedLocalEndpoint(state), "http://127.0.0.1:8088");
    assert.equal(state.localEndpoints[agent.id], "http://127.0.0.1:8095");
});

test("selectedLocalEndpoint prefers the selected agent running local run endpoint", () => {
    const state = {
        agents: [agent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8095" },
        localRun: {
            ...emptyLocalRun(),
            running: true,
            agentId: agent.id,
            endpoint: "http://127.0.0.1:8096",
        },
    };

    assert.equal(selectedLocalEndpoint(state), "http://127.0.0.1:8096");
});

test("emptyLocalRun has no agent ownership", () => {
    assert.deepEqual(emptyLocalRun(), {
        running: false,
        command: null,
        startedAt: null,
        completedAt: null,
        exitCode: null,
        log: [],
        events: [],
        process: null,
        agentId: null,
        agentName: null,
        endpoint: null,
        runId: null,
        readiness: null,
    });
});

test("switchSelectedAgent clears local transcript, health, and canvas-owned local run", () => {
    let stopped = 0;
    const state = {
        agents: [agent, policyAgent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8095" },
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
        localRun: {
            ...emptyLocalRun(),
            running: true,
            agentId: agent.id,
            agentName: agent.serviceName,
            endpoint: "http://127.0.0.1:8095",
            runId: "run-1",
        },
        lastHealth: { ok: true, body: "agent=minimal-agent" },
        messages: [
            { id: "local-1", target: "local" },
            { id: "hosted-1", target: "hosted" },
        ],
    };

    switchSelectedAgent(state, policyAgent.id, {
        stopLocalRun: () => {
            stopped += 1;
        },
    });

    assert.equal(stopped, 1);
    assert.equal(state.selectedAgentId, policyAgent.id);
    assert.deepEqual(state.localRun, emptyLocalRun());
    assert.equal(state.lastHealth, null);
    assert.deepEqual(state.messages, []);
});

test("switchSelectedAgent stops any running local run before clearing ownership", () => {
    let stopped = 0;
    const state = {
        agents: [agent, policyAgent],
        selectedAgentId: agent.id,
        localEndpoints: {},
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
        localRun: {
            ...emptyLocalRun(),
            running: true,
            agentId: policyAgent.id,
            agentName: policyAgent.serviceName,
            endpoint: "http://127.0.0.1:8096",
            runId: "run-2",
        },
        lastHealth: { ok: true },
        messages: [{ id: "local-1", target: "local" }],
    };

    switchSelectedAgent(state, policyAgent.id, {
        stopLocalRun: () => {
            stopped += 1;
        },
    });

    assert.equal(stopped, 1);
    assert.equal(state.selectedAgentId, policyAgent.id);
    assert.deepEqual(state.localRun, emptyLocalRun());
    assert.equal(state.lastHealth, null);
    assert.deepEqual(state.messages, []);
});

test("switchSelectedAgent clears all stale readiness, prompt, and target transcript state", () => {
    const state = {
        agents: [agent, policyAgent],
        selectedAgentId: agent.id,
        localEndpoints: { [agent.id]: "http://127.0.0.1:8095" },
        hostedByAgent: { [agent.id]: emptyHostedContext(agent) },
        hosted: emptyHostedContext(agent),
        localRun: emptyLocalRun(),
        lastHealth: {
            ok: false,
            readinessStatus: { status: "ok", reachable: true, ready: false },
            identity: { expected: "minimal-agent", actual: "contract-policy-expert", status: "mismatch" },
        },
        projectEndpointPrompt: { open: true },
        messages: [
            { id: "local-1", target: "local" },
            { id: "hosted-1", target: "hosted" },
        ],
    };

    switchSelectedAgent(state, policyAgent.id);

    assert.equal(state.selectedAgentId, policyAgent.id);
    assert.equal(state.lastHealth, null);
    assert.equal(state.projectEndpointPrompt, null);
    assert.deepEqual(state.messages, []);
});

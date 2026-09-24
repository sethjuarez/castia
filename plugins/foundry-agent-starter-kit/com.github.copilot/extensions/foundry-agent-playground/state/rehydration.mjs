import {
    addActivity,
    emptyFoundryConnection,
    emptyHostedContext,
    emptyLocalRun,
    normalizeEndpoint,
    selectedAgent,
    setSelectedAgent,
    setTarget,
} from "./snapshot.mjs";
import { emptyOperationState } from "../domain/operations.mjs";

const RESTORABLE_PROTOCOLS = new Set(["responses", "activity", "invocations"]);
const RESTORABLE_TARGETS = new Set(["local", "hosted"]);
const FOUNDRY_CONNECTION_FIELDS = [
    "projectEndpoint",
    "modelDeployment",
    "subscriptionId",
    "location",
    "projectId",
    "accountName",
    "projectName",
    "connectedAt",
    "lastConnectExitCode",
    "lastDiscoveryMessage",
];
const HOSTED_CONTEXT_FIELDS = [
    "agentName",
    "agentId",
    "version",
    "responsesEndpoint",
    "activityEndpoint",
    "invocationsEndpoint",
    "projectEndpoint",
    "modelDeployment",
    "status",
    "deployedAt",
    "lastRefresh",
    "lastRefreshExitCode",
    "lastRefreshSource",
    "lastRemoteDiscoveryStatus",
    "lastRemoteDiscoveryMessage",
];
const ENV_BOOTSTRAP_FIELDS = ["previewedAt", "dryRun", "overwrite", "targets", "written", "skipped"];
const MAX_RESTORED_ACTIVITY = 80;
const MAX_RESTORED_OPERATION_HISTORY = 50;

function isObject(value) {
    return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function cloneJson(value, fallback = null) {
    if (value === undefined) return fallback;
    try {
        return JSON.parse(JSON.stringify(value));
    } catch {
        return fallback;
    }
}

function pickFields(source, fields) {
    const result = {};
    if (!isObject(source)) return result;
    for (const field of fields) {
        if (source[field] !== undefined) result[field] = cloneJson(source[field]);
    }
    return result;
}

function sanitizeFoundryConnection(snapshot) {
    return {
        ...emptyFoundryConnection(),
        ...pickFields(snapshot?.foundryConnection, FOUNDRY_CONNECTION_FIELDS),
    };
}

function sanitizeHostedContext(agent, snapshot) {
    return {
        ...emptyHostedContext(agent),
        ...pickFields(snapshot, HOSTED_CONTEXT_FIELDS),
    };
}

function sanitizeEnvBootstrap(snapshot) {
    const envBootstrap = pickFields(snapshot?.envBootstrap, ENV_BOOTSTRAP_FIELDS);
    return Object.keys(envBootstrap).length ? envBootstrap : null;
}

function sanitizeLocalEndpoints(snapshot, agentIds) {
    const restored = {};
    if (!isObject(snapshot?.localEndpoints)) return restored;
    for (const [agentId, endpoint] of Object.entries(snapshot.localEndpoints)) {
        if (!agentIds.has(agentId)) continue;
        try {
            restored[agentId] = normalizeEndpoint(endpoint);
        } catch {
            // Ignore invalid persisted endpoints; the default endpoint remains available.
        }
    }
    return restored;
}

function restoreHostedByAgent(state, snapshot) {
    const restored = {};
    const savedHostedByAgent = isObject(snapshot?.hostedByAgent) ? snapshot.hostedByAgent : {};
    for (const agent of state.agents || []) {
        restored[agent.id] = sanitizeHostedContext(agent, savedHostedByAgent[agent.id]);
    }
    if (isObject(snapshot?.hosted) && state.selectedAgentId) {
        restored[state.selectedAgentId] = {
            ...restored[state.selectedAgentId],
            ...sanitizeHostedContext(selectedAgent(state), snapshot.hosted),
        };
    }
    state.hostedByAgent = restored;
    state.hosted = state.hostedByAgent[state.selectedAgentId] || emptyHostedContext(selectedAgent(state));
}

function sanitizeMessages(snapshot) {
    return Array.isArray(snapshot?.messages)
        ? cloneJson(snapshot.messages, []).filter((message) => isObject(message))
        : [];
}

function sanitizeActivity(snapshot) {
    return Array.isArray(snapshot?.activity)
        ? cloneJson(snapshot.activity, [])
            .filter((entry) => isObject(entry) && entry.status !== "running")
            .slice(-MAX_RESTORED_ACTIVITY)
        : [];
}

function sanitizeOperations(snapshot) {
    const operations = emptyOperationState();
    if (Array.isArray(snapshot?.operations?.history)) {
        operations.history = cloneJson(snapshot.operations.history, [])
            .filter((entry) => isObject(entry) && entry.status !== "running" && entry.status !== "cancel_requested")
            .slice(-MAX_RESTORED_OPERATION_HISTORY);
    }
    if (isObject(snapshot?.operations?.active)) {
        operations.history.push({
            ...cloneJson(snapshot.operations.active, {}),
            status: "interrupted",
            cancellable: false,
            completedAt: new Date().toISOString(),
            summary: "Interrupted by Copilot app or extension restart.",
            cancellation: null,
        });
    }
    return operations;
}

function interruptedSummary(snapshot) {
    if (snapshot?.localRun?.running) return "Local agent was running before restart; start local again to create a new process.";
    if (snapshot?.operations?.active) return "A Playground operation was active before restart; run the command again if needed.";
    return "Restored Playground state from the previous app session.";
}

export function rehydratePlaygroundState(state, snapshot, { input = {} } = {}) {
    if (!isObject(snapshot)) return { restored: false };

    const agentIds = new Set((state.agents || []).map((agent) => agent.id));
    const inputAgentId = input?.agentId && agentIds.has(input.agentId) ? input.agentId : null;
    const savedAgentId = snapshot.selectedAgentId && agentIds.has(snapshot.selectedAgentId)
        ? snapshot.selectedAgentId
        : null;
    setSelectedAgent(state, inputAgentId || savedAgentId || state.selectedAgentId);

    state.localEndpoints = {
        ...state.localEndpoints,
        ...sanitizeLocalEndpoints(snapshot, agentIds),
    };
    if (input?.endpoint) {
        state.localEndpoints[state.selectedAgentId] = normalizeEndpoint(input.endpoint);
    }

    if (RESTORABLE_TARGETS.has(snapshot.target)) {
        setTarget(state, snapshot.target);
    }
    if (RESTORABLE_PROTOCOLS.has(snapshot.activeProtocol)) {
        state.activeProtocol = snapshot.activeProtocol;
    }

    state.foundryConnection = sanitizeFoundryConnection(snapshot);
    restoreHostedByAgent(state, snapshot);
    state.envBootstrap = sanitizeEnvBootstrap(snapshot);
    state.teams = isObject(snapshot.teams) ? cloneJson(snapshot.teams, {}) : {};
    state.messages = sanitizeMessages(snapshot);
    state.activity = sanitizeActivity(snapshot);
    state.operations = sanitizeOperations(snapshot);

    state.localRun = {
        ...emptyLocalRun(),
        events: snapshot?.localRun?.running
            ? [{ kind: "fail", text: "Local agent was interrupted by app restart.", at: new Date().toISOString() }]
            : [],
    };
    state.deployment = {
        running: false,
        exitCode: null,
        startedAt: null,
        completedAt: null,
        command: null,
        needsProvision: false,
        log: [],
    };
    state.lastHealth = null;
    state.lastHealthByTarget = {};
    state.projectEndpointPrompt = null;

    addActivity(state, {
        actor: "Canvas",
        kind: "restore_state",
        status: "warn",
        summary: interruptedSummary(snapshot),
        details: {
            restoredFrom: state.runtimeStore?.statePath || null,
            previousLocalRunRunning: Boolean(snapshot?.localRun?.running),
            previousActiveOperation: snapshot?.operations?.active?.id || null,
        },
    });

    return { restored: true };
}

import { serviceEnvPrefix } from "./agent-discovery.mjs";
import { DEFAULT_AGENT_ROOT, DEFAULT_ENDPOINT, DEFAULT_SERVICE_NAME } from "./constants.mjs";

function codedError(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
}

export function normalizeEndpoint(value) {
    const endpoint = String(value || DEFAULT_ENDPOINT).trim().replace(/\/+$/, "");
    if (!/^https?:\/\/[^/\s]+/i.test(endpoint)) {
        throw codedError("invalid_endpoint", "Endpoint must be an http(s) URL.");
    }
    return endpoint;
}

export function activeEndpoint(state) {
    return state.target === "hosted" ? state.hosted.responsesEndpoint || "" : selectedLocalEndpoint(state);
}

export function selectedAgent(state) {
    return (
        state.agents.find((agent) => agent.id === state.selectedAgentId) ||
        state.agents[0] || {
            id: "minimal-agent",
            serviceName: DEFAULT_SERVICE_NAME,
            displayName: DEFAULT_SERVICE_NAME,
            root: DEFAULT_AGENT_ROOT,
            rootLabel: "examples\\python\\minimal-agent",
            envPrefix: serviceEnvPrefix(DEFAULT_SERVICE_NAME),
        }
    );
}

export function selectedLocalEndpoint(state) {
    const agent = selectedAgent(state);
    if (state.localRun?.running && state.localRun.agentId === agent.id && state.localRun.endpoint) {
        return state.localRun.endpoint;
    }
    return state.localEndpoints[agent.id] || DEFAULT_ENDPOINT;
}

export function endpointPort(endpoint) {
    try {
        const parsed = new URL(endpoint);
        return Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
    } catch {
        return null;
    }
}

export function endpointWithPort(endpoint, port) {
    const parsed = new URL(endpoint);
    parsed.hostname = "127.0.0.1";
    parsed.port = String(port);
    return parsed.toString().replace(/\/+$/, "");
}

export function addLocalEvent(state, kind, text) {
    state.localRun ||= {};
    state.localRun.events = [
        ...(state.localRun.events || []),
        { kind, text, at: new Date().toISOString() },
    ].slice(-5);
}

export function hasFoundryProjectValues(state) {
    return Boolean(state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment);
}

export function emptyHostedContext(agent) {
    return {
        agentName: agent.displayName || agent.serviceName,
        agentId: null,
        version: null,
        responsesEndpoint: null,
        activityEndpoint: null,
        invocationsEndpoint: null,
        projectEndpoint: null,
        modelDeployment: null,
        status: "not_deployed",
        deployedAt: null,
        lastRefresh: null,
        lastRefreshExitCode: null,
        lastRefreshSource: null,
        lastRemoteDiscoveryStatus: null,
        lastRemoteDiscoveryMessage: null,
    };
}

export function emptyFoundryConnection() {
    return {
        projectEndpoint: null,
        modelDeployment: null,
        subscriptionId: null,
        location: null,
        projectId: null,
        accountName: null,
        projectName: null,
        connectedAt: null,
        lastConnectExitCode: null,
        lastDiscoveryMessage: null,
    };
}

export function emptyLocalRun() {
    return {
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
    };
}

export function setSelectedAgent(state, agentId) {
    const agent = state.agents.find((candidate) => candidate.id === agentId) || state.agents[0];
    state.selectedAgentId = agent.id;
    state.hostedByAgent[agent.id] ||= emptyHostedContext(agent);
    state.localEndpoints[agent.id] ||= DEFAULT_ENDPOINT;
    state.hosted = state.hostedByAgent[agent.id];
    return agent;
}

export function switchSelectedAgent(state, agentId, { stopLocalRun } = {}) {
    const previousAgent = selectedAgent(state);
    const agent = setSelectedAgent(state, agentId);
    if (agent.id !== previousAgent.id) {
        if (state.localRun?.running || state.localRun?.process) {
            stopLocalRun?.(state);
        }
        state.localRun = emptyLocalRun();
        state.lastHealth = null;
        clearMessagesForTarget(state, "local");
    }
    return agent;
}

export function transcriptStats(messages) {
    const completed = messages.filter((message) => message.response?.ok);
    const failed = messages.filter(
        (message) => message.response && !message.response.streaming && !message.response.ok,
    );
    const latencies = messages
        .map((message) => message.response?.durationMs)
        .filter((value) => Number.isFinite(value));
    const averageMs = latencies.length
        ? Math.round(latencies.reduce((sum, value) => sum + value, 0) / latencies.length)
        : 0;
    return {
        total: messages.length,
        completed: completed.length,
        failed: failed.length,
        averageMs,
        lastStatus: messages.at(-1)?.response?.status ?? null,
    };
}

export function messagesForTarget(state) {
    return state.messages.filter((message) =>
        state.target === "hosted" ? message.target === "hosted" : message.target !== "hosted",
    );
}

export function clearMessagesForTarget(state, target) {
    const keep = state.messages.filter((message) =>
        target === "hosted" ? message.target !== "hosted" : message.target === "hosted",
    );
    state.messages.length = 0;
    state.messages.push(...keep);
}

export function stateSnapshot(state) {
    const agent = selectedAgent(state);
    const visibleMessages = messagesForTarget(state);
    return {
        endpoint: activeEndpoint(state),
        localEndpoint: selectedLocalEndpoint(state),
        target: state.target,
        agents: state.agents,
        selectedAgentId: state.selectedAgentId,
        selectedAgent: agent,
        foundryConnection: state.foundryConnection,
        envBootstrap: state.envBootstrap,
        hosted: state.hosted,
        deployment: state.deployment,
        localEnv: state.localEnv,
        localRun: {
            ...state.localRun,
            process: undefined,
        },
        activeEndpoint: activeEndpoint(state),
        activePort: endpointPort(activeEndpoint(state)),
        teams: state.teams,
        messages: state.messages,
        visibleMessages,
        lastHealth: state.lastHealth,
        stats: transcriptStats(visibleMessages),
    };
}

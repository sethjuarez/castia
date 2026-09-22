import { serviceEnvPrefix } from "../client/agent-discovery.mjs";
import { responseText } from "../client/agent-client.mjs";
import { DEFAULT_AGENT_ROOT, DEFAULT_ENDPOINT, DEFAULT_SERVICE_NAME } from "../domain/constants.mjs";

const MAX_ACTIVITY_ENTRIES = 80;

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

export function addActivity(state, { actor = "Canvas", kind, status = "info", summary, details = null }) {
    state.activity ||= [];
    const entry = {
        id: `act-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        at: new Date().toISOString(),
        actor,
        kind,
        status,
        summary,
        ...(details ? { details } : {}),
    };
    state.activity.push(entry);
    if (state.activity.length > MAX_ACTIVITY_ENTRIES) {
        state.activity.splice(0, state.activity.length - MAX_ACTIVITY_ENTRIES);
    }
    return entry;
}

export function updateActivity(state, id, patch) {
    const entry = (state.activity || []).find((candidate) => candidate.id === id);
    if (!entry) return null;
    Object.assign(entry, patch, { updatedAt: new Date().toISOString() });
    return entry;
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
        state.messages.length = 0;
        state.projectEndpointPrompt = null;
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

function parseResponseEnvelopeString(value) {
    const text = String(value || "").trim();
    if (!text || !/^[{[]/.test(text)) return null;
    try {
        const parsed = JSON.parse(text);
        return isResponseEnvelope(parsed) ? parsed : null;
    } catch {
        return null;
    }
}

function isResponseEnvelope(body, seen = new Set()) {
    if (!body || typeof body !== "object" || seen.has(body)) return false;
    seen.add(body);
    if (Object.prototype.hasOwnProperty.call(body, "output_text")) return true;
    if (Array.isArray(body.output)) return true;
    return isResponseEnvelope(body.response, seen) || isResponseEnvelope(body.body, seen);
}

export function responseDisplayText(response) {
    return String(responseText(response?.body) || "").trim();
}

export function responsePendingLabel(response) {
    const answer = responseDisplayText(response);
    const status = String(response?.status || "").toLowerCase();
    const pending = !answer && Boolean(
        response?.streaming ||
        response?.delivery?.active ||
        status === "waiting" ||
        status === "streaming"
    );
    return pending ? "Thinking..." : null;
}

export function copyableAnswerText(response) {
    if (!response?.ok || response?.streaming || responsePendingLabel(response)) return "";
    const body = response.body;
    if (typeof body === "string") {
        const text = body.trim();
        if (/^[{[]/.test(text) && !parseResponseEnvelopeString(text)) return "";
    }
    return responseDisplayText(response);
}

export function latestCopyTarget(messages = []) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const text = copyableAnswerText(messages[index]?.response);
        if (text) {
            return {
                messageIndex: index,
                createdAt: messages[index]?.createdAt || null,
                text,
            };
        }
    }
    return null;
}

export function transcriptState(state) {
    const visibleMessages = messagesForTarget(state);
    const latestCopy = latestCopyTarget(visibleMessages);
    const turns = visibleMessages.map((message) => ({
        prompt: message.input || "",
        answer: responseDisplayText(message.response),
        ok: message.response?.ok ?? null,
        status: message.response?.status ?? null,
        pendingLabel: responsePendingLabel(message.response),
        copyableText: copyableAnswerText(message.response),
    }));
    const latestPending = [...turns].reverse().find((turn) => turn.pendingLabel);
    return {
        target: state.target,
        selectedAgentId: state.selectedAgentId,
        endpoint: activeEndpoint(state),
        turns,
        prompts: turns.map((turn) => turn.prompt),
        answers: turns.map((turn) => turn.answer),
        pendingLabel: latestPending?.pendingLabel || null,
        latestCopyTarget: latestCopy,
        copyButton: {
            enabled: Boolean(latestCopy),
            text: latestCopy?.text || "",
        },
        healthBanner: {
            ok: state.lastHealth?.ok ?? null,
            status: state.lastHealth?.status ?? null,
            body: state.lastHealth?.body ?? null,
            readinessStatus: state.lastHealth?.readinessStatus ?? null,
            identity: state.lastHealth?.identity ?? null,
            protocolSupport: state.lastHealth?.protocolSupport ?? null,
            configurationStatus: state.lastHealth?.configurationStatus ?? null,
        },
    };
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
        deployment: state.deployment
            ? { ...state.deployment, process: undefined, abortController: undefined }
            : state.deployment,
        localEnv: state.localEnv,
        localRun: {
            ...state.localRun,
            process: undefined,
        },
        activeEndpoint: activeEndpoint(state),
        activePort: endpointPort(activeEndpoint(state)),
        teams: state.teams,
        activity: state.activity || [],
        messages: state.messages,
        visibleMessages,
        transcriptState: transcriptState(state),
        lastHealth: state.lastHealth,
        readinessStatus: state.lastHealth?.readinessStatus ?? null,
        identity: state.lastHealth?.identity ?? null,
        protocolSupport: state.lastHealth?.protocolSupport ?? null,
        configurationStatus: state.lastHealth?.configurationStatus ?? null,
        projectEndpointPrompt: state.projectEndpointPrompt || null,
        operations: state.operations || { active: null, history: [] },
        runtimeStore: state.runtimeStore
            ? {
                root: state.runtimeStore.root,
                statePath: state.runtimeStore.statePath,
                operationsPath: state.runtimeStore.operationsPath,
            }
            : null,
        stats: transcriptStats(visibleMessages),
    };
}

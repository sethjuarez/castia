export const FOUNDRY_API_VERSION = "v1";

function compact(values) {
    return values.filter((value) => value !== null && value !== undefined && String(value).trim());
}

function unique(values) {
    const seen = new Set();
    return values.filter((value) => {
        const key = String(value).toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function normalizeName(value) {
    return String(value || "").replace(/[^A-Za-z0-9]+/g, "").toLowerCase();
}

function normalizeEndpoint(value) {
    if (!value) return "";
    try {
        const url = new URL(String(value));
        return `${url.origin}${url.pathname.replace(/\/+$/, "")}`.toLowerCase();
    } catch {
        return String(value).replace(/\/+$/, "").toLowerCase();
    }
}

function sameEndpoint(left, right) {
    return Boolean(left && right && normalizeEndpoint(left) === normalizeEndpoint(right));
}

function foundryUrl(projectEndpoint, path, params = {}) {
    const url = new URL(`${String(projectEndpoint || "").replace(/\/+$/, "")}${path}`);
    url.searchParams.set("api-version", FOUNDRY_API_VERSION);
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    return url.toString();
}

async function foundryHeaders(accessTokenProvider) {
    const token = await accessTokenProvider("https://ai.azure.com");
    return {
        Accept: "application/json",
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
    };
}

async function fetchJson(url, headers, fetchImpl) {
    const response = await fetchImpl(url, { headers, signal: AbortSignal.timeout(8000) });
    const text = await response.text();
    let body = null;
    try {
        body = text ? JSON.parse(text) : null;
    } catch {
        body = text;
    }
    return { ok: response.ok, status: response.status, body };
}

function dataArray(body) {
    if (Array.isArray(body)) return body;
    if (Array.isArray(body?.data)) return body.data;
    if (Array.isArray(body?.value)) return body.value;
    if (Array.isArray(body?.agents)) return body.agents;
    if (Array.isArray(body?.versions)) return body.versions;
    return [];
}

function field(record, names) {
    for (const name of names) {
        const value = record?.[name];
        if (value !== undefined && value !== null && String(value).trim()) return value;
    }
    return null;
}

function extractVersion(value) {
    if (!value) return null;
    if (typeof value === "string" || typeof value === "number") return String(value);
    return field(value, ["version", "name", "id", "activeVersion", "active_version"]);
}

function extractAgentName(record) {
    return field(record, ["name", "agent_name", "agentName", "id"]);
}

function extractAgentId(record) {
    return field(record, ["id", "agent_id", "agentId"]);
}

function hasHostedDeployment(hosted) {
    return Boolean(hosted?.version || hosted?.responsesEndpoint || hosted?.activityEndpoint || hosted?.invocationsEndpoint);
}

function clearDeploymentFields(hosted, agent) {
    return {
        ...hosted,
        agentName: hosted?.agentName || agent?.displayName || agent?.serviceName || null,
        agentId: null,
        version: null,
        responsesEndpoint: null,
        activityEndpoint: null,
        invocationsEndpoint: null,
        status: "not_deployed",
        deployedAt: null,
        lastRefreshSource: null,
    };
}

function deployedStatus(record) {
    const status = extractStatus(record);
    if (!status) return true;
    return /^(active|deployed|succeeded|success|ready)$/i.test(status);
}

function extractStatus(...records) {
    for (const record of records) {
        const status = field(record, ["status", "state", "provisioningState", "provisioning_state"]);
        if (status) return String(status);
    }
    return null;
}

function timestampFromRecord(...records) {
    for (const record of records) {
        const value = field(record, ["updated_at", "updatedAt", "created_at", "createdAt", "lastModifiedAt"]);
        if (!value) continue;
        if (Number.isFinite(Number(value))) return new Date(Number(value) * 1000).toISOString();
        const date = new Date(value);
        if (!Number.isNaN(date.getTime())) return date.toISOString();
    }
    return null;
}

function protocolFromKey(key) {
    const normalized = String(key || "").toLowerCase();
    if (normalized.includes("responses")) return "responsesEndpoint";
    if (normalized.includes("activity")) return "activityEndpoint";
    if (normalized.includes("invocation")) return "invocationsEndpoint";
    return null;
}

function protocolFromUrl(value) {
    const text = String(value || "");
    if (!/^https?:\/\//i.test(text)) return null;
    const lower = text.toLowerCase();
    if (lower.includes("/protocols/openai/responses") || lower.endsWith("/responses") || lower.includes("/openai/v1/responses")) {
        return "responsesEndpoint";
    }
    if (lower.includes("activity")) return "activityEndpoint";
    if (lower.includes("invocations")) return "invocationsEndpoint";
    return null;
}

export function normalizeHostedResponsesEndpoint(endpoint) {
    if (!endpoint) return null;
    try {
        const url = new URL(endpoint);
        if (!/\/endpoint\/protocols\/openai\/responses$/i.test(url.pathname)) {
            return String(endpoint).replace(/\/+$/, "");
        }
        url.pathname = url.pathname.replace(
            /(\/agents\/[^/]+)\/versions\/[^/]+\/endpoint\/protocols\/openai\/responses$/i,
            "$1/endpoint/protocols/openai/responses",
        );
        if (!url.searchParams.has("api-version")) {
            url.searchParams.set("api-version", FOUNDRY_API_VERSION);
        }
        return url.toString();
    } catch {
        return String(endpoint).replace(/\/+$/, "");
    }
}

function endpointFieldForProtocol(protocol) {
    const normalized = String(protocol || "").toLowerCase();
    if (normalized === "responses" || normalized === "openai") return "responsesEndpoint";
    if (normalized === "activity") return "activityEndpoint";
    if (normalized === "invocations" || normalized === "invocation") return "invocationsEndpoint";
    return null;
}

function isFoundryProtocolEndpoint(value, { projectEndpoint, agentName, version }) {
    if (!projectEndpoint || !agentName || !version) return false;
    try {
        const endpoint = new URL(value);
        const project = new URL(projectEndpoint);
        const path = decodeURIComponent(endpoint.pathname).toLowerCase();
        const name = String(agentName).toLowerCase();
        const selectedVersion = String(version).toLowerCase();
        if (endpoint.origin.toLowerCase() !== project.origin.toLowerCase()) return false;
        return (
            path.includes(`/agents/${name}/versions/${selectedVersion}/endpoint/protocols/`) ||
            path.includes(`/agents/${name}/endpoint/protocols/`)
        );
    } catch {
        return false;
    }
}

function collectEndpointUrls(value, options = {}, parentKey = "", depth = 0, output = {}) {
    if (depth > 8 || value === null || value === undefined) return output;
    if (typeof value === "string") {
        const protocol = protocolFromKey(parentKey) || protocolFromUrl(value);
        if (protocol && !output[protocol] && /^https?:\/\//i.test(value) && isFoundryProtocolEndpoint(value, options)) {
            output[protocol] = protocol === "responsesEndpoint" ? normalizeHostedResponsesEndpoint(value) : value.replace(/\/+$/, "");
        }
        return output;
    }
    if (Array.isArray(value)) {
        for (const item of value) collectEndpointUrls(item, options, parentKey, depth + 1, output);
        return output;
    }
    if (typeof value === "object") {
        for (const [key, nested] of Object.entries(value)) {
            collectEndpointUrls(nested, options, key, depth + 1, output);
        }
    }
    return output;
}

function collectDeclaredProtocols(value, output = new Set(), depth = 0) {
    if (depth > 8 || value === null || value === undefined) return output;
    if (typeof value === "string") {
        if (endpointFieldForProtocol(value)) output.add(value.toLowerCase());
        return output;
    }
    if (Array.isArray(value)) {
        for (const item of value) collectDeclaredProtocols(item, output, depth + 1);
        return output;
    }
    if (typeof value === "object") {
        if (typeof value.protocol === "string" && endpointFieldForProtocol(value.protocol)) {
            output.add(value.protocol.toLowerCase());
        }
        for (const key of ["protocols", "protocol_versions", "container_protocol_versions"]) {
            collectDeclaredProtocols(value[key], output, depth + 1);
        }
        collectDeclaredProtocols(value.definition, output, depth + 1);
        collectDeclaredProtocols(value.versions?.latest, output, depth + 1);
    }
    return output;
}

function constructProtocolEndpoint(projectEndpoint, agentName, version, protocol) {
    if (!projectEndpoint || !agentName || !version) return null;
    const base = String(projectEndpoint).replace(/\/+$/, "");
    const encodedAgent = encodeURIComponent(agentName);
    const encodedVersion = encodeURIComponent(version);
    const normalized = String(protocol || "").toLowerCase();
    if (normalized === "responses" || normalized === "openai") {
        return normalizeHostedResponsesEndpoint(`${base}/agents/${encodedAgent}/endpoint/protocols/openai/responses`);
    }
    if (normalized === "activity") {
        return `${base}/agents/${encodedAgent}/versions/${encodedVersion}/endpoint/protocols/activity`;
    }
    if (normalized === "invocations" || normalized === "invocation") {
        return `${base}/agents/${encodedAgent}/versions/${encodedVersion}/endpoint/protocols/invocations`;
    }
    return null;
}

function compareVersions(left, right) {
    const leftVersion = Number(extractVersion(left));
    const rightVersion = Number(extractVersion(right));
    if (Number.isFinite(leftVersion) && Number.isFinite(rightVersion) && leftVersion !== rightVersion) {
        return rightVersion - leftVersion;
    }
    const leftCreated = Number(new Date(field(left, ["created_at", "createdAt"]) || 0));
    const rightCreated = Number(new Date(field(right, ["created_at", "createdAt"]) || 0));
    return rightCreated - leftCreated;
}

function chooseVersion(versions, agentRecord) {
    const active = extractVersion(field(agentRecord || {}, ["active_version", "activeVersion", "version"]));
    if (active) {
        const match = versions.find((version) => extractVersion(version) === active);
        if (match && deployedStatus(match)) return match;
    }
    return [...versions]
        .filter((version) => !version.draft && version.draft !== true && deployedStatus(version))
        .sort((left, right) => {
            const leftActive = /^(active|deployed|succeeded)$/i.test(String(extractStatus(left) || ""));
            const rightActive = /^(active|deployed|succeeded)$/i.test(String(extractStatus(right) || ""));
            if (leftActive !== rightActive) return rightActive ? 1 : -1;
            return compareVersions(left, right);
        })[0] || null;
}

function agentNameCandidates(agent, currentHosted) {
    return unique(compact([
        currentHosted?.agentName,
        agent?.displayName,
        agent?.serviceName,
    ]).map(String));
}

function preserveCurrentHosted(currentHosted, status, message, now, exitCode = 0) {
    return {
        ...currentHosted,
        lastRefresh: now.toISOString(),
        lastRefreshExitCode: exitCode,
        lastRemoteDiscoveryStatus: status,
        lastRemoteDiscoveryMessage: message,
    };
}

function remoteVersionIsOlder(currentHosted, remoteVersion) {
    const current = Number(currentHosted?.version);
    const remote = Number(remoteVersion);
    return Number.isFinite(current) && Number.isFinite(remote) && current > remote;
}

async function findAgentRecord(projectEndpoint, agent, currentHosted, headers, fetchImpl) {
    const candidates = agentNameCandidates(agent, currentHosted);
    let lastNotFound = null;
    for (const name of candidates) {
        const result = await fetchJson(foundryUrl(projectEndpoint, `/agents/${encodeURIComponent(name)}`), headers, fetchImpl);
        if (result.ok) return { record: result.body, name };
        if (result.status !== 404) return { error: result, name };
        lastNotFound = result;
    }
    const list = await fetchJson(foundryUrl(projectEndpoint, "/agents", { limit: 100 }), headers, fetchImpl);
    if (!list.ok) return { error: list, name: candidates[0] || agent?.serviceName || null };
    const wanted = new Set(candidates.map(normalizeName));
    const wantedId = currentHosted?.agentId ? String(currentHosted.agentId).toLowerCase() : null;
    const records = dataArray(list.body);
    const idMatch = wantedId && records.find((candidate) => {
        const candidateId = extractAgentId(candidate) ? String(extractAgentId(candidate)).toLowerCase() : null;
        return candidateId === wantedId;
    });
    if (idMatch) return { record: idMatch, name: extractAgentName(idMatch), lastNotFound, matchedBy: "agent_id" };

    const exactMatch = records.find((candidate) => wanted.has(normalizeName(extractAgentName(candidate))));
    if (exactMatch) return { record: exactMatch, name: extractAgentName(exactMatch), lastNotFound, matchedBy: "name" };

    const prefixMatches = records.filter((candidate) => {
        const candidateName = normalizeName(extractAgentName(candidate));
        return [...wanted].some((name) => candidateName.startsWith(name) || name.startsWith(candidateName));
    });
    const record = prefixMatches.length === 1 ? prefixMatches[0] : null;
    return {
        record: record || null,
        name: record ? extractAgentName(record) : candidates[0] || agent?.serviceName || null,
        lastNotFound,
        matchedBy: record ? "prefix" : null,
        ambiguousMessage:
            prefixMatches.length > 1
                ? `Multiple hosted agents matched ${candidates[0] || agent?.serviceName}; use the exact agent name or cached agent id.`
                : null,
    };
}

async function getAgentVersion(projectEndpoint, agentName, version, headers, fetchImpl) {
    if (!agentName || !version) return null;
    const result = await fetchJson(
        foundryUrl(projectEndpoint, `/agents/${encodeURIComponent(agentName)}/versions/${encodeURIComponent(version)}`),
        headers,
        fetchImpl,
    );
    return result.ok ? result.body : null;
}

export function hostedContextFromAzd({ agent, currentHosted, foundryConnection, values = {}, exitCode, now = new Date() }) {
    const prefix = agent.envPrefix;
    const projectEndpoint =
        values.AZURE_AI_PROJECT_ENDPOINT ||
        values.AZURE_AIPROJECT_ENDPOINT ||
        values.FOUNDRY_PROJECT_ENDPOINT ||
        foundryConnection.projectEndpoint ||
        currentHosted.projectEndpoint;
    const modelDeployment =
        values.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        values.AZURE_OPENAI_DEPLOYMENT_NAME ||
        foundryConnection.modelDeployment ||
        currentHosted.modelDeployment;
    const baseHosted =
        currentHosted.projectEndpoint && projectEndpoint && !sameEndpoint(currentHosted.projectEndpoint, projectEndpoint)
            ? clearDeploymentFields(currentHosted, agent)
            : currentHosted;
    return {
        ...baseHosted,
        agentName: values[`${prefix}_NAME`] || baseHosted.agentName,
        agentId: values[`${prefix}_ID`] || baseHosted.agentId,
        version: values[`${prefix}_VERSION`] || baseHosted.version,
        responsesEndpoint: normalizeHostedResponsesEndpoint(values[`${prefix}_RESPONSES_ENDPOINT`] || baseHosted.responsesEndpoint),
        activityEndpoint: values[`${prefix}_ACTIVITY_ENDPOINT`] || baseHosted.activityEndpoint,
        invocationsEndpoint: values[`${prefix}_INVOCATIONS_ENDPOINT`] || baseHosted.invocationsEndpoint,
        projectEndpoint,
        modelDeployment,
        lastRefresh: now.toISOString(),
        lastRefreshExitCode: exitCode,
        lastRefreshSource: values[`${prefix}_VERSION`] || values[`${prefix}_RESPONSES_ENDPOINT`] ? "azd" : baseHosted.lastRefreshSource || null,
    };
}

export async function discoverHostedContextFromFoundry({
    agent,
    currentHosted,
    foundryConnection,
    fetchImpl = fetch,
    accessTokenProvider,
    now = new Date(),
}) {
    const projectEndpoint = currentHosted.projectEndpoint || foundryConnection.projectEndpoint;
    if (!projectEndpoint || !accessTokenProvider) {
        return { attempted: false, hosted: currentHosted };
    }

    const headers = await foundryHeaders(accessTokenProvider);
    const foundAgent = await findAgentRecord(projectEndpoint, agent, currentHosted, headers, fetchImpl);
    if (foundAgent.error) {
        const message =
            typeof foundAgent.error.body === "string"
                ? foundAgent.error.body
                : foundAgent.error.body?.error?.message || foundAgent.error.body?.message || `HTTP ${foundAgent.error.status}`;
        return {
            attempted: true,
            ok: false,
            hosted: {
                ...currentHosted,
                lastRefresh: now.toISOString(),
                lastRefreshExitCode: foundAgent.error.status,
                lastRefreshSource: currentHosted.lastRefreshSource || "azd",
                lastRemoteDiscoveryStatus: "error",
                lastRemoteDiscoveryMessage: message,
            },
        };
    }

    const agentRecord = foundAgent.record;
    const agentName = extractAgentName(agentRecord) || foundAgent.name;
    if (!agentRecord || !agentName) {
        if (hasHostedDeployment(currentHosted)) {
            return {
                attempted: true,
                ok: true,
                hosted: preserveCurrentHosted(
                    currentHosted,
                    "not_found_using_cache",
                    `Foundry did not return ${foundAgent.name || agent.serviceName}; keeping cached deployment metadata.`,
                    now,
                ),
            };
        }
        return {
            attempted: true,
            ok: true,
            hosted: {
                ...currentHosted,
                agentName: currentHosted.agentName || agent.displayName || agent.serviceName,
                agentId: null,
                version: null,
                responsesEndpoint: null,
                activityEndpoint: null,
                invocationsEndpoint: null,
                projectEndpoint,
                modelDeployment: currentHosted.modelDeployment || foundryConnection.modelDeployment,
                status: "not_deployed",
                lastRefresh: now.toISOString(),
                lastRefreshExitCode: 0,
                lastRefreshSource: "foundry",
                lastRemoteDiscoveryStatus: "not_found",
                lastRemoteDiscoveryMessage:
                    foundAgent.ambiguousMessage ||
                    `No hosted agent named ${foundAgent.name || agent.serviceName} was found in the Foundry project.`,
            },
        };
    }

    const versionsResult = await fetchJson(
        foundryUrl(projectEndpoint, `/agents/${encodeURIComponent(agentName)}/versions`, { order: "desc", limit: 20 }),
        headers,
        fetchImpl,
    );
    if (!versionsResult.ok && versionsResult.status !== 404) {
        const message =
            typeof versionsResult.body === "string"
                ? versionsResult.body
                : versionsResult.body?.error?.message || versionsResult.body?.message || `HTTP ${versionsResult.status}`;
        return {
            attempted: true,
            ok: false,
            hosted: {
                ...currentHosted,
                lastRefresh: now.toISOString(),
                lastRefreshExitCode: versionsResult.status,
                lastRefreshSource: currentHosted.lastRefreshSource || "azd",
                lastRemoteDiscoveryStatus: "error",
                lastRemoteDiscoveryMessage: message,
            },
        };
    }

    const versions = versionsResult.ok ? dataArray(versionsResult.body) : [];
    const selectedVersion = chooseVersion(versions, agentRecord);
    const version =
        extractVersion(selectedVersion) ||
        (versions.length ? null : extractVersion(field(agentRecord, ["active_version", "activeVersion", "version"])));
    if (!version) {
        if (hasHostedDeployment(currentHosted)) {
            return {
                attempted: true,
                ok: true,
                hosted: preserveCurrentHosted(
                    currentHosted,
                    "not_deployed_using_cache",
                    `Foundry did not return a deployed version for ${agentName}; keeping cached deployment metadata.`,
                    now,
                ),
            };
        }
        return {
            attempted: true,
            ok: true,
            hosted: {
                ...currentHosted,
                agentName,
                agentId: extractAgentId(agentRecord) || currentHosted.agentId || null,
                version: null,
                responsesEndpoint: null,
                activityEndpoint: null,
                invocationsEndpoint: null,
                projectEndpoint,
                modelDeployment: currentHosted.modelDeployment || foundryConnection.modelDeployment,
                status: "not_deployed",
                lastRefresh: now.toISOString(),
                lastRefreshExitCode: 0,
                lastRefreshSource: "foundry",
                lastRemoteDiscoveryStatus: "not_deployed",
                lastRemoteDiscoveryMessage: `Found hosted agent ${agentName}, but no deployed versions were returned.`,
            },
        };
    }

    const versionDetails = await getAgentVersion(projectEndpoint, agentName, version, headers, fetchImpl);
    if (versionDetails && !deployedStatus(versionDetails)) {
        if (hasHostedDeployment(currentHosted)) {
            return {
                attempted: true,
                ok: true,
                hosted: preserveCurrentHosted(
                    currentHosted,
                    "not_deployed_using_cache",
                    `Foundry returned version ${version} with status ${extractStatus(versionDetails)}; keeping cached deployment metadata.`,
                    now,
                ),
            };
        }
        return {
            attempted: true,
            ok: true,
            hosted: {
                ...currentHosted,
                agentName,
                agentId: extractAgentId(agentRecord) || currentHosted.agentId || null,
                version: null,
                responsesEndpoint: null,
                activityEndpoint: null,
                invocationsEndpoint: null,
                projectEndpoint,
                modelDeployment: currentHosted.modelDeployment || foundryConnection.modelDeployment,
                status: extractStatus(versionDetails) || "not_deployed",
                lastRefresh: now.toISOString(),
                lastRefreshExitCode: 0,
                lastRefreshSource: "foundry",
                lastRemoteDiscoveryStatus: "not_deployed",
                lastRemoteDiscoveryMessage: `Found hosted agent ${agentName}, but version ${version} is not deployed.`,
            },
        };
    }
    if (remoteVersionIsOlder(currentHosted, version)) {
        return {
            attempted: true,
            ok: true,
            hosted: preserveCurrentHosted(
                currentHosted,
                "stale_remote_using_cache",
                `Foundry returned version ${version}, which is older than cached version ${currentHosted.version}; keeping cached deployment metadata.`,
                now,
            ),
        };
    }
    const endpointOptions = { projectEndpoint, agentName, version };
    const endpoints = {
        ...collectEndpointUrls(agentRecord, endpointOptions),
        ...collectEndpointUrls(selectedVersion, endpointOptions),
        ...collectEndpointUrls(versionDetails, endpointOptions),
    };
    const declaredProtocols = collectDeclaredProtocols(agentRecord);
    collectDeclaredProtocols(selectedVersion, declaredProtocols);
    collectDeclaredProtocols(versionDetails, declaredProtocols);
    for (const protocol of declaredProtocols) {
        const fieldName = endpointFieldForProtocol(protocol);
        if (fieldName) endpoints[fieldName] ||= constructProtocolEndpoint(projectEndpoint, agentName, version, protocol);
    }
    endpoints.responsesEndpoint ||= constructProtocolEndpoint(projectEndpoint, agentName, version, "responses");

    return {
        attempted: true,
        ok: true,
        hosted: {
            ...currentHosted,
            agentName,
            agentId: extractAgentId(agentRecord) || extractAgentId(selectedVersion) || currentHosted.agentId || null,
            version,
            responsesEndpoint: endpoints.responsesEndpoint || null,
            activityEndpoint: endpoints.activityEndpoint || null,
            invocationsEndpoint: endpoints.invocationsEndpoint || null,
            projectEndpoint,
            modelDeployment: currentHosted.modelDeployment || foundryConnection.modelDeployment,
            status: extractStatus(versionDetails, selectedVersion, agentRecord) || "deployed",
            deployedAt: timestampFromRecord(versionDetails, selectedVersion, agentRecord) || currentHosted.deployedAt || null,
            lastRefresh: now.toISOString(),
            lastRefreshExitCode: 0,
            lastRefreshSource: "foundry",
            lastRemoteDiscoveryStatus: "found",
            lastRemoteDiscoveryMessage:
                foundAgent.matchedBy === "prefix"
                    ? `Matched remote agent ${agentName} for ${agent.serviceName || agent.displayName}. Found version ${version} in the Foundry project.`
                    : `Found ${agentName} version ${version} in the Foundry project.`,
        },
    };
}

import { normalizeHostedResponsesEndpoint } from "./hosted-discovery.mjs";

const tokenCache = new Map();
let commandRunner;

export function configureAgentClient({ runCommand }) {
    commandRunner = runCommand;
}

export function responseText(body) {
    const extracted = textFromResponseBody(body);
    if (extracted) return extracted;
    if (body && typeof body === "object") {
        if (typeof body.output === "string") return body.output;
        if (typeof body.error === "string") return body.error;
        if (typeof body.error?.message === "string") return body.error.message;
        if (typeof body.error?.code === "string") return body.error.code;
        if (typeof body.detail === "string") return body.detail;
        if (typeof body.message === "string") return body.message;
        return "";
    }
    return body ?? "";
}

function textOrEmpty(value) {
    return typeof value === "string" && value.trim() ? value : "";
}

function textFromResponseBody(body, seen = new Set()) {
    if (!body || typeof body !== "object" || seen.has(body)) return "";
    seen.add(body);
    const direct = textOrEmpty(body.output_text);
    if (direct) return direct;
    const output = textFromResponsesOutput(body.output);
    if (output) return output;
    return textFromResponseBody(body.response, seen) || textFromResponseBody(body.body, seen);
}

function textFromResponsesOutput(output) {
    if (!Array.isArray(output)) return "";
    const parts = [];
    for (const item of output) {
        if (!item || typeof item !== "object") continue;
        if (typeof item.text === "string") {
            parts.push(item.text);
        }
        const content = item.content;
        if (!Array.isArray(content)) continue;
        for (const part of content) {
            if (part && typeof part === "object" && typeof part.text === "string") {
                parts.push(part.text);
            }
        }
    }
    return parts.join("").trim();
}

export function isLoopbackEndpoint(endpoint) {
    try {
        const { hostname } = new URL(endpoint);
        return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
    } catch {
        return false;
    }
}

export function isFoundryResponsesEndpoint(endpoint) {
    try {
        const { pathname } = new URL(endpoint);
        return /\/endpoint\/protocols\/openai\/responses$/i.test(pathname);
    } catch {
        return false;
    }
}

export function responsesUrl(endpoint) {
    if (isFoundryResponsesEndpoint(endpoint)) {
        return normalizeHostedResponsesEndpoint(endpoint);
    }
    return `${endpoint.replace(/\/+$/, "")}/responses`;
}

export function protocolUrl(endpoint, path) {
    const normalizedPath = String(path || "");
    const trimmed = String(endpoint || "").replace(/\/+$/, "");
    let endpointPath = "";
    try {
        endpointPath = new URL(trimmed).pathname;
    } catch {
        endpointPath = "";
    }
    if (normalizedPath === "/responses") {
        if (/\/responses$/i.test(endpointPath)) return trimmed;
        return responsesUrl(trimmed);
    }
    if (normalizedPath === "/activity/messages") {
        if (/\/activity\/messages$/i.test(trimmed) || /\/endpoint\/protocols\/activity$/i.test(endpointPath)) {
            return trimmed;
        }
    }
    if (normalizedPath === "/invocations") {
        if (/\/invocations$/i.test(trimmed) || /\/endpoint\/protocols\/invocations$/i.test(endpointPath)) {
            return trimmed;
        }
    }
    return `${trimmed}${normalizedPath}`;
}

export function readinessText(body) {
    if (!body || typeof body !== "object") return body ?? "";
    const parts = [];
    const agentName = body.agent && typeof body.agent === "object" ? body.agent.name : null;
    if (agentName) parts.push(`agent=${agentName}`);
    if (Array.isArray(body.protocols) && body.protocols.length) {
        parts.push(`protocols=${body.protocols.join(",")}`);
    }
    const missing = body.configuration && typeof body.configuration === "object"
        ? body.configuration.missing_required
        : null;
    if (Array.isArray(missing) && missing.length) {
        parts.push(`missing=${missing.join(",")}`);
    }
    return parts.length ? parts.join("; ") : JSON.stringify(body, null, 2);
}

export function readinessAgentName(readiness) {
    return readiness?.agent && typeof readiness.agent === "object" && typeof readiness.agent.name === "string"
        ? readiness.agent.name
        : null;
}

function normalizedAgentName(name) {
    return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function readinessIdentity(endpoint, expectedAgentNames, actualAgentName, requireKnownIdentity) {
    const expectedNames = (Array.isArray(expectedAgentNames) ? expectedAgentNames : [expectedAgentNames])
        .map((name) => String(name || "").trim())
        .filter(Boolean);
    if (!expectedNames.length) return null;
    const expected = expectedNames[0];
    const expectedNormalized = new Set(expectedNames.map(normalizedAgentName).filter(Boolean));
    if (!actualAgentName) {
        if (!requireKnownIdentity) return null;
        return {
            body: `Readiness identity is unknown for ${endpoint}; selected ${expected}. The agent returned legacy/non-JSON readiness, so this endpoint was not accepted.`,
            identity: { expected, actual: null, status: "unknown" },
        };
    }
    if (!expectedNormalized.has(normalizedAgentName(actualAgentName))) {
        return {
            body: `Endpoint belongs to ${actualAgentName}; selected ${expected}.`,
            identity: { expected, actual: actualAgentName, status: "mismatch" },
        };
    }
    return { identity: { expected, actual: actualAgentName, status: "match" } };
}

function readinessStatus(parsed) {
    const status = parsed.readiness && typeof parsed.readiness.status === "string"
        ? parsed.readiness.status
        : parsed.ok
        ? "ready"
        : "failed";
    return {
        status,
        reachable: true,
        ready: parsed.ok,
    };
}

function readinessProtocolSupport(readiness, requiredProtocols = []) {
    const required = (Array.isArray(requiredProtocols) ? requiredProtocols : [requiredProtocols])
        .map((protocol) => String(protocol || "").trim())
        .filter(Boolean);
    const protocols = Array.isArray(readiness?.protocols)
        ? readiness.protocols.map((protocol) => String(protocol || "").trim()).filter(Boolean)
        : null;
    if (!required.length) {
        return { status: "not_required", required, protocols: protocols || [], missing: [] };
    }
    if (!protocols) {
        return { status: "unknown", required, protocols: [], missing: [] };
    }
    const normalized = new Set(protocols.map((protocol) => protocol.toLowerCase()));
    const missing = required.filter((protocol) => !normalized.has(protocol.toLowerCase()));
    return {
        status: missing.length ? "missing" : "supported",
        required,
        protocols,
        missing,
    };
}

function readinessConfigurationStatus(readiness) {
    const missing = readiness?.configuration && typeof readiness.configuration === "object"
        ? readiness.configuration.missing_required
        : null;
    const missingRequired = Array.isArray(missing)
        ? missing.map((value) => String(value || "").trim()).filter(Boolean)
        : [];
    if (missingRequired.length) {
        return { status: "missing", missingRequired };
    }
    if (readiness?.configuration && typeof readiness.configuration === "object") {
        return { status: "configured", missingRequired: [] };
    }
    return { status: "unknown", missingRequired: [] };
}

export function parseReadinessResponse(responseOk, status, text) {
    let body = text;
    try {
        body = text ? JSON.parse(text) : null;
    } catch {
        return { ok: responseOk, body };
    }
    if (body && typeof body === "object" && typeof body.status === "string") {
        return {
            ok: responseOk && body.status === "ok",
            body: readinessText(body),
            readiness: body,
        };
    }
    return { ok: responseOk, body: readinessText(body) };
}

export async function azureAccessToken(resource, { forceRefresh = false } = {}) {
    const cached = tokenCache.get(resource);
    if (!forceRefresh && cached && cached.expiresAt > Date.now() + 60000) {
        return cached.token;
    }
    if (!commandRunner) {
        throw new Error("Agent client command runner is not configured.");
    }
    const result = await commandRunner("az", [
        "account",
        "get-access-token",
        "--resource",
        resource,
        "--query",
        "accessToken",
        "--output",
        "tsv",
    ]);
    if (result.code !== 0) {
        throw new Error(result.output || "Azure login is required to call the hosted agent.");
    }
    const token = result.output.trim();
    if (!token) {
        throw new Error("Azure CLI did not return an access token for the hosted agent.");
    }
    tokenCache.set(resource, { token, expiresAt: Date.now() + 50 * 60 * 1000 });
    return token;
}

export async function requestHeadersForEndpoint(endpoint, accept, { forceRefresh = false } = {}) {
    const headers = {
        "Content-Type": "application/json",
        ...(accept ? { Accept: accept } : {}),
    };
    if (/^https:\/\//i.test(endpoint) && !isLoopbackEndpoint(endpoint)) {
        headers.Authorization = `Bearer ${await azureAccessToken("https://ai.azure.com", { forceRefresh })}`;
    }
    return headers;
}

function shouldRetryWithFreshToken(endpoint, response) {
    return /^https:\/\//i.test(endpoint) && !isLoopbackEndpoint(endpoint) && (response.status === 401 || response.status === 403);
}

async function fetchAgent(endpoint, url, { accept, body, timeoutMs = 60000, forceRefresh = false } = {}) {
    return fetch(url, {
        method: "POST",
        headers: await requestHeadersForEndpoint(endpoint, accept, { forceRefresh }),
        body,
        signal: AbortSignal.timeout(timeoutMs),
    });
}

function readinessHeaders(endpoint, expectsIdentity) {
    if (expectsIdentity && isLoopbackEndpoint(endpoint)) {
        return { "X-Castia-Readiness": "diagnostics" };
    }
    return {};
}

export async function callAgentStream(endpoint, payload, onDelta) {
    const started = Date.now();
    try {
        const requestBody = JSON.stringify({ ...payload, stream: true });
        let response = await fetchAgent(endpoint, responsesUrl(endpoint), {
            accept: "text/event-stream",
            body: requestBody,
        });
        if (shouldRetryWithFreshToken(endpoint, response)) {
            response = await fetchAgent(endpoint, responsesUrl(endpoint), {
                accept: "text/event-stream",
                body: requestBody,
                forceRefresh: true,
            });
        }
        const contentType = response.headers.get("content-type") || "";
        if (!response.ok || !contentType.includes("text/event-stream") || !response.body) {
            const text = await response.text();
            let body = text;
            try {
                body = text ? JSON.parse(text) : null;
            } catch {
                // Keep non-JSON error bodies readable in the tester.
            }
            return {
                ok: response.ok,
                status: response.status,
                durationMs: Date.now() - started,
                body,
                delivery: {
                    mode: contentType.includes("text/event-stream") ? "upstream-stream" : "single-response",
                    upstreamStreaming: contentType.includes("text/event-stream"),
                    active: false,
                },
            };
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let outputText = "";
        let completedBody = null;

        const handlePart = (part) => {
            const lines = part.split("\n");
            const eventLine = lines.find((line) => line.startsWith("event: "));
            const eventName = eventLine ? eventLine.slice(7).trim() : "message";
            const data = lines
                .filter((line) => line.startsWith("data: "))
                .map((line) => line.slice(6))
                .join("\n");
            if (!data) return;
            if (data.trim() === "[DONE]") return;
            const payload = JSON.parse(data);
            if (eventName === "response.output_text.delta") {
                const delta = String(payload.delta || "");
                if (delta) {
                    outputText += delta;
                    onDelta(delta, outputText, Date.now() - started);
                }
            } else if (eventName === "response.completed") {
                completedBody = payload;
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split("\n\n");
            buffer = parts.pop() || "";
            for (const part of parts) {
                handlePart(part);
            }
        }
        buffer += decoder.decode();
        if (buffer.trim()) {
            handlePart(buffer);
        }

        const finalText = textOrEmpty(outputText) || responseText(completedBody);
        return {
            ok: true,
            status: response.status,
            durationMs: Date.now() - started,
            body: completedBody && typeof completedBody === "object"
                ? { ...completedBody, output_text: finalText }
                : { output_text: finalText },
            delivery: {
                mode: "upstream-stream",
                upstreamStreaming: true,
                active: false,
            },
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
            delivery: {
                mode: "error",
                upstreamStreaming: false,
                active: false,
            },
        };
    }
}

export async function callAgent(endpoint, path, payload) {
    const started = Date.now();
    try {
        const url = protocolUrl(endpoint, path);
        const requestBody = JSON.stringify(payload);
        let response = await fetchAgent(endpoint, url, { body: requestBody });
        if (shouldRetryWithFreshToken(endpoint, response)) {
            response = await fetchAgent(endpoint, url, { body: requestBody, forceRefresh: true });
        }
        const text = await response.text();
        let body = text;
        try {
            body = text ? JSON.parse(text) : null;
        } catch {
            // Keep non-JSON error bodies readable in the tester.
        }
        return {
            ok: response.ok,
            status: response.status,
            durationMs: Date.now() - started,
            body,
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
        };
    }
}

export async function checkReadiness(endpoint, {
    timeoutMs = 10000,
    expectedAgentName = null,
    expectedAgentNames = null,
    requiredProtocols = ["responses"],
} = {}) {
    const started = Date.now();
    if (isFoundryResponsesEndpoint(endpoint)) {
        return {
            ok: true,
            status: "hosted",
            durationMs: 0,
            body: "Hosted Responses endpoint discovered. Send a prompt to test it.",
            readinessStatus: { status: "hosted", reachable: true, ready: true },
            identity: null,
            protocolSupport: { status: "supported", required: ["responses"], protocols: ["responses"], missing: [] },
            configurationStatus: { status: "not_required", missingRequired: [] },
        };
    }
    const expectsIdentity = Boolean(expectedAgentNames || expectedAgentName);
    try {
        const response = await fetch(`${endpoint}/readiness`, {
            headers: readinessHeaders(endpoint, expectsIdentity),
            signal: AbortSignal.timeout(timeoutMs),
        });
        const text = await response.text();
        const parsed = parseReadinessResponse(response.ok, response.status, text);
        const actualAgentName = readinessAgentName(parsed.readiness);
        const identity = readinessIdentity(endpoint, expectedAgentNames || expectedAgentName, actualAgentName, parsed.ok);
        const identityBlocksReady = identity && identity.identity?.status !== "match";
        const protocolSupport = readinessProtocolSupport(parsed.readiness, requiredProtocols);
        const protocolBlocksReady = protocolSupport.status === "missing";
        const configurationStatus = readinessConfigurationStatus(parsed.readiness);
        const configurationBlocksReady = configurationStatus.status === "missing";
        const statusFields = readinessStatus(parsed);
        return {
            ok: parsed.ok && !identityBlocksReady && !protocolBlocksReady && !configurationBlocksReady,
            status: response.status,
            durationMs: Date.now() - started,
            body: identity?.body || parsed.body,
            readinessStatus: {
                ...statusFields,
                ready: parsed.ok && !identityBlocksReady && !protocolBlocksReady && !configurationBlocksReady,
            },
            protocolSupport,
            configurationStatus,
            ...(parsed.readiness ? { readiness: parsed.readiness } : {}),
            ...(identity?.identity ? { identity: identity.identity } : {}),
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
            readinessStatus: { status: "unreachable", reachable: false, ready: false },
            identity: null,
            protocolSupport: { status: "unknown", required: requiredProtocols, protocols: [], missing: [] },
            configurationStatus: { status: "unknown", missingRequired: [] },
        };
    }
}

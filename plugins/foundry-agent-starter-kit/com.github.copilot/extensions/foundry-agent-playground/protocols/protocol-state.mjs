export const PROTOCOLS = ["responses", "activity", "invocations"];

export function protocolLabel(protocol) {
    return {
        responses: "Responses",
        activity: "Activity",
        invocations: "Invocations",
    }[protocol] || protocol;
}

export function normalizeProtocol(value) {
    const protocol = String(value || "").trim().toLowerCase();
    return PROTOCOLS.includes(protocol) ? protocol : "responses";
}

export function protocolsFromHealth(health) {
    return Array.isArray(health?.readiness?.protocols)
        ? health.readiness.protocols.map((protocol) => String(protocol || "").trim().toLowerCase()).filter(Boolean)
        : null;
}

export function routeForProtocol(health, protocol) {
    const routes = health?.readiness?.routes;
    if (routes && typeof routes === "object" && typeof routes[protocol] === "string") {
        return routes[protocol];
    }
    return {
        responses: "/responses",
        activity: "/activity/messages",
        invocations: "/invocations",
    }[protocol] || null;
}

export function protocolEndpoint({ target, baseEndpoint, hosted, health }, protocol) {
    if (target === "hosted") {
        return {
            responses: hosted?.responsesEndpoint || "",
            activity: hosted?.activityEndpoint || "",
            invocations: hosted?.invocationsEndpoint || "",
        }[protocol] || "";
    }
    if (!baseEndpoint) return "";
    const route = routeForProtocol(health, protocol);
    return route ? `${String(baseEndpoint).replace(/\/+$/, "")}${route}` : "";
}

export function protocolCapabilities({ target, baseEndpoint, hosted, health }) {
    const declared = target === "hosted" ? null : protocolsFromHealth(health);
    return Object.fromEntries(PROTOCOLS.map((protocol) => {
        const endpoint = protocolEndpoint({ target, baseEndpoint, hosted, health }, protocol);
        if (target === "hosted") {
            return [protocol, endpoint
                ? { protocol, label: protocolLabel(protocol), status: "supported", endpoint, source: "hosted", reason: null }
                : {
                    protocol,
                    label: protocolLabel(protocol),
                    status: "unknown",
                    endpoint: "",
                    source: "hosted",
                    reason: `${protocolLabel(protocol)} endpoint has not been discovered for the hosted agent.`,
                }];
        }
        if (!health) {
            return [protocol, {
                protocol,
                label: protocolLabel(protocol),
                status: "unknown",
                endpoint,
                source: "local",
                reason: "Run a readiness check to discover protocol support.",
            }];
        }
        if (!health.ok && health.configurationStatus?.status === "missing") {
            return [protocol, {
                protocol,
                label: protocolLabel(protocol),
                status: "unknown",
                endpoint,
                source: "readiness",
                reason: `Missing required configuration: ${health.configurationStatus.missingRequired.join(", ")}.`,
            }];
        }
        if (!declared) {
            return [protocol, {
                protocol,
                label: protocolLabel(protocol),
                status: "unknown",
                endpoint,
                source: "readiness",
                reason: "Readiness did not include protocol diagnostics.",
            }];
        }
        const supported = declared.includes(protocol);
        return [protocol, supported
            ? { protocol, label: protocolLabel(protocol), status: "supported", endpoint, source: "readiness", reason: null }
            : {
                protocol,
                label: protocolLabel(protocol),
                status: "unsupported",
                endpoint: "",
                source: "readiness",
                reason: `${protocolLabel(protocol)} is not declared by this agent.`,
            }];
    }));
}

export function chooseActiveProtocol(current, capabilities) {
    const normalized = normalizeProtocol(current);
    if (capabilities?.[normalized]?.status === "supported") return normalized;
    return PROTOCOLS.find((protocol) => capabilities?.[protocol]?.status === "supported") || normalized;
}

const DEFAULT_CHANNEL = "msteams";

function id(prefix) {
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function buildActivityPayload({
    input,
    serviceUrl,
    conversationId,
    activityId,
    channelId = DEFAULT_CHANNEL,
    conversationType = "personal",
    from = { id: "canvas-user", name: "You", role: "user" },
    recipient = { id: "canvas-agent", name: "Agent", role: "bot" },
    raw = null,
} = {}) {
    if (raw && typeof raw === "object") {
        return {
            ...raw,
            id: raw.id || activityId,
            channelId: raw.channelId || channelId,
            serviceUrl,
            text: raw.text ?? input,
            conversation: raw.conversation || { id: conversationId, conversationType },
            from: raw.from || from,
            recipient: raw.recipient || recipient,
        };
    }
    return {
        type: "message",
        id: activityId,
        channelId,
        serviceUrl,
        text: input,
        conversation: { id: conversationId, conversationType },
        from,
        recipient,
    };
}

export function createActivityTurn(state, input, { raw = null, endpoint = "" } = {}) {
    const createdAt = new Date().toISOString();
    const conversationId = id("canvas-conversation");
    const activityId = id("turn");
    const serviceUrl = state.connectorBaseUrl;
    const payload = buildActivityPayload({
        input,
        raw,
        serviceUrl,
        conversationId,
        activityId,
    });
    const trackedConversationId = payload.conversation?.id || conversationId;
    const trackedActivityId = payload.id || activityId;
    return {
        id: trackedActivityId,
        input: payload.text || input || "",
        createdAt,
        target: state.target,
        protocol: "activity",
        activity: {
            conversationId: trackedConversationId,
            inboundActivityId: trackedActivityId,
            serviceUrl: payload.serviceUrl || serviceUrl,
        },
        request: {
            endpoint,
            path: "/activity/messages",
            body: payload,
        },
        response: {
            ok: false,
            status: "waiting",
            durationMs: 0,
            body: { output_text: "" },
            streaming: true,
            delivery: { mode: "activity-ack", active: true, upstreamStreaming: false },
        },
        events: [
            {
                id: id("evt"),
                at: createdAt,
                kind: "inbound.message",
                activityId: trackedActivityId,
                text: payload.text || "",
                body: payload,
            },
        ],
    };
}

export function applyConnectorEvent(turn, event) {
    turn.events ||= [];
    turn.events.push({ id: id("evt"), at: new Date().toISOString(), ...event });
    const outputText = latestVisibleActivityText(turn);
    turn.response = {
        ...(turn.response || {}),
        ok: true,
        status: turn.response?.status === "waiting" ? "streaming" : turn.response?.status || "streaming",
        body: { output_text: outputText },
        streaming: true,
        delivery: { mode: "activity-egress", active: true, upstreamStreaming: false },
    };
}

export function finishActivityTurn(turn, result) {
    if (turn.target === "hosted" && !(turn.events || []).some((event) => event.kind?.startsWith("outbound."))) {
        turn.events ||= [];
        turn.events.push({
            id: id("evt"),
            at: new Date().toISOString(),
            kind: "outbound.ack",
            text: "Activity acknowledged. Hosted connector egress cannot call back to the local canvas.",
            body: result.body,
        });
    }
    const outputText = latestVisibleActivityText(turn);
    turn.response = {
        ok: result.ok,
        status: result.status,
        durationMs: result.durationMs,
        body: outputText ? { output_text: outputText, ack: result.body } : result.body,
        streaming: false,
        delivery: { mode: "activity-ack", active: false, upstreamStreaming: false },
    };
    return turn;
}

export function latestVisibleActivityText(turn) {
    const visible = new Map();
    const order = [];
    for (const event of turn.events || []) {
        if (event.kind === "outbound.ack") {
            if (event.text) {
                visible.set(event.id, event.text);
                order.push(event.id);
            }
            continue;
        }
        if (event.kind === "outbound.message" || event.kind === "outbound.message.update") {
            const key = event.activityId || event.id;
            if (!key) {
                continue;
            }
            if (!visible.has(key)) {
                order.push(key);
            }
            visible.set(key, event.text || "");
            continue;
        }
        if (event.kind === "outbound.message.delete") {
            visible.delete(event.activityId);
        }
    }
    for (let index = order.length - 1; index >= 0; index -= 1) {
        const text = visible.get(order[index]);
        if (text) {
            return text;
        }
    }
    return "";
}

export function findActivityTurnByConversation(state, conversationId) {
    return (state.messages || []).find((message) =>
        message.protocol === "activity" && message.activity?.conversationId === conversationId
    ) || null;
}

export function activityEventFromConnector({ method, pathParts, body }) {
    const [, , conversations, conversationId, activities, activityId, maybeReactions, reactionType] = pathParts;
    if (conversations !== "conversations" || !conversationId || activities !== "activities") {
        return null;
    }
    if (method === "POST" && !activityId) {
        if (body?.type === "typing") {
            return { kind: "outbound.typing", conversationId, body };
        }
        return {
            kind: "outbound.message",
            conversationId,
            activityId: id("reply"),
            text: body?.text || "",
            body,
        };
    }
    if (method === "PUT" && activityId && maybeReactions === "reactions") {
        return {
            kind: "outbound.reaction.add",
            conversationId,
            activityId,
            reaction: reactionType || body?.type || "like",
            body,
        };
    }
    if (method === "DELETE" && activityId && maybeReactions === "reactions") {
        return {
            kind: "outbound.reaction.remove",
            conversationId,
            activityId,
            reaction: reactionType || "like",
            body,
        };
    }
    if (method === "PUT" && activityId) {
        return {
            kind: "outbound.message.update",
            conversationId,
            activityId,
            text: body?.text || "",
            body,
        };
    }
    if (method === "DELETE" && activityId) {
        return { kind: "outbound.message.delete", conversationId, activityId, body };
    }
    return null;
}

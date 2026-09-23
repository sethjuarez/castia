import assert from "node:assert/strict";
import test from "node:test";
import { createRequestHandler } from "../routes/playground-routes.mjs";

function jsonResponse() {
    return {
        status: null,
        headers: null,
        body: "",
        writeHead(status, headers) {
            this.status = status;
            this.headers = headers;
        },
        end(body = "") {
            this.body = body;
        },
    };
}

function jsonRequest(method, url, body = null) {
    const chunks = body === null ? [] : [Buffer.from(JSON.stringify(body))];
    return {
        method,
        url,
        async *[Symbol.asyncIterator]() {
            yield* chunks;
        },
    };
}

test("request handler factory returns a route handler and 404s unknown routes", async () => {
    const handleRequest = createRequestHandler({});
    const response = jsonResponse();

    await handleRequest({ method: "GET", url: "/missing" }, response, {});

    assert.equal(response.status, 404);
    assert.equal(response.headers["Content-Type"], "application/json; charset=utf-8");
    assert.deepEqual(JSON.parse(response.body), { error: "Not found." });
});

test("request handler routes injected state snapshots", async () => {
    const handleRequest = createRequestHandler({
        snapshotState: (state) => ({ marker: state.marker }),
    });
    const response = jsonResponse();

    await handleRequest({ method: "GET", url: "/api/state" }, response, { marker: "snapshot" });

    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(response.body), { marker: "snapshot" });
});

test("request handler validates target route input before mutating state", async () => {
    const handleRequest = createRequestHandler({});
    const state = { target: "local" };
    const response = jsonResponse();

    await handleRequest(jsonRequest("POST", "/api/target", { target: "invalid" }), response, state);

    assert.equal(response.status, 400);
    assert.deepEqual(JSON.parse(response.body), { error: "Target must be local or hosted." });
    assert.equal(state.target, "local");
});

test("protocol route rejects unsupported protocols without mutating active protocol", async () => {
    const handleRequest = createRequestHandler({
        snapshotState: (state) => ({ activeProtocol: state.activeProtocol }),
    });
    const state = {
        target: "local",
        activeProtocol: "responses",
        agents: [{ id: "agent-1", serviceName: "agent-one" }],
        selectedAgentId: "agent-1",
        localEndpoints: { "agent-1": "http://127.0.0.1:8088" },
        hosted: {},
        lastHealth: {
            ok: true,
            readiness: { protocols: ["responses"], routes: { responses: "/responses" } },
        },
        lastHealthByTarget: {},
    };
    const response = jsonResponse();

    await handleRequest(jsonRequest("POST", "/api/protocol", { protocol: "activity" }), response, state);

    assert.equal(response.status, 409);
    assert.equal(state.activeProtocol, "responses");
});

test("protocol route accepts supported Activity protocol", async () => {
    const handleRequest = createRequestHandler({
        snapshotState: (state) => ({ activeProtocol: state.activeProtocol }),
    });
    const state = {
        target: "local",
        activeProtocol: "responses",
        agents: [{ id: "agent-1", serviceName: "agent-one" }],
        selectedAgentId: "agent-1",
        localEndpoints: { "agent-1": "http://127.0.0.1:8088" },
        hosted: {},
        lastHealth: {
            ok: true,
            readiness: { protocols: ["activity", "responses"], routes: { activity: "/activity/messages", responses: "/responses" } },
        },
        lastHealthByTarget: {},
        activity: [],
    };
    const response = jsonResponse();

    await handleRequest(jsonRequest("POST", "/api/protocol", { protocol: "activity" }), response, state);

    assert.equal(response.status, 200);
    assert.equal(state.activeProtocol, "activity");
});

test("connector route captures reactions, updates, and deletes", async () => {
    const handleRequest = createRequestHandler({
        snapshotState: (state) => ({ events: state.messages[0].events }),
    });
    const state = {
        messages: [
            {
                protocol: "activity",
                activity: { conversationId: "conversation-1" },
                events: [],
                response: { status: "waiting" },
            },
        ],
    };

    let response = jsonResponse();
    await handleRequest(jsonRequest("PUT", "/connector/v3/conversations/conversation-1/activities/turn-1/reactions/eyes", {}), response, state);
    response = jsonResponse();
    await handleRequest(jsonRequest("PUT", "/connector/v3/conversations/conversation-1/activities/reply-1", { text: "updated" }), response, state);
    response = jsonResponse();
    await handleRequest(jsonRequest("DELETE", "/connector/v3/conversations/conversation-1/activities/reply-1"), response, state);

    assert.deepEqual(state.messages[0].events.map((event) => event.kind), [
        "outbound.reaction.add",
        "outbound.message.update",
        "outbound.message.delete",
    ]);
});

test("connector route captures Activity Protocol egress on the matching turn", async () => {
    let broadcasted = false;
    const handleRequest = createRequestHandler({
        snapshotState: (state) => ({ events: state.messages[0].events }),
        broadcastSnapshot: () => {
            broadcasted = true;
        },
    });
    const state = {
        messages: [
            {
                protocol: "activity",
                activity: { conversationId: "conversation-1" },
                events: [],
                response: { status: "waiting" },
            },
        ],
    };
    const response = jsonResponse();

    await handleRequest(jsonRequest("POST", "/connector/v3/conversations/conversation-1/activities", {
        type: "message",
        text: "agent reply",
    }), response, state);

    assert.equal(response.status, 200);
    assert.equal(broadcasted, true);
    assert.deepEqual(JSON.parse(response.body).id, state.messages[0].events[0].activityId);
    assert.equal(state.messages[0].events[0].kind, "outbound.message");
    assert.equal(state.messages[0].events[0].text, "agent reply");
    assert.equal(state.messages[0].response.body.output_text, "agent reply");
});

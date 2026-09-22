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

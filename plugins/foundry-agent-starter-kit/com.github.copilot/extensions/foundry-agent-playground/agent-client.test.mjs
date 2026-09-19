import assert from "node:assert/strict";
import { test } from "node:test";
import {
    checkReadiness,
    configureAgentClient,
    requestHeadersForEndpoint,
    responseText,
    responsesUrl,
} from "./agent-client.mjs";

test("responsesUrl normalizes Foundry hosted Responses endpoints", () => {
    assert.equal(
        responsesUrl("https://acct.services.ai.azure.com/api/projects/proj/agents/minimal-agent/versions/6/endpoint/protocols/openai/responses"),
        "https://acct.services.ai.azure.com/api/projects/proj/agents/minimal-agent/endpoint/protocols/openai/responses?api-version=v1",
    );
    assert.equal(responsesUrl("http://127.0.0.1:8088"), "http://127.0.0.1:8088/responses");
});

test("requestHeadersForEndpoint adds Azure auth only for hosted endpoints", async () => {
    let calls = 0;
    configureAgentClient({
        runCommand: async () => {
            calls += 1;
            return { code: 0, output: "token\n" };
        },
    });

    assert.deepEqual(await requestHeadersForEndpoint("http://127.0.0.1:8088"), {
        "Content-Type": "application/json",
    });
    assert.equal(calls, 0);

    const headers = await requestHeadersForEndpoint("https://acct.services.ai.azure.com/api/projects/proj/agents/a/endpoint/protocols/openai/responses");
    assert.equal(headers.Authorization, "Bearer token");
    assert.equal(calls, 1);
});

test("checkReadiness treats hosted Responses endpoints as ready without probing /readiness", async () => {
    const result = await checkReadiness(
        "https://acct.services.ai.azure.com/api/projects/proj/agents/a/endpoint/protocols/openai/responses?api-version=v1",
    );

    assert.equal(result.ok, true);
    assert.equal(result.status, "hosted");
});

test("responseText prefers Responses output_text", () => {
    assert.equal(responseText({ output_text: "hello", output: "fallback" }), "hello");
    assert.equal(responseText("plain"), "plain");
});

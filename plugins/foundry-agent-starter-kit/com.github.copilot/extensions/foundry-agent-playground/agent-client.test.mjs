import assert from "node:assert/strict";
import { test } from "node:test";
import {
    checkReadiness,
    configureAgentClient,
    parseReadinessResponse,
    requestHeadersForEndpoint,
    readinessText,
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

test("parseReadinessResponse treats Castia JSON status ok as ready", () => {
    const result = parseReadinessResponse(true, 200, JSON.stringify({
        status: "ok",
        agent: { name: "contract-expert" },
        protocols: ["responses", "invocations"],
        routes: { readiness: "/readiness", responses: "/responses" },
        configuration: { missing_required: [] },
    }));

    assert.equal(result.ok, true);
    assert.equal(result.body, "agent=contract-expert; protocols=responses,invocations");
    assert.equal(result.readiness.agent.name, "contract-expert");
});

test("checkReadiness rejects endpoints owned by a different selected agent", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "ok",
        agent: { name: "contract-expert" },
        protocols: ["responses"],
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8095", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.equal(result.body, "Endpoint belongs to contract-expert; selected contract-policy-expert.");
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-expert",
            status: "mismatch",
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness does not accept legacy readiness when agent identity is required", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response("Agent running!", { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8095", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.equal(result.identity.status, "unknown");
        assert.match(result.body, /Readiness identity is unknown/);
        assert.match(result.body, /selected contract-policy-expert/);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness accepts structured readiness for the selected agent", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "ok",
        agent: { name: "contract-policy-expert" },
        protocols: ["responses"],
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8096", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, true);
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-policy-expert",
            status: "match",
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness reports identity mismatch even when stale endpoint is not otherwise ready", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "configuration_missing",
        agent: { name: "contract-expert" },
        protocols: ["responses"],
        configuration: { missing_required: ["FOUNDRY_PROJECT_ENDPOINT"] },
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8095", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.equal(result.body, "Endpoint belongs to contract-expert; selected contract-policy-expert.");
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-expert",
            status: "mismatch",
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness accepts normalized selected agent names", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "ok",
        agent: { name: "contract_policy_expert" },
        protocols: ["responses"],
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8096", {
            expectedAgentNames: ["Contract Policy Expert", "contract-policy-expert"],
        });

        assert.equal(result.ok, true);
        assert.deepEqual(result.identity, {
            expected: "Contract Policy Expert",
            actual: "contract_policy_expert",
            status: "match",
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("parseReadinessResponse keeps Castia configuration_missing from becoming ready", () => {
    const result = parseReadinessResponse(true, 200, JSON.stringify({
        status: "configuration_missing",
        agent: { name: "contract-policy-expert" },
        protocols: ["responses"],
        configuration: { missing_required: ["FOUNDRY_PROJECT_ENDPOINT"] },
    }));

    assert.equal(result.ok, false);
    assert.equal(
        result.body,
        "agent=contract-policy-expert; protocols=responses; missing=FOUNDRY_PROJECT_ENDPOINT",
    );
    assert.deepEqual(result.readiness.configuration.missing_required, ["FOUNDRY_PROJECT_ENDPOINT"]);
});

test("readinessText preserves useful non-Castia readiness bodies", () => {
    assert.equal(readinessText("Agent running!"), "Agent running!");
    assert.equal(readinessText({ ok: true }), "{\n  \"ok\": true\n}");
});

test("responseText prefers Responses output_text", () => {
    assert.equal(responseText({ output_text: "hello", output: "fallback" }), "hello");
    assert.equal(responseText("plain"), "plain");
});

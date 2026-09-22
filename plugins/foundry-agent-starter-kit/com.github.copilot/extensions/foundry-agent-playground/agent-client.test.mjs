import assert from "node:assert/strict";
import { test } from "node:test";
import {
    callAgent,
    checkReadiness,
    configureAgentClient,
    parseReadinessResponse,
    requestHeadersForEndpoint,
    readinessText,
    responseText,
    responsesUrl,
} from "./client/agent-client.mjs";

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

test("callAgent refreshes hosted Azure token once after auth failure", async () => {
    const previousFetch = globalThis.fetch;
    const endpoint = "https://acct.services.ai.azure.com/api/projects/proj/agents/a/endpoint/protocols/openai/responses?api-version=v1";
    const commandOutputs = ["stale-token\n", "fresh-token\n"];
    const authorizations = [];
    configureAgentClient({
        runCommand: async () => ({ code: 0, output: commandOutputs.shift() }),
    });
    await requestHeadersForEndpoint(endpoint, null, { forceRefresh: true });
    globalThis.fetch = async (_url, options) => {
        authorizations.push(options.headers.Authorization);
        if (authorizations.length === 1) {
            return new Response(JSON.stringify({ error: "forbidden" }), { status: 403 });
        }
        return new Response(JSON.stringify({ output_text: "ok" }), { status: 200 });
    };
    try {
        const result = await callAgent(
            endpoint,
            "/responses",
            { input: "hello" },
        );

        assert.equal(result.ok, true);
        assert.equal(result.status, 200);
        assert.deepEqual(result.body, { output_text: "ok" });
        assert.equal(authorizations.length, 2);
        assert.match(authorizations[0], /^Bearer /);
        assert.match(authorizations[1], /^Bearer /);
        assert.notEqual(authorizations[0], authorizations[1]);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness treats hosted Responses endpoints as ready without probing /readiness", async () => {
    const result = await checkReadiness(
        "https://acct.services.ai.azure.com/api/projects/proj/agents/a/endpoint/protocols/openai/responses?api-version=v1",
    );

    assert.equal(result.ok, true);
    assert.equal(result.status, "hosted");
    assert.deepEqual(result.readinessStatus, { status: "hosted", reachable: true, ready: true });
    assert.deepEqual(result.protocolSupport, {
        status: "supported",
        required: ["responses"],
        protocols: ["responses"],
        missing: [],
    });
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
        assert.deepEqual(result.readinessStatus, { status: "ok", reachable: true, ready: false });
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-expert",
            status: "mismatch",
        });
        assert.deepEqual(result.protocolSupport, {
            status: "supported",
            required: ["responses"],
            protocols: ["responses"],
            missing: [],
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
    const calls = [];
    globalThis.fetch = async (_url, options = {}) => {
        calls.push(options);
        return new Response(JSON.stringify({
            status: "ok",
            agent: { name: "contract-policy-expert" },
            protocols: ["responses"],
        }), { status: 200 });
    };
    try {
        const result = await checkReadiness("http://127.0.0.1:8096", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, true);
        assert.equal(calls[0].headers["X-Castia-Readiness"], "diagnostics");
        assert.deepEqual(result.readinessStatus, { status: "ok", reachable: true, ready: true });
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-policy-expert",
            status: "match",
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness does not request diagnostics without local identity matching", async () => {
    const previousFetch = globalThis.fetch;
    const calls = [];
    globalThis.fetch = async (_url, options = {}) => {
        calls.push(options);
        return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    };
    try {
        const result = await checkReadiness("http://127.0.0.1:8096");

        assert.equal(result.ok, true);
        assert.deepEqual(calls[0].headers, {});
        assert.deepEqual(result.readiness, { status: "ok" });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness does not request diagnostics from non-loopback endpoints", async () => {
    const previousFetch = globalThis.fetch;
    const calls = [];
    globalThis.fetch = async (_url, options = {}) => {
        calls.push(options);
        return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
    };
    try {
        const result = await checkReadiness("https://agent.example.test", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.deepEqual(calls[0].headers, {});
        assert.equal(result.identity.status, "unknown");
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

test("checkReadiness reports missing required configuration separately from reachability", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "configuration_missing",
        agent: { name: "contract-policy-expert" },
        protocols: ["responses"],
        configuration: { missing_required: ["FOUNDRY_PROJECT_ENDPOINT"] },
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8096", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.deepEqual(result.readinessStatus, { status: "configuration_missing", reachable: true, ready: false });
        assert.deepEqual(result.identity, {
            expected: "contract-policy-expert",
            actual: "contract-policy-expert",
            status: "match",
        });
        assert.deepEqual(result.configurationStatus, {
            status: "missing",
            missingRequired: ["FOUNDRY_PROJECT_ENDPOINT"],
        });
        assert.deepEqual(result.protocolSupport, {
            status: "supported",
            required: ["responses"],
            protocols: ["responses"],
            missing: [],
        });
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test("checkReadiness reports reachable endpoints missing required protocol support", async () => {
    const previousFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({
        status: "ok",
        agent: { name: "contract-policy-expert" },
        protocols: ["invocations"],
        configuration: { missing_required: [] },
    }), { status: 200 });
    try {
        const result = await checkReadiness("http://127.0.0.1:8096", {
            expectedAgentName: "contract-policy-expert",
        });

        assert.equal(result.ok, false);
        assert.deepEqual(result.readinessStatus, { status: "ok", reachable: true, ready: false });
        assert.deepEqual(result.protocolSupport, {
            status: "missing",
            required: ["responses"],
            protocols: ["invocations"],
            missing: ["responses"],
        });
        assert.deepEqual(result.configurationStatus, {
            status: "configured",
            missingRequired: [],
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

test("responseText extracts answer text from completed response envelopes", () => {
    assert.equal(
        responseText({
            type: "response.completed",
            response: {
                id: "resp-1",
                output_text: "final answer",
                output: [{ type: "message", content: [{ type: "output_text", text: "fallback" }] }],
            },
        }),
        "final answer",
    );
});

test("responseText does not expose empty response envelopes as JSON", () => {
    assert.equal(responseText({ output_text: "", output: [] }), "");
    assert.equal(responseText({ response: { output_text: "", output: [] } }), "");
});

test("responseText extracts useful structured errors", () => {
    assert.equal(responseText({ error: { message: "deployment unavailable", code: "failed_dependency" } }), "deployment unavailable");
    assert.equal(responseText({ error: { code: "failed_dependency" } }), "failed_dependency");
    assert.equal(responseText({ detail: "bad request" }), "bad request");
});

test("responseText falls back to nested Responses message content when output_text is blank", () => {
    assert.equal(
        responseText({
            output_text: "",
            output: [
                {
                    type: "message",
                    content: [
                        { type: "output_text", text: "nested answer" },
                    ],
                },
            ],
        }),
        "nested answer",
    );
});

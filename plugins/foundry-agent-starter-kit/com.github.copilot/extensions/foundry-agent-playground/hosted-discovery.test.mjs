import assert from "node:assert/strict";
import { test } from "node:test";
import { discoverHostedContextFromFoundry, hostedContextFromAzd } from "./client/hosted-discovery.mjs";

const projectEndpoint = "https://acct.services.ai.azure.com/api/projects/waypoint";
const now = new Date("2026-09-18T12:00:00.000Z");

function agent(overrides = {}) {
    return {
        id: "modules\\agents\\contract-expert:contract-expert",
        serviceName: "contract-expert",
        displayName: "contract-expert",
        rootLabel: "modules\\agents\\contract-expert",
        envPrefix: "AGENT_CONTRACT_EXPERT",
        ...overrides,
    };
}

function emptyHosted(selected = agent()) {
    return {
        agentName: selected.displayName,
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

function mockFetch(routes) {
    const calls = [];
    const fetchImpl = async (url) => {
        calls.push(url);
        const path = new URL(url).pathname;
        const route = routes[path] || { status: 404, body: { error: { message: "not found" } } };
        return new Response(JSON.stringify(route.body), {
            status: route.status || 200,
            headers: { "content-type": "application/json" },
        });
    };
    fetchImpl.calls = calls;
    return fetchImpl;
}

test("remote Foundry discovery populates hosted context when azd env has no AGENT outputs", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: { AZURE_ENV_NAME: "dev" },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: {
                id: "agent-123",
                name: "contract-expert",
                active_version: "7",
                agent_endpoints: {
                    responses: `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
                    activity: `${projectEndpoint}/agents/contract-expert/versions/7/endpoint/protocols/activity`,
                },
            },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [{ version: "7", status: "active", created_at: "2026-09-18T11:00:00Z" }] },
        },
        "/api/projects/waypoint/agents/contract-expert/versions/7": {
            body: {
                version: "7",
                status: "active",
                endpoints: {
                    invocations: `${projectEndpoint}/agents/contract-expert/versions/7/endpoint/protocols/invocations`,
                },
            },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.ok, true);
    assert.equal(result.hosted.agentName, "contract-expert");
    assert.equal(result.hosted.agentId, "agent-123");
    assert.equal(result.hosted.version, "7");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
    assert.equal(result.hosted.activityEndpoint, `${projectEndpoint}/agents/contract-expert/versions/7/endpoint/protocols/activity`);
    assert.equal(result.hosted.invocationsEndpoint, `${projectEndpoint}/agents/contract-expert/versions/7/endpoint/protocols/invocations`);
    assert.equal(result.hosted.lastRefreshSource, "foundry");
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "found");
});

test("remote Foundry discovery leaves clean state ready for first deploy when no deployed version exists", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: { id: "agent-123", name: "contract-expert" },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [] },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.ok, true);
    assert.equal(result.hosted.version, null);
    assert.equal(result.hosted.responsesEndpoint, null);
    assert.equal(result.hosted.status, "not_deployed");
    assert.equal(result.hosted.lastRefreshSource, "foundry");
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "not_deployed");
});

test("remote Foundry propagation lag preserves fresh azd deployment outputs", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {
            AGENT_CONTRACT_EXPERT_NAME: "contract-expert",
            AGENT_CONTRACT_EXPERT_ID: "agent-from-azd",
            AGENT_CONTRACT_EXPERT_VERSION: "9",
            AGENT_CONTRACT_EXPERT_RESPONSES_ENDPOINT: `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
        },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: { id: "agent-from-azd", name: "contract-expert" },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [] },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.ok, true);
    assert.equal(result.hosted.agentId, "agent-from-azd");
    assert.equal(result.hosted.version, "9");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
    assert.equal(result.hosted.lastRefreshSource, "azd");
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "not_deployed_using_cache");
});

test("switching Foundry projects clears cached deployment endpoints from the previous project", () => {
    const selected = agent();
    const oldProjectEndpoint = "https://old.services.ai.azure.com/api/projects/old";
    const switched = hostedContextFromAzd({
        agent: selected,
        currentHosted: {
            ...emptyHosted(selected),
            agentId: "old-agent",
            version: "6",
            responsesEndpoint: `${oldProjectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
            activityEndpoint: `${oldProjectEndpoint}/agents/contract-expert/versions/6/endpoint/protocols/activity`,
            invocationsEndpoint: `${oldProjectEndpoint}/agents/contract-expert/versions/6/endpoint/protocols/invocations`,
            projectEndpoint: oldProjectEndpoint,
            status: "active",
            lastRefreshSource: "foundry",
        },
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });

    assert.equal(switched.projectEndpoint, projectEndpoint);
    assert.equal(switched.agentId, null);
    assert.equal(switched.version, null);
    assert.equal(switched.responsesEndpoint, null);
    assert.equal(switched.activityEndpoint, null);
    assert.equal(switched.invocationsEndpoint, null);
    assert.equal(switched.status, "not_deployed");
});

test("remote Foundry discovery error preserves azd deployment outputs as fallback", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {
            AGENT_CONTRACT_EXPERT_NAME: "contract-expert",
            AGENT_CONTRACT_EXPERT_ID: "agent-from-azd",
            AGENT_CONTRACT_EXPERT_VERSION: "9",
            AGENT_CONTRACT_EXPERT_RESPONSES_ENDPOINT: `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
        },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            status: 500,
            body: { error: { message: "temporary Foundry outage" } },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.ok, false);
    assert.equal(result.hosted.agentId, "agent-from-azd");
    assert.equal(result.hosted.version, "9");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
    assert.equal(result.hosted.lastRefreshSource, "azd");
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "error");
    assert.match(result.hosted.lastRemoteDiscoveryMessage, /temporary Foundry outage/);
});

test("remote discovery ignores unrelated OpenAI URLs and derives versioned protocol endpoints", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });
    const unrelatedOpenAiUrl = "https://acct.openai.azure.com/openai/v1/responses";
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: {
                id: "agent-123",
                name: "contract-expert",
                active_version: "7",
                model: {
                    azure_openai_endpoint: unrelatedOpenAiUrl,
                },
            },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [{ version: "7", status: "active" }] },
        },
        "/api/projects/waypoint/agents/contract-expert/versions/7": {
            body: { version: "7", status: "active" },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.notEqual(result.hosted.responsesEndpoint, unrelatedOpenAiUrl);
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
});

test("remote Foundry discovery refreshes hosted state for the newly selected agent", async () => {
    const selected = agent({
        id: "modules\\agents\\policy-agent:policy-agent",
        serviceName: "policy-agent",
        displayName: "Policy Agent",
        rootLabel: "modules\\agents\\policy-agent",
        envPrefix: "AGENT_POLICY_AGENT",
    });
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/Policy%20Agent": { status: 404, body: { error: { message: "not found" } } },
        "/api/projects/waypoint/agents/policy-agent": {
            body: { id: "agent-policy", name: "policy-agent", active_version: "4" },
        },
        "/api/projects/waypoint/agents/policy-agent/versions": {
            body: { data: [{ version: "4", status: "active" }] },
        },
        "/api/projects/waypoint/agents/policy-agent/versions/4": {
            body: { version: "4", status: "active" },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.agentName, "policy-agent");
    assert.equal(result.hosted.agentId, "agent-policy");
    assert.equal(result.hosted.version, "4");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/policy-agent/endpoint/protocols/openai/responses?api-version=v1`);
    assert.ok(fetchImpl.calls.some((url) => new URL(url).pathname.endsWith("/agents/policy-agent")));
});

test("remote Foundry discovery can match a suffixed agent by cached agent id", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {
            AGENT_CONTRACT_EXPERT_ID: "agent-suffixed",
        },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": { status: 404, body: { error: { message: "not found" } } },
        "/api/projects/waypoint/agents": {
            body: { data: [{ id: "agent-suffixed", name: "contract-expert-dev", active_version: "2" }] },
        },
        "/api/projects/waypoint/agents/contract-expert-dev/versions": {
            body: { data: [{ version: "2", status: "active" }] },
        },
        "/api/projects/waypoint/agents/contract-expert-dev/versions/2": {
            body: { version: "2", status: "active" },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.agentName, "contract-expert-dev");
    assert.equal(result.hosted.agentId, "agent-suffixed");
    assert.equal(result.hosted.version, "2");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert-dev/endpoint/protocols/openai/responses?api-version=v1`);
});

test("remote Foundry discovery does not choose an ambiguous prefix agent match", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": { status: 404, body: { error: { message: "not found" } } },
        "/api/projects/waypoint/agents": {
            body: {
                data: [
                    { id: "agent-dev", name: "contract-expert-dev", active_version: "2" },
                    { id: "agent-eval", name: "contract-expert-eval", active_version: "3" },
                ],
            },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.version, null);
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "not_found");
    assert.match(result.hosted.lastRemoteDiscoveryMessage, /Multiple hosted agents matched/);
});

test("remote Foundry discovery advances from version n to n plus 1 after deployment", async () => {
    const selected = agent();
    const previous = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {
            AGENT_CONTRACT_EXPERT_NAME: "contract-expert",
            AGENT_CONTRACT_EXPERT_VERSION: "5",
            AGENT_CONTRACT_EXPERT_RESPONSES_ENDPOINT: `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
        },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: { id: "agent-123", name: "contract-expert", active_version: "6" },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: {
                data: [
                    { version: "6", status: "active", created_at: "2026-09-18T12:05:00Z" },
                    { version: "5", status: "active", created_at: "2026-09-18T12:00:00Z" },
                ],
            },
        },
        "/api/projects/waypoint/agents/contract-expert/versions/6": {
            body: {
                version: "6",
                status: "active",
                definition: {
                    protocol_versions: [
                        { protocol: "activity", version: "2.0.0" },
                        { protocol: "responses", version: "2.0.0" },
                        { protocol: "invocations", version: "2.0.0" },
                    ],
                },
            },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: previous,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.version, "6");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
    assert.equal(result.hosted.activityEndpoint, `${projectEndpoint}/agents/contract-expert/versions/6/endpoint/protocols/activity`);
    assert.equal(result.hosted.invocationsEndpoint, `${projectEndpoint}/agents/contract-expert/versions/6/endpoint/protocols/invocations`);
    assert.equal(result.hosted.lastRefreshSource, "foundry");
});

test("remote Foundry stale version does not overwrite cached n plus 1 deployment", async () => {
    const selected = agent();
    const cached = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {
            AGENT_CONTRACT_EXPERT_NAME: "contract-expert",
            AGENT_CONTRACT_EXPERT_VERSION: "6",
            AGENT_CONTRACT_EXPERT_RESPONSES_ENDPOINT: `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`,
        },
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: { id: "agent-123", name: "contract-expert", active_version: "5" },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [{ version: "5", status: "active" }] },
        },
        "/api/projects/waypoint/agents/contract-expert/versions/5": {
            body: { version: "5", status: "active" },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: cached,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.version, "6");
    assert.equal(result.hosted.responsesEndpoint, `${projectEndpoint}/agents/contract-expert/endpoint/protocols/openai/responses?api-version=v1`);
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "stale_remote_using_cache");
});

test("remote Foundry failed version is not treated as deployed", async () => {
    const selected = agent();
    const azdHosted = hostedContextFromAzd({
        agent: selected,
        currentHosted: emptyHosted(selected),
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        values: {},
        exitCode: 0,
        now,
    });
    const fetchImpl = mockFetch({
        "/api/projects/waypoint/agents/contract-expert": {
            body: { id: "agent-123", name: "contract-expert", active_version: "8" },
        },
        "/api/projects/waypoint/agents/contract-expert/versions": {
            body: { data: [{ version: "8", status: "failed" }] },
        },
    });

    const result = await discoverHostedContextFromFoundry({
        agent: selected,
        currentHosted: azdHosted,
        foundryConnection: { projectEndpoint, modelDeployment: "gpt-6-astra" },
        fetchImpl,
        accessTokenProvider: async () => "token",
        now,
    });

    assert.equal(result.hosted.version, null);
    assert.equal(result.hosted.responsesEndpoint, null);
    assert.equal(result.hosted.status, "not_deployed");
    assert.equal(result.hosted.lastRemoteDiscoveryStatus, "not_deployed");
});

import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { discoverAgents } from "../client/agent-discovery.mjs";

test("discoverAgents de-duplicates root and nested hosted agent manifests by id", async () => {
    const workspace = await mkdtemp(join(tmpdir(), "foundry-agent-playground-"));
    try {
        await mkdir(join(workspace, "modules", "agents", "contract-expert"), { recursive: true });
        await mkdir(join(workspace, "modules", "agents", "contract-policy-expert"), { recursive: true });
        await writeFile(
            join(workspace, "azure.yaml"),
            [
                "services:",
                "  contract-expert:",
                "    project: modules/agents/contract-expert",
                "    host: azure.ai.agent",
                "    name: Contract Expert From Root",
                "  contract-policy-expert:",
                "    project: modules/agents/contract-policy-expert",
                "    host: azure.ai.agent",
                "  contract-agent:",
                "    project: modules/agents/contract-agent",
                "    host: azure.ai.agent",
            ].join("\n"),
            "utf8",
        );
        await writeFile(
            join(workspace, "modules", "agents", "contract-expert", "azure.yaml"),
            ["services:", "  contract-expert:", "    project: .", "    host: azure.ai.agent", "    name: Contract Expert Nested"].join("\n"),
            "utf8",
        );
        await writeFile(
            join(workspace, "modules", "agents", "contract-policy-expert", "azure.yaml"),
            ["services:", "  contract-policy-expert:", "    project: .", "    host: azure.ai.agent"].join("\n"),
            "utf8",
        );

        const agents = await discoverAgents({ workspace });

        assert.deepEqual(
            agents.map((agent) => agent.id),
            [
                "modules\\agents\\contract-agent:contract-agent",
                "modules\\agents\\contract-expert:contract-expert",
                "modules\\agents\\contract-policy-expert:contract-policy-expert",
            ],
        );
        assert.equal(agents.find((agent) => agent.serviceName === "contract-expert")?.displayName, "Contract Expert From Root");
    } finally {
        await rm(workspace, { recursive: true, force: true });
    }
});

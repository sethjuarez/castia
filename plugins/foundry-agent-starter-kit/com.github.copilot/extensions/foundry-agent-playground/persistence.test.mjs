import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
    appendOperationRecord,
    configureRuntimeStore,
    persistStateSnapshot,
    sessionStoreRoot,
} from "./persistence.mjs";

test("sessionStoreRoot stays under session-scoped files storage", () => {
    const root = sessionStoreRoot("session-123", { root: "C:\\tmp\\custom-store" });

    assert.equal(root, "C:\\tmp\\custom-store");
    assert.equal(sessionStoreRoot(null), null);
    assert.match(sessionStoreRoot("session-123", { instanceId: "canvas 1" }), /session-123.*foundry-agent-playground.*canvas-1/);
});

test("persistence writes state.json and operations.jsonl", async () => {
    const root = await mkdtemp(join(tmpdir(), "playground-store-"));
    const state = {};
    configureRuntimeStore(state, { sessionId: "session-123", root });
    try {
        await persistStateSnapshot(state, { selectedAgentId: "agent-1" });
        await appendOperationRecord(state, { event: "started", operation: { id: "op-1" } });

        assert.match(await readFile(state.runtimeStore.statePath, "utf8"), /"selectedAgentId": "agent-1"/);
        assert.match(await readFile(state.runtimeStore.operationsPath, "utf8"), /"event":"started"/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

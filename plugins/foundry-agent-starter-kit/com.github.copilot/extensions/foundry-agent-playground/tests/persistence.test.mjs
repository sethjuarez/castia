import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
    appendOperationRecord,
    configureRuntimeStore,
    legacySessionStoreRoot,
    PERSISTED_STATE_SCHEMA_VERSION,
    persistStateSnapshot,
    pluginDataRoot,
    pluginRuntimeRoot,
    readStateSnapshot,
    sessionStoreRoot,
} from "../state/persistence.mjs";

test("plugin data roots stay under per-user plugin-data storage", () => {
    assert.match(pluginDataRoot(), /[\\/]plugin-data[\\/]foundry-agent-starter-kit$/);
    assert.match(pluginRuntimeRoot(), /[\\/]plugin-data[\\/]foundry-agent-starter-kit[\\/]runtime$/);
});

test("sessionStoreRoot stays under plugin-scoped user data storage", () => {
    const root = sessionStoreRoot("session-123", { root: "C:\\tmp\\custom-store" });

    assert.equal(root, "C:\\tmp\\custom-store");
    assert.equal(sessionStoreRoot(null), null);
    assert.match(
        sessionStoreRoot("session-123", { instanceId: "canvas 1" }),
        /plugin-data.*foundry-agent-starter-kit.*sessions.*session-123.*foundry-agent-playground.*canvas-1/,
    );
    assert.match(
        legacySessionStoreRoot("session-123", { instanceId: "canvas 1" }),
        /session-state.*session-123.*files.*foundry-agent-playground.*canvas-1/,
    );
});

test("persistence writes state.json and operations.jsonl", async () => {
    const root = await mkdtemp(join(tmpdir(), "playground-store-"));
    const state = {};
    configureRuntimeStore(state, { sessionId: "session-123", root });
    try {
        await persistStateSnapshot(state, { selectedAgentId: "agent-1" });
        await appendOperationRecord(state, { event: "started", operation: { id: "op-1" } });

        const persisted = JSON.parse(await readFile(state.runtimeStore.statePath, "utf8"));
        assert.equal(persisted.schemaVersion, PERSISTED_STATE_SCHEMA_VERSION);
        assert.match(await readFile(state.runtimeStore.statePath, "utf8"), /"selectedAgentId": "agent-1"/);
        assert.match(await readFile(state.runtimeStore.operationsPath, "utf8"), /"event":"started"/);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("readStateSnapshot reads existing snapshots and tolerates missing files", async () => {
    const root = await mkdtemp(join(tmpdir(), "playground-store-"));
    const state = {};
    configureRuntimeStore(state, { sessionId: "session-123", root });
    try {
        assert.deepEqual(await readStateSnapshot(state), {
            snapshot: null,
            path: state.runtimeStore.statePath,
            error: null,
        });

        await writeFile(state.runtimeStore.statePath, '{ "selectedAgentId": "agent-1" }\n', "utf8");
        const result = await readStateSnapshot(state);

        assert.deepEqual(result.snapshot, { selectedAgentId: "agent-1" });
        assert.equal(result.path, state.runtimeStore.statePath);
        assert.equal(result.error, null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("readStateSnapshot reports corrupt snapshots without throwing", async () => {
    const root = await mkdtemp(join(tmpdir(), "playground-store-"));
    const state = {};
    configureRuntimeStore(state, { sessionId: "session-123", root });
    try {
        await writeFile(state.runtimeStore.statePath, "{ not json", "utf8");
        const result = await readStateSnapshot(state);

        assert.equal(result.snapshot, null);
        assert.equal(result.path, state.runtimeStore.statePath);
        assert.ok(result.error instanceof Error);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test("readStateSnapshot restores legacy session-state snapshots", async () => {
    const root = await mkdtemp(join(tmpdir(), "playground-store-"));
    const legacyRoot = await mkdtemp(join(tmpdir(), "playground-legacy-store-"));
    const state = {
        runtimeStore: {
            root,
            statePath: join(root, "state.json"),
            operationsPath: join(root, "operations.jsonl"),
            legacyStatePath: join(legacyRoot, "state.json"),
            legacyOperationsPath: join(legacyRoot, "operations.jsonl"),
        },
    };
    try {
        await writeFile(state.runtimeStore.legacyStatePath, '{ "selectedAgentId": "agent-legacy" }\n', "utf8");
        const result = await readStateSnapshot(state);

        assert.deepEqual(result.snapshot, { selectedAgentId: "agent-legacy" });
        assert.equal(result.path, state.runtimeStore.legacyStatePath);
        assert.equal(result.error, null);
    } finally {
        await rm(root, { recursive: true, force: true });
        await rm(legacyRoot, { recursive: true, force: true });
    }
});

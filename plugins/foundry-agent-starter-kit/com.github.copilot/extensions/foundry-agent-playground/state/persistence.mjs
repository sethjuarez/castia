import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const PERSISTED_STATE_SCHEMA_VERSION = 1;
export const PLUGIN_DATA_DIR_NAME = "foundry-agent-starter-kit";

function copilotHome() {
    return process.env.COPILOT_HOME || join(homedir(), ".copilot");
}

function safeSegment(value) {
    return String(value || "default")
        .replace(/[^A-Za-z0-9._-]+/g, "-")
        .replace(/^-+|-+$/g, "") || "default";
}

export function pluginDataRoot({ root = null } = {}) {
    return root || join(copilotHome(), "plugin-data", PLUGIN_DATA_DIR_NAME);
}

export function pluginRuntimeRoot({ root = null } = {}) {
    return join(pluginDataRoot({ root }), "runtime");
}

export function sessionStoreRoot(sessionId, { root = null, instanceId = "default" } = {}) {
    if (root) return root;
    if (!sessionId) return null;
    return join(
        pluginDataRoot(),
        "sessions",
        safeSegment(sessionId),
        "foundry-agent-playground",
        safeSegment(instanceId),
    );
}

export function legacySessionStoreRoot(sessionId, { instanceId = "default" } = {}) {
    if (!sessionId) return null;
    return join(
        copilotHome(),
        "session-state",
        safeSegment(sessionId),
        "files",
        "foundry-agent-playground",
        safeSegment(instanceId),
    );
}

export function configureRuntimeStore(state, { sessionId, root = null, instanceId = "default" } = {}) {
    const storeRoot = sessionStoreRoot(sessionId, { root, instanceId });
    if (!storeRoot) {
        state.runtimeStore = null;
        return null;
    }
    const legacyRoot = root ? null : legacySessionStoreRoot(sessionId, { instanceId });
    state.runtimeStore = {
        root: storeRoot,
        statePath: join(storeRoot, "state.json"),
        operationsPath: join(storeRoot, "operations.jsonl"),
        legacyStatePath: legacyRoot ? join(legacyRoot, "state.json") : null,
        legacyOperationsPath: legacyRoot ? join(legacyRoot, "operations.jsonl") : null,
        queue: Promise.resolve(),
        writeCounter: 0,
    };
    return state.runtimeStore;
}

export async function persistStateSnapshot(state, snapshot) {
    const store = state?.runtimeStore;
    const path = store?.statePath;
    if (!path) return null;
    store.queue = (store.queue || Promise.resolve()).catch(() => {}).then(async () => {
        await mkdir(dirname(path), { recursive: true });
        store.writeCounter = (store.writeCounter || 0) + 1;
        const tempPath = `${path}.${process.pid}.${store.writeCounter}.tmp`;
        await writeFile(tempPath, `${JSON.stringify({
            schemaVersion: PERSISTED_STATE_SCHEMA_VERSION,
            ...snapshot,
        }, null, 2)}\n`, "utf8");
        await rename(tempPath, path);
        return path;
    });
    return store.queue;
}

export async function readStateSnapshot(stateOrStore) {
    const store = stateOrStore?.runtimeStore || stateOrStore;
    const path = store?.statePath;
    if (!path) return { snapshot: null, path: null, error: null };
    for (const candidate of [path, store?.legacyStatePath].filter(Boolean)) {
        try {
            const text = await readFile(candidate, "utf8");
            const snapshot = JSON.parse(text);
            if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
                return { snapshot: null, path: candidate, error: new Error("Persisted Playground state is not a JSON object.") };
            }
            return { snapshot, path: candidate, error: null };
        } catch (error) {
            if (error?.code === "ENOENT") {
                continue;
            }
            return { snapshot: null, path: candidate, error };
        }
    }
    return { snapshot: null, path, error: null };
}

export async function appendOperationRecord(state, record) {
    const path = state?.runtimeStore?.operationsPath;
    if (!path) return null;
    await mkdir(dirname(path), { recursive: true });
    await appendFile(path, `${JSON.stringify({ ...record, at: record.at || new Date().toISOString() })}\n`, "utf8");
    return path;
}

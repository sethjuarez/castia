import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const PERSISTED_STATE_SCHEMA_VERSION = 1;

function copilotHome() {
    return process.env.COPILOT_HOME || join(homedir(), ".copilot");
}

function safeSegment(value) {
    return String(value || "default")
        .replace(/[^A-Za-z0-9._-]+/g, "-")
        .replace(/^-+|-+$/g, "") || "default";
}

export function sessionStoreRoot(sessionId, { root = null, instanceId = "default" } = {}) {
    if (root) return root;
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
    state.runtimeStore = {
        root: storeRoot,
        statePath: join(storeRoot, "state.json"),
        operationsPath: join(storeRoot, "operations.jsonl"),
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
    try {
        const text = await readFile(path, "utf8");
        const snapshot = JSON.parse(text);
        if (!snapshot || typeof snapshot !== "object" || Array.isArray(snapshot)) {
            return { snapshot: null, path, error: new Error("Persisted Playground state is not a JSON object.") };
        }
        return { snapshot, path, error: null };
    } catch (error) {
        if (error?.code === "ENOENT") {
            return { snapshot: null, path, error: null };
        }
        return { snapshot: null, path, error };
    }
}

export async function appendOperationRecord(state, record) {
    const path = state?.runtimeStore?.operationsPath;
    if (!path) return null;
    await mkdir(dirname(path), { recursive: true });
    await appendFile(path, `${JSON.stringify({ ...record, at: record.at || new Date().toISOString() })}\n`, "utf8");
    return path;
}

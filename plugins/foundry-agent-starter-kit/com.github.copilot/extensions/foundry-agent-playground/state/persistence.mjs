import { appendFile, mkdir, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

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
        await writeFile(tempPath, `${JSON.stringify(snapshot, null, 2)}\n`, "utf8");
        await rename(tempPath, path);
        return path;
    });
    return store.queue;
}

export async function appendOperationRecord(state, record) {
    const path = state?.runtimeStore?.operationsPath;
    if (!path) return null;
    await mkdir(dirname(path), { recursive: true });
    await appendFile(path, `${JSON.stringify({ ...record, at: record.at || new Date().toISOString() })}\n`, "utf8");
    return path;
}

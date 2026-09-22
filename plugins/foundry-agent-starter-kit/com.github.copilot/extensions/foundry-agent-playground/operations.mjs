const MAX_OPERATION_HISTORY = 50;

export function emptyOperationState() {
    return {
        active: null,
        history: [],
    };
}

function ensureOperations(state) {
    state.operations ||= emptyOperationState();
    state.operations.history ||= [];
    return state.operations;
}

function operationId(kind) {
    return `op-${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function beginOperation(state, { kind, actor = "Canvas", phase = "starting", cancellable = true } = {}) {
    const operations = ensureOperations(state);
    if (operations.active?.status === "running" || operations.active?.status === "cancel_requested") {
        return { started: false, duplicate: true, operation: operations.active };
    }
    const operation = {
        id: operationId(kind || "operation"),
        kind: kind || "operation",
        actor,
        status: "running",
        phase,
        cancellable,
        irreversible: false,
        startedAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        completedAt: null,
        cancellation: null,
    };
    operations.active = operation;
    operations.history.push({ ...operation });
    if (operations.history.length > MAX_OPERATION_HISTORY) {
        operations.history.splice(0, operations.history.length - MAX_OPERATION_HISTORY);
    }
    return { started: true, duplicate: false, operation };
}

export function updateOperation(state, patch = {}) {
    const operations = ensureOperations(state);
    if (!operations.active) return null;
    Object.assign(operations.active, patch, { updatedAt: new Date().toISOString() });
    operations.history.push({ ...operations.active });
    if (operations.history.length > MAX_OPERATION_HISTORY) {
        operations.history.splice(0, operations.history.length - MAX_OPERATION_HISTORY);
    }
    return operations.active;
}

export function enterIrreversiblePhase(state, phase) {
    return updateOperation(state, { phase, irreversible: true });
}

export function requestOperationCancel(state, { operationId: requestedId = null } = {}) {
    const operations = ensureOperations(state);
    const operation = operations.active;
    if (!operation || !["running", "cancel_requested"].includes(operation.status)) {
        return { accepted: false, mode: "none", operation: null };
    }
    if (requestedId && operation.id !== requestedId) {
        return { accepted: false, mode: "operation_mismatch", operation };
    }
    const mode = operation.irreversible ? "wait_for_settle" : "stop_requested";
    updateOperation(state, {
        status: "cancel_requested",
        cancellation: {
            requestedAt: new Date().toISOString(),
            mode,
            message: mode === "wait_for_settle"
                ? "Remote Foundry work already started; waiting for the operation to settle."
                : "Cancellation requested before irreversible remote work.",
        },
    });
    return { accepted: true, mode, operation: operations.active };
}

export function completeOperation(state, { status = "completed", exitCode = null, summary = null } = {}) {
    const operations = ensureOperations(state);
    if (!operations.active) return null;
    const operation = updateOperation(state, {
        status,
        exitCode,
        summary,
        completedAt: new Date().toISOString(),
    });
    operations.active = null;
    return operation;
}

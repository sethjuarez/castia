import { requestOperationCancel } from "../domain/operations.mjs";
import { addActivity, selectedAgent, selectedLocalEndpoint } from "../state/snapshot.mjs";

export function setProjectEndpointPrompt(state, reason = "missing_configuration") {
    state.projectEndpointPrompt = {
        open: true,
        reason,
        selectedAgentId: state.selectedAgentId,
        requestedAt: new Date().toISOString(),
        message: "Foundry project endpoint is required before starting local.",
    };
    return state.projectEndpointPrompt;
}

export function clearProjectEndpointPrompt(state) {
    state.projectEndpointPrompt = null;
}

export function createPlaygroundCommands({
    selectAgent,
    refreshHostedContext,
    snapshotState,
    syncLocalBootstrapForStart,
    startLocalAgent,
    bootstrapLocalEnv,
    recordOperation,
}) {
    async function commandSelectAgent(state, agentId, { actor = "Canvas" } = {}) {
        const agent = await selectAgent(state, agentId);
        await refreshHostedContext(state);
        addActivity(state, {
            actor,
            kind: "select_agent",
            status: "completed",
            summary: `Selected ${agent.displayName || agent.serviceName}.`,
            details: { selectedAgent: agent },
        });
        return snapshotState(state);
    }

    async function commandStartLocal(state, { actor = "Canvas" } = {}) {
        state.target = "local";
        const sync = await syncLocalBootstrapForStart(state);
        if (!sync.ok) {
            const prompt = setProjectEndpointPrompt(state);
            addActivity(state, {
                actor,
                kind: "start_local",
                status: "blocked",
                summary: prompt.message,
                details: { prompt, sync },
            });
            return {
                ok: false,
                needsProjectEndpoint: true,
                projectEndpointPrompt: prompt,
                state: snapshotState(state),
            };
        }
        clearProjectEndpointPrompt(state);
        await startLocalAgent(state);
        state.target = "local";
        addActivity(state, {
            actor,
            kind: "start_local",
            status: "running",
            summary: "Local agent start requested.",
            details: { endpoint: selectedLocalEndpoint(state), selectedAgent: selectedAgent(state) },
        });
        return { ok: true, state: snapshotState(state) };
    }

    function commandCancelOperation(state, { operationId = null, actor = "Canvas" } = {}) {
        const result = requestOperationCancel(state, { operationId });
        if (result.accepted && result.mode === "stop_requested") {
            state.deployment?.abortController?.abort();
        }
        if (result.accepted) {
            addActivity(state, {
                actor,
                kind: "cancel_operation",
                status: result.mode === "wait_for_settle" ? "warn" : "running",
                summary: result.operation.cancellation?.message || "Cancellation requested.",
                details: { operation: result.operation, mode: result.mode },
            });
            recordOperation(state, "cancel_requested", { mode: result.mode });
        }
        return {
            ...result,
            state: snapshotState(state),
        };
    }

    async function commandBootstrapLocalEnv(state, input = {}, { actor = "Canvas" } = {}) {
        const result = await bootstrapLocalEnv(state, input);
        if (!input.dryRun) {
            clearProjectEndpointPrompt(state);
        }
        if (state.foundryConnection?.projectEndpoint) {
            await refreshHostedContext(state);
        }
        addActivity(state, {
            actor,
            kind: "configure_project_endpoint",
            status: "completed",
            summary: input.dryRun
                ? "Previewed project endpoint configuration targets."
                : "Configured project endpoint values for local startup.",
            details: {
                dryRun: Boolean(input.dryRun),
                targetCount: Array.isArray(result?.targets) ? result.targets.length : undefined,
                writtenCount: Array.isArray(result?.written) ? result.written.length : undefined,
                skippedCount: Array.isArray(result?.skipped) ? result.skipped.length : undefined,
            },
        });
        return { result, state: snapshotState(state) };
    }

    return {
        commandSelectAgent,
        commandStartLocal,
        commandCancelOperation,
        commandBootstrapLocalEnv,
    };
}

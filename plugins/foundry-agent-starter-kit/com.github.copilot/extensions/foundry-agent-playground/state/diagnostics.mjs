export function activityState(state) {
    const entries = state.activity || [];
    return {
        count: entries.length,
        latest: entries.at(-1) || null,
        entries,
    };
}

export function operationState(state) {
    return state.operations || { active: null, history: [] };
}

export function latestDiagnostics(state) {
    const diagnostics = [];
    if (state.projectEndpointPrompt?.open) {
        diagnostics.push({
            severity: "action",
            code: "project_endpoint_required",
            message: state.projectEndpointPrompt.message || "Foundry project endpoint is required.",
            nextAction: "Use start_local to open the in-canvas project endpoint dialog.",
        });
    }
    if (state.lastHealth && !state.lastHealth.ok) {
        diagnostics.push({
            severity: "warning",
            code: "readiness_failed",
            message: String(state.lastHealth.body || state.lastHealth.status || "Readiness check failed."),
            readinessStatus: state.lastHealth.readinessStatus || null,
            identity: state.lastHealth.identity || null,
            protocolSupport: state.lastHealth.protocolSupport || null,
            configurationStatus: state.lastHealth.configurationStatus || null,
        });
    }
    if (state.localRun?.identityMismatch) {
        diagnostics.push({
            severity: "warning",
            code: "identity_mismatch",
            message: state.localRun.identityMismatch.message,
            identity: state.localRun.identityMismatch,
        });
    }
    if (state.deployment?.needsProvision) {
        diagnostics.push({
            severity: "action",
            code: "provision_required",
            message: "Infrastructure has not been provisioned for deploy.",
            nextAction: "Run provision before deploy.",
        });
    }
    if (state.operations?.active?.status === "cancel_requested") {
        diagnostics.push({
            severity: "info",
            code: "operation_cancel_requested",
            message: state.operations.active.cancellation?.message || "Cancellation requested.",
            operation: state.operations.active,
        });
    }
    return {
        generatedAt: new Date().toISOString(),
        diagnostics,
    };
}

export function foundryState(state) {
    return {
        selectedAgentId: state.selectedAgentId,
        foundryConnection: state.foundryConnection,
        hosted: state.hosted,
        deployment: {
            ...state.deployment,
            process: undefined,
            abortController: undefined,
        },
        teams: state.teams,
    };
}

export function nextActions(state) {
    const actions = [];
    if (state.projectEndpointPrompt?.open) {
        actions.push({
            id: "enter_foundry_project_endpoint",
            label: "Enter Foundry project endpoint",
            command: "start_local",
            reason: "Local startup needs non-secret Foundry project values.",
        });
    } else if (state.target !== "hosted" && !state.localRun?.running && !state.lastHealth?.ok) {
        actions.push({
            id: "start_local",
            label: "Start local",
            command: "start_local",
            reason: "Local chat requires a ready local agent.",
        });
    }
    if (state.target !== "hosted" && state.lastHealth && !state.lastHealth.ok) {
        actions.push({
            id: "check_readiness",
            label: "Check readiness",
            command: "health_check",
            reason: "Readiness is currently failing.",
        });
    }
    if (state.hosted?.responsesEndpoint && state.target !== "hosted") {
        actions.push({
            id: "test_hosted",
            label: "Test Foundry hosted endpoint",
            command: "set_target",
            input: { target: "hosted" },
            reason: "A hosted Responses endpoint is available.",
        });
    }
    if (state.deployment?.needsProvision) {
        actions.push({
            id: "provision",
            label: "Prepare deploy",
            command: "provision",
            reason: "Deploy reported missing provisioned infrastructure.",
        });
    }
    return { actions };
}

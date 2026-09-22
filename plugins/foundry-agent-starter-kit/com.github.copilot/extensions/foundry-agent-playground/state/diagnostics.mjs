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

function projectResourceGroup(projectId) {
    const match = String(projectId || "").match(/\/resourceGroups\/([^/]+)/i);
    return match ? match[1] : null;
}

export function telemetryState(state) {
    const foundry = state.foundryConnection || {};
    const hosted = state.hosted || {};
    const subscriptionId = foundry.subscriptionId || null;
    const resourceGroup = projectResourceGroup(foundry.projectId);
    const accountName = foundry.accountName || null;
    const agentName = hosted.agentName || hosted.agentId || null;
    const agentVersion = hosted.version || null;
    const hasProjectContext = Boolean(subscriptionId && resourceGroup && accountName);
    const hasHostedContext = Boolean(agentName && hosted.responsesEndpoint);
    const discoveryCommand = hasProjectContext
        ? `az cognitiveservices account connection list --name ${accountName} --resource-group ${resourceGroup} --subscription ${subscriptionId} --output json`
        : null;
    return {
        status: hasProjectContext && hasHostedContext ? "ready_to_discover" : "missing_context",
        selectedAgentId: state.selectedAgentId || null,
        project: {
            endpoint: foundry.projectEndpoint || null,
            id: foundry.projectId || null,
            subscriptionId,
            resourceGroup,
            accountName,
            projectName: foundry.projectName || null,
        },
        hosted: {
            agentName,
            version: agentVersion,
            status: hosted.status || null,
            responsesEndpoint: hosted.responsesEndpoint || null,
        },
        observability: {
            connectionCategory: "AppInsights",
            connectionNameHint: "appinsights",
            discoveryCommand,
            expectedResourceMetadataKeys: [
                "ResourceId",
                "ApplicationInsightsConnectionString",
            ],
        },
        traceLookup: {
            traceIdSources: [
                "azd ai agent invoke Trace ID",
                "azure.ai.agentserver.x-request-id in Application Insights customDimensions",
            ],
            operationIdHint: "Find rows where customDimensions.azure.ai.agentserver.x-request-id matches the trace ID, then use that row's operation_Id for drill-down.",
            kqlTemplates: {
                findTrace: [
                    "union requests, dependencies, traces, exceptions, customEvents",
                    "| where timestamp > ago(2h)",
                    "| where tostring(customDimensions) contains \"<trace-id>\"",
                    "| project timestamp, itemType, name, operation_Id, success, resultCode, duration, customDimensions",
                    "| order by timestamp desc",
                ].join("\n"),
                summarizeOperation: [
                    "union requests, dependencies, traces, exceptions, customEvents",
                    "| where timestamp > ago(2h)",
                    "| where operation_Id == \"<operation-id>\"",
                    "| summarize count(), failed=countif(success == false) by itemType, name, resultCode",
                    "| order by itemType, count_ desc",
                ].join("\n"),
            },
        },
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
    if (state.hosted?.responsesEndpoint && state.foundryConnection?.projectEndpoint) {
        actions.push({
            id: "find_telemetry",
            label: "Find hosted telemetry",
            command: "get_telemetry_state",
            reason: "Hosted telemetry can be discovered from the Foundry project's App Insights connection.",
        });
    }
    return { actions };
}

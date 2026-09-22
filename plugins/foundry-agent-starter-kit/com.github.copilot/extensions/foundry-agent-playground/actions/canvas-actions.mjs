import { checkReadiness } from "../client/agent-client.mjs";
import {
    activeEndpoint,
    addActivity,
    clearMessagesForTarget,
    selectedAgent,
    setTarget,
    setTargetHealth,
    transcriptState,
    updateActivity,
} from "../state/snapshot.mjs";
import {
    activityState,
    foundryState,
    latestDiagnostics,
    nextActions,
    operationState,
} from "../state/diagnostics.mjs";

export function createCanvasActions({
    CanvasError,
    instanceState,
    commandSelectAgent,
    commandStartLocal,
    commandCancelOperation,
    commandBootstrapLocalEnv,
    setLocalEndpoint,
    readinessAgentNames,
    reconcileSelectedAgentFromReadiness,
    reconcileLocalReadinessAfterHealth,
    localStartupStillPending,
    responseTurn,
    completeResponseTurn,
    completedResponseResult,
    snapshotState,
    broadcastSnapshot,
}) {
    return [
        {
            name: "set_endpoint",
            description: "Set the agent endpoint used by this tester.",
            inputSchema: {
                type: "object",
                properties: { endpoint: { type: "string" } },
                required: ["endpoint"],
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                if (state.target === "hosted") {
                    state.hosted.responsesEndpoint = String(ctx.input?.endpoint || "").trim().replace(/\/+$/, "");
                } else {
                    setLocalEndpoint(state, ctx.input?.endpoint);
                }
                addActivity(state, {
                    actor: "Copilot",
                    kind: "set_endpoint",
                    status: "completed",
                    summary: `Endpoint set to ${activeEndpoint(state) || "not configured"}.`,
                    details: { target: state.target, endpoint: activeEndpoint(state) },
                });
                broadcastSnapshot(state);
                return snapshotState(state);
            },
        },
        {
            name: "select_agent",
            description: "Select a discovered agent and clear stale readiness and transcript state from the previous agent.",
            inputSchema: {
                type: "object",
                properties: { agentId: { type: "string" } },
                required: ["agentId"],
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                const snapshot = await commandSelectAgent(state, ctx.input?.agentId, { actor: "Copilot" });
                broadcastSnapshot(state);
                return snapshot;
            },
        },
        {
            name: "set_target",
            description: "Switch the playground target between local and hosted Foundry chat.",
            inputSchema: {
                type: "object",
                properties: {
                    target: { type: "string", enum: ["local", "hosted"] },
                },
                required: ["target"],
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                setTarget(state, ctx.input.target);
                addActivity(state, {
                    actor: "Copilot",
                    kind: "set_target",
                    status: "completed",
                    summary: `Switched to ${state.target} target.`,
                    details: { target: state.target, endpoint: activeEndpoint(state) },
                });
                broadcastSnapshot(state);
                return snapshotState(state);
            },
        },
        {
            name: "start_local",
            description: "Start the selected local agent, or open the in-canvas Foundry project endpoint prompt when configuration is missing.",
            handler: async (ctx) => {
                const state = instanceState(ctx);
                const result = await commandStartLocal(state, { actor: "Copilot" });
                broadcastSnapshot(state);
                return result;
            },
        },
        {
            name: "configure_project_endpoint",
            description: "Submit the already-open in-canvas Foundry project endpoint prompt and write non-secret values to gitignored .env files.",
            inputSchema: {
                type: "object",
                properties: {
                    projectEndpoint: {
                        type: "string",
                        description: "Foundry project endpoint ending in /api/projects/<project>.",
                    },
                    modelDeployment: {
                        type: "string",
                        description: "Optional model deployment name. Defaults to the starter kit default.",
                    },
                    toolboxName: {
                        type: "string",
                        description: "Optional toolbox name for derived MCP endpoint values.",
                    },
                    overwrite: {
                        type: "boolean",
                        description: "Overwrite existing non-empty .env values.",
                    },
                    dryRun: {
                        type: "boolean",
                        description: "Preview writable .env targets without writing files.",
                    },
                    targetPaths: {
                        type: "array",
                        items: { type: "string" },
                        description: "Optional workspace-relative .env target paths.",
                    },
                },
                required: ["projectEndpoint"],
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                if (!state.projectEndpointPrompt?.open) {
                    throw new CanvasError(
                        "project_endpoint_prompt_not_open",
                        "Use start_local first. Project endpoint configuration is only accepted while the in-canvas prompt is open.",
                    );
                }
                const result = await commandBootstrapLocalEnv(state, ctx.input || {}, { actor: "Copilot" });
                if (!ctx.input?.dryRun) {
                    const startLocal = await commandStartLocal(state, { actor: "Copilot" });
                    broadcastSnapshot(state);
                    return { result: result.result, startLocal, state: startLocal.state };
                }
                broadcastSnapshot(state);
                return result;
            },
        },
        {
            name: "health_check",
            description: "Call GET /readiness on the configured agent endpoint.",
            handler: async (ctx) => {
                const state = instanceState(ctx);
                const activity = addActivity(state, {
                    actor: "Copilot",
                    kind: "health_check",
                    status: "running",
                    summary: `Checking readiness for ${activeEndpoint(state) || "configured endpoint"}.`,
                    details: { target: state.target, endpoint: activeEndpoint(state) },
                });
                const health = {
                    ...(await checkReadiness(activeEndpoint(state), {
                        expectedAgentNames: state.target === "local" ? readinessAgentNames(selectedAgent(state)) : null,
                    })),
                    source: "copilot",
                };
                const stillStarting = localStartupStillPending(state, health);
                if (!stillStarting) {
                    state.lastHealth = setTargetHealth(state, health);
                }
                if (state.target === "local" && !stillStarting) {
                    reconcileSelectedAgentFromReadiness(state, state.lastHealth, { source: "copilot" });
                    reconcileLocalReadinessAfterHealth(state);
                }
                updateActivity(state, activity.id, {
                    status: stillStarting ? "running" : state.lastHealth.ok ? "completed" : "failed",
                    summary: stillStarting
                        ? `Local agent is still starting; readiness ${health.status} in ${health.durationMs}ms.`
                        : `Readiness ${state.lastHealth.status} in ${state.lastHealth.durationMs}ms.`,
                    details: { health, stillStarting },
                });
                broadcastSnapshot(state);
                return {
                    readiness: health,
                    transcript: transcriptState(state),
                    state: snapshotState(state),
                };
            },
        },
        {
            name: "send_response",
            description: "Send a prompt to POST /responses and append the result to the transcript.",
            inputSchema: {
                type: "object",
                properties: {
                    input: { type: "string" },
                    waitForFinal: {
                        type: "boolean",
                        description: "When false, append a visible pending turn and return immediately while completion continues in the background.",
                    },
                    appendVisible: {
                        type: "boolean",
                        description: "Append the prompt and answer to the visible transcript. Defaults to true.",
                    },
                    expectAgent: {
                        type: "string",
                        description: "Optional expected selected agent id or display/service name for diagnostics.",
                    },
                },
                required: ["input"],
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                const input = String(ctx.input?.input || "").trim();
                if (!input) {
                    throw new CanvasError("input_required", "Input is required.");
                }
                if (ctx.input?.appendVisible === false && ctx.input?.waitForFinal === false) {
                    throw new CanvasError("unsupported_options", "appendVisible=false cannot be combined with waitForFinal=false.");
                }
                if (state.target !== "hosted" && !state.lastHealth?.ok) {
                    throw new CanvasError("not_ready", "Start the local agent and wait for readiness before sending a prompt.");
                }
                const expected = String(ctx.input?.expectAgent || "").trim();
                const agent = selectedAgent(state);
                if (expected && ![agent.id, agent.serviceName, agent.displayName].includes(expected)) {
                    addActivity(state, {
                        actor: "Copilot",
                        kind: "send_response",
                        status: "warn",
                        summary: `Expected ${expected}, selected ${agent.displayName || agent.serviceName}.`,
                        details: { expected, selectedAgent: agent },
                    });
                }
                const activity = addActivity(state, {
                    actor: "Copilot",
                    kind: "send_response",
                    status: "running",
                    summary: "Prompt sent to the agent.",
                    details: { input, target: state.target, endpoint: activeEndpoint(state) },
                });
                const turn = responseTurn(state, input);
                if (ctx.input?.appendVisible !== false) {
                    state.messages.push(turn);
                }
                broadcastSnapshot(state);
                if (ctx.input?.waitForFinal === false) {
                    void completeResponseTurn(state, turn, activity.id).catch((error) => {
                        turn.response = {
                            ok: false,
                            status: 0,
                            durationMs: 0,
                            body: error instanceof Error ? error.message : String(error),
                            delivery: { mode: "error", upstreamStreaming: false, active: false },
                        };
                        updateActivity(state, activity.id, {
                            status: "failed",
                            summary: "Response failed.",
                            details: { error: turn.response.body },
                        });
                    }).finally(() => {
                        broadcastSnapshot(state);
                    });
                    return completedResponseResult(state, turn);
                }
                const result = await completeResponseTurn(state, turn, activity.id);
                broadcastSnapshot(state);
                return result;
            },
        },
        {
            name: "get_transcript_state",
            description: "Return the visible transcript, latest copy target, pending label, and health banner state.",
            handler: async (ctx) => {
                const state = instanceState(ctx);
                return transcriptState(state);
            },
        },
        {
            name: "get_activity_state",
            description: "Return compact activity details for recent UI and Copilot-driven operations.",
            handler: async (ctx) => activityState(instanceState(ctx)),
        },
        {
            name: "get_operation_state",
            description: "Return the active operation and recent operation history.",
            handler: async (ctx) => operationState(instanceState(ctx)),
        },
        {
            name: "get_latest_diagnostics",
            description: "Return deterministic readiness, identity, configuration, protocol, and operation diagnostics.",
            handler: async (ctx) => latestDiagnostics(instanceState(ctx)),
        },
        {
            name: "get_foundry_state",
            description: "Return Foundry connection, hosted agent, deployment, and Teams handoff state.",
            handler: async (ctx) => foundryState(instanceState(ctx)),
        },
        {
            name: "get_next_actions",
            description: "Return recommended next actions derived from current playground state.",
            handler: async (ctx) => nextActions(instanceState(ctx)),
        },
        {
            name: "cancel_operation",
            description: "Request phase-aware cancellation of the active deploy/provision operation.",
            inputSchema: {
                type: "object",
                properties: {
                    operationId: {
                        type: "string",
                        description: "Optional active operation id to guard cancellation against stale requests.",
                    },
                },
                additionalProperties: false,
            },
            handler: async (ctx) => {
                const state = instanceState(ctx);
                const result = commandCancelOperation(state, {
                    operationId: ctx.input?.operationId || null,
                    actor: "Copilot",
                });
                broadcastSnapshot(state);
                return result;
            },
        },
        {
            name: "clear_transcript",
            description: "Clear the tester transcript for this canvas instance.",
            handler: async (ctx) => {
                const state = instanceState(ctx);
                clearMessagesForTarget(state, state.target);
                addActivity(state, {
                    actor: "Copilot",
                    kind: "clear_transcript",
                    status: "completed",
                    summary: `Cleared ${state.target} transcript.`,
                    details: { target: state.target },
                });
                broadcastSnapshot(state);
                return snapshotState(state);
            },
        },
            
    ];
}

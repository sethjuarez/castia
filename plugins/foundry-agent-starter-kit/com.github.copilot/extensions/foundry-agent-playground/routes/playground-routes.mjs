import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { callAgentStream, checkReadiness } from "../client/agent-client.mjs";
import { activityEventFromConnector, applyConnectorEvent, findActivityTurnByConversation } from "../protocols/activity-protocol.mjs";
import { renderHtml } from "../renderer/renderer.mjs";
import {
    activityState,
    foundryState,
    latestDiagnostics,
    nextActions,
    operationState,
} from "../state/diagnostics.mjs";
import {
    activeEndpoint,
    activeProtocolEndpoint,
    addActivity,
    addLocalEvent,
    clearTargetHealth,
    clearMessagesForTarget,
    emptyFoundryConnection,
    responseDisplayText,
    selectedAgent,
    selectedLocalEndpoint,
    protocolState as snapshotProtocolState,
    setActiveProtocol as setStateActiveProtocol,
    setSelectedAgent,
    setTarget,
    setTargetHealth,
    updateActivity,
} from "../state/snapshot.mjs";
import { readBody, sendHtml, sendJson, sendNoContent, writeEvent } from "./http.mjs";

export function createRequestHandler({
    extensionRoot,
    snapshotState,
    openSnapshotStream,
    setLocalEndpoint,
    commandSelectAgent,
    connectFoundry,
    refreshHostedContext,
    clearProjectEndpointPrompt,
    commandBootstrapLocalEnv,
    hydrateFoundryConnection,
    readinessAgentNames,
    reconcileSelectedAgentFromReadiness,
    reconcileLocalReadinessAfterHealth,
    localStartupStillPending,
    commandStartLocal,
    stopLocalAgent,
    responseTurn,
    completeResponseTurn,
    commandCancelOperation,
    streamAzdLifecycle,
    setActiveProtocol = setStateActiveProtocol,
    protocolState = snapshotProtocolState,
    createActivityTurn,
    completeActivityTurn,
    invocationTurn,
    completeInvocationTurn,
    broadcastSnapshot = () => {},
}) {
    async function handleRequest(req, res, state) {
        try {
            const url = new URL(req.url || "/", "http://127.0.0.1");
            if (req.method === "GET" && url.pathname === "/") {
                sendHtml(res, renderHtml());
                return;
            }
            if (req.method === "GET" && url.pathname === "/favicon.ico") {
                sendNoContent(res);
                return;
            }
            if (req.method === "GET" && url.pathname === "/assets/icon-service-AI-Foundry.svg") {
                const svg = await readFile(join(extensionRoot, "assets", "icon-service-AI-Foundry.svg"), "utf8");
                res.writeHead(200, {
                    "Content-Type": "image/svg+xml; charset=utf-8",
                    "Cache-Control": "no-store",
                });
                res.end(svg);
                return;
            }
            if (req.method === "GET" && url.pathname === "/assets/icon-teams.svg") {
                const svg = await readFile(join(extensionRoot, "assets", "icon-teams.svg"), "utf8");
                res.writeHead(200, {
                    "Content-Type": "image/svg+xml; charset=utf-8",
                    "Cache-Control": "no-store",
                });
                res.end(svg);
                return;
            }
            if (req.method === "GET" && url.pathname === "/assets/icon-a365-agents.svg") {
                const svg = await readFile(join(extensionRoot, "assets", "icon-a365-agents.svg"), "utf8");
                res.writeHead(200, {
                    "Content-Type": "image/svg+xml; charset=utf-8",
                    "Cache-Control": "no-store",
                });
                res.end(svg);
                return;
            }
            if (req.method === "GET" && url.pathname === "/api/state") {
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "GET" && url.pathname === "/api/diagnostics") {
                sendJson(res, 200, {
                    activity: activityState(state),
                    operation: operationState(state),
                    diagnostics: latestDiagnostics(state),
                    foundry: foundryState(state),
                    nextActions: nextActions(state),
                });
                return;
            }
            if (req.method === "GET" && url.pathname === "/api/events") {
                openSnapshotStream(req, res, state);
                return;
            }
            if (url.pathname.startsWith("/connector/")) {
                const pathParts = url.pathname.split("/").filter(Boolean);
                const body = req.method === "DELETE" ? {} : await readBody(req);
                const event = activityEventFromConnector({ method: req.method, pathParts, body });
                if (!event) {
                    sendJson(res, 404, { error: "Connector route not found." });
                    return;
                }
                const turn = findActivityTurnByConversation(state, event.conversationId);
                if (!turn) {
                    sendJson(res, 404, { error: "No Activity turn is tracking this conversation." });
                    return;
                }
                applyConnectorEvent(turn, event);
                broadcastSnapshot(state);
                sendJson(res, 200, event.activityId ? { id: event.activityId } : {});
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/endpoint") {
                const body = await readBody(req);
                if (body.target === "hosted" || state.target === "hosted") {
                    state.hosted.responsesEndpoint = String(body.endpoint || "").trim().replace(/\/+$/, "");
                    state.hostedByAgent[state.selectedAgentId] = state.hosted;
                    setTarget(state, "hosted");
                } else {
                    setLocalEndpoint(state, body.endpoint);
                }
                addActivity(state, {
                    actor: "You",
                    kind: "set_endpoint",
                    status: "completed",
                    summary: `Endpoint set to ${activeEndpoint(state) || "not configured"}.`,
                    details: { target: state.target, endpoint: activeEndpoint(state) },
                });
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/agent") {
                const body = await readBody(req);
                sendJson(res, 200, await commandSelectAgent(state, body.agentId, { actor: "You" }));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/foundry/connect") {
                const body = await readBody(req);
                await connectFoundry(state, {
                    projectEndpoint: body.projectEndpoint,
                    modelDeployment: body.modelDeployment,
                });
                await refreshHostedContext(state);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/env/bootstrap") {
                const body = await readBody(req);
                sendJson(res, 200, await commandBootstrapLocalEnv(state, body));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/project-endpoint-prompt/clear") {
                clearProjectEndpointPrompt(state);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/target") {
                const body = await readBody(req);
                if (!["local", "hosted"].includes(body.target)) {
                    sendJson(res, 400, { error: "Target must be local or hosted." });
                    return;
                }
                setTarget(state, body.target);
                if (body.refresh && state.target === "hosted") {
                    await refreshHostedContext(state);
                }
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/protocol") {
                const body = await readBody(req);
                const previousProtocol = state.activeProtocol;
                const protocol = String(body.protocol || "").trim().toLowerCase();
                const capability = protocolState({ ...state, activeProtocol: protocol }).protocols[protocol];
                if (capability?.status !== "supported") {
                    state.activeProtocol = previousProtocol;
                    sendJson(res, 409, { error: capability?.reason || "Protocol is not supported.", capability, state: snapshotState(state) });
                    return;
                }
                setActiveProtocol(state, protocol);
                addActivity(state, {
                    actor: "You",
                    kind: "set_protocol",
                    status: "completed",
                    summary: `Switched to ${capability.label} protocol.`,
                    details: capability,
                });
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/hosted/refresh") {
                await refreshHostedContext(state);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/config/refresh") {
                state.foundryConnection = emptyFoundryConnection();
                await hydrateFoundryConnection(state);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/health") {
                const activity = addActivity(state, {
                    actor: "You",
                    kind: "health_check",
                    status: "running",
                    summary: `Checking readiness for ${activeEndpoint(state) || "configured endpoint"}.`,
                    details: { target: state.target, endpoint: activeEndpoint(state) },
                });
                const health = {
                    ...(await checkReadiness(activeEndpoint(state), {
                        expectedAgentNames: state.target === "local" ? readinessAgentNames(selectedAgent(state)) : null,
                        requiredProtocols: [],
                    })),
                    source: "manual",
                };
                const stillStarting = localStartupStillPending(state, health);
                if (!stillStarting) {
                    state.lastHealth = setTargetHealth(state, health);
                }
                if (state.target === "local" && !stillStarting) {
                    reconcileSelectedAgentFromReadiness(state, state.lastHealth, { source: "manual" });
                    reconcileLocalReadinessAfterHealth(state);
                }
                updateActivity(state, activity.id, {
                    status: stillStarting ? "running" : state.lastHealth.ok ? "completed" : "failed",
                    summary: stillStarting
                        ? `Local agent is still starting; readiness ${health.status} in ${health.durationMs}ms.`
                        : `Readiness ${state.lastHealth.status} in ${state.lastHealth.durationMs}ms.`,
                    details: { health, stillStarting },
                });
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/agent/switch-to-readiness") {
                const mismatch = state.localRun?.identityMismatch;
                if (!mismatch?.agentId) {
                    sendJson(res, 409, { error: "No discovered readiness agent is available to switch to." });
                    return;
                }
                const endpoint = state.localRun?.endpoint || selectedLocalEndpoint(state);
                setSelectedAgent(state, mismatch.agentId);
                setTarget(state, "local");
                state.localEndpoints[state.selectedAgentId] = endpoint;
                state.localRun = {
                    ...state.localRun,
                    agentId: state.selectedAgentId,
                    agentName: selectedAgent(state).serviceName,
                    endpoint,
                    identityMismatch: null,
                };
                clearTargetHealth(state, "local");
                state.messages.length = 0;
                addLocalEvent(state, "ok", `Selected ${selectedAgent(state).displayName || selectedAgent(state).serviceName} from readiness.`);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/local/start") {
                const result = await commandStartLocal(state, { actor: "You" });
                if (!result.ok) {
                    sendJson(res, 409, {
                        error: result.projectEndpointPrompt.message,
                        needsProjectEndpoint: true,
                        projectEndpointPrompt: result.projectEndpointPrompt,
                        state: result.state,
                    });
                    return;
                }
                sendJson(res, 200, result.state);
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/local/stop") {
                await stopLocalAgent(state);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/responses") {
                const body = await readBody(req);
                const input = String(body.input || "").trim();
                if (!input) {
                    sendJson(res, 400, { error: "Input is required." });
                    return;
                }
                if (state.target !== "hosted" && !state.lastHealth?.ok) {
                    sendJson(res, 409, { error: "Start the local agent and wait for readiness before sending a prompt." });
                    return;
                }
                const activity = addActivity(state, {
                    actor: "You",
                    kind: "send_response",
                    status: "running",
                    summary: "Prompt sent to the agent.",
                    details: { input, target: state.target, endpoint: activeEndpoint(state) },
                });
                const turn = responseTurn(state, input);
                state.messages.push(turn);
                await completeResponseTurn(state, turn, activity.id);
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/activity") {
                const body = await readBody(req);
                const input = String(body.input || body.raw?.text || "").trim();
                if (!input) {
                    sendJson(res, 400, { error: "Input is required." });
                    return;
                }
                const capability = protocolState(state).protocols.activity;
                if (capability.status !== "supported") {
                    sendJson(res, 409, { error: capability.reason || "Activity protocol is not supported.", capability });
                    return;
                }
                if (state.target !== "hosted" && !state.lastHealth?.ok) {
                    sendJson(res, 409, { error: "Start the local agent and wait for readiness before sending an Activity." });
                    return;
                }
                const activity = addActivity(state, {
                    actor: "You",
                    kind: "send_activity",
                    status: "running",
                    summary: "Activity sent to the agent.",
                    details: { input, target: state.target, endpoint: activeProtocolEndpoint(state, "activity") },
                });
                const turn = createActivityTurn(state, input, { raw: body.raw, endpoint: capability.endpoint });
                state.messages.push(turn);
                const result = await completeActivityTurn(state, turn, activity.id);
                sendJson(res, 200, result.state);
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/invocations") {
                const body = await readBody(req);
                const input = String(body.input || "").trim();
                if (!input) {
                    sendJson(res, 400, { error: "Input is required." });
                    return;
                }
                const capability = protocolState(state).protocols.invocations;
                if (capability.status !== "supported") {
                    sendJson(res, 409, { error: capability.reason || "Invocations protocol is not supported.", capability });
                    return;
                }
                if (state.target !== "hosted" && !state.lastHealth?.ok) {
                    sendJson(res, 409, { error: "Start the local agent and wait for readiness before sending an invocation." });
                    return;
                }
                const activity = addActivity(state, {
                    actor: "You",
                    kind: "send_invocation",
                    status: "running",
                    summary: "Invocation sent to the agent.",
                    details: { input, target: state.target, endpoint: activeProtocolEndpoint(state, "invocations") },
                });
                const turn = invocationTurn(state, input);
                state.messages.push(turn);
                const result = await completeInvocationTurn(state, turn, activity.id);
                sendJson(res, 200, result.state);
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/responses/stream") {
                const body = await readBody(req);
                const input = String(body.input || "").trim();
                if (!input) {
                    sendJson(res, 400, { error: "Input is required." });
                    return;
                }
                if (state.target !== "hosted" && !state.lastHealth?.ok) {
                    sendJson(res, 409, { error: "Start the local agent and wait for readiness before sending a prompt." });
                    return;
                }
                res.writeHead(200, {
                    "Content-Type": "text/event-stream; charset=utf-8",
                    "Cache-Control": "no-store",
                    Connection: "keep-alive",
                });
                const activity = addActivity(state, {
                    actor: "You",
                    kind: "send_response",
                    status: "running",
                    summary: "Prompt sent to the agent.",
                    details: { input, target: state.target, endpoint: activeEndpoint(state), stream: true },
                });
                const turn = responseTurn(state, input, { stream: true });
                turn.response.delivery.mode = "pending-stream";
                state.messages.push(turn);
                writeEvent(res, "snapshot", snapshotState(state));
                const result = await callAgentStream(activeEndpoint(state), { input }, (_delta, outputText, durationMs) => {
                    turn.response = {
                        ok: true,
                        status: "streaming",
                        durationMs,
                        body: { output_text: outputText },
                        streaming: true,
                        delivery: {
                            mode: "upstream-stream",
                            upstreamStreaming: true,
                            active: true,
                        },
                    };
                    writeEvent(res, "snapshot", snapshotState(state));
                });
                turn.response = result;
                updateActivity(state, activity.id, {
                    status: result.ok ? "completed" : "failed",
                    summary: result.ok ? `Response completed (${result.status}).` : `Response failed (${result.status || "error"}).`,
                    details: { displayText: responseDisplayText(result), status: result.status, durationMs: result.durationMs },
                });
                writeEvent(res, "snapshot", snapshotState(state));
                res.end();
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/clear") {
                clearMessagesForTarget(state, state.target);
                addActivity(state, {
                    actor: "You",
                    kind: "clear_transcript",
                    status: "completed",
                    summary: `Cleared ${state.target} transcript.`,
                    details: { target: state.target },
                });
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/deploy/clear") {
                state.deployment.log.length = 0;
                state.deployment.exitCode = null;
                state.deployment.needsProvision = false;
                sendJson(res, 200, snapshotState(state));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/operation/cancel") {
                const body = await readBody(req);
                sendJson(res, 200, commandCancelOperation(state, {
                    operationId: body.operationId || null,
                    actor: "You",
                }));
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/provision/stream") {
                await streamAzdLifecycle(res, state, {
                    commandName: "provision",
                    args: ["provision", "--no-prompt"],
                });
                return;
            }
            if (req.method === "POST" && url.pathname === "/api/deploy/stream") {
                await streamAzdLifecycle(res, state, {
                    commandName: "deploy",
                    args: ["deploy", selectedAgent(state).serviceName, "--no-prompt"],
                });
                return;
            }
            sendJson(res, 404, { error: "Not found." });
        } catch (error) {
            sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) });
        }
    }

    return handleRequest;
}

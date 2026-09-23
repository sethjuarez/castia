import {
    renderCodeBlock,
    renderInline,
    renderMarkdown,
    renderTable,
    splitTableRow,
    isTableSeparator,
} from "./markdown/markdown-renderer.mjs";
import {
    parseMermaidFlowchart,
    parseMermaidEdges,
    parseMermaidNode,
    renderMermaidExpandButton,
    renderMermaidFallbackSource,
    renderSafeMermaidFlowchartBlock,
    mermaidShapeLabel,
    wrapMermaidLabel,
} from "./diagrams/mermaid-safe-flowchart.mjs";
import {
    hydrateVendoredMermaidDiagrams,
    hasRawHtmlMermaidLabel,
    hasUnsafeSvgCss,
    initializeVendoredMermaidRuntime,
    mermaidSourceHash,
    mermaidSourceSupport,
    removeMermaidRenderArtifacts,
    renderVendoredMermaidBlock,
    sanitizeMermaidSvg,
    sanitizeSvgElement,
} from "./diagrams/mermaid-vendor-renderer.mjs";
import { renderMermaidBlock } from "./diagrams/mermaid-renderer.mjs";
import { renderJsonDocument, highlightJson } from "./json/json-renderer.mjs";
import { escapeHtml } from "./shared/html.mjs";

export {
    renderCodeBlock,
    renderInline,
    renderMarkdown,
    renderTable,
    splitTableRow,
    isTableSeparator,
    renderMermaidBlock,
    parseMermaidFlowchart,
    parseMermaidEdges,
    parseMermaidNode,
    renderMermaidExpandButton,
    renderMermaidFallbackSource,
    renderSafeMermaidFlowchartBlock,
    renderVendoredMermaidBlock,
    mermaidSourceSupport,
    hydrateVendoredMermaidDiagrams,
    hasRawHtmlMermaidLabel,
    hasUnsafeSvgCss,
    initializeVendoredMermaidRuntime,
    mermaidSourceHash,
    sanitizeMermaidSvg,
    sanitizeSvgElement,
    removeMermaidRenderArtifacts,
    mermaidShapeLabel,
    wrapMermaidLabel,
    renderJsonDocument,
    highlightJson,
    escapeHtml,
};

export function localReadinessState(state) {
    if (state?.target === "hosted") {
        return { ready: true, reason: null };
    }
    if (state?.lastHealth?.ok) {
        return { ready: true, reason: null };
    }
    if (isStartupReadinessPending(state)) {
        return { ready: false, reason: "Local agent is running; waiting for readiness." };
    }
    if (state?.lastHealth && !state.lastHealth.ok) {
        if (state.lastHealth.identity?.status === "mismatch") {
            return { ready: false, reason: "Selected agent does not match endpoint: " + String(state.lastHealth.body || "identity mismatch").slice(0, 160) };
        }
        if (state.lastHealth.identity?.status === "unknown") {
            return { ready: false, reason: "Endpoint identity is unknown for the selected agent." };
        }
        if (state.lastHealth.configurationStatus?.status === "missing") {
            return {
                ready: false,
                reason: "Missing required configuration: " + state.lastHealth.configurationStatus.missingRequired.join(", "),
            };
        }
        if (state.lastHealth.protocolSupport?.status === "missing") {
            return {
                ready: false,
                reason: "Missing required protocol support: " + state.lastHealth.protocolSupport.missing.join(", "),
            };
        }
        const status = state.lastHealth.status ? " (" + state.lastHealth.status + ")" : "";
        const detail = state.lastHealth.body ? ": " + String(state.lastHealth.body).slice(0, 160) : "";
        return { ready: false, reason: "Readiness check failed" + status + detail };
    }
    if (state?.localRun?.running) {
        return { ready: false, reason: "Local agent is running; waiting for readiness." };
    }
    if (state?.localRun?.exitCode !== null && state?.localRun?.exitCode !== undefined) {
        return { ready: false, reason: "Local agent stopped. Start local again before chatting." };
    }
    return { ready: false, reason: "Start local before chatting." };
}

export function localStartupText(localRun) {
    const failure = localRun?.failure;
    if (failure) {
        return failure.suggestion || failure.rootCause || "Local startup failed.";
    }
    const phase = localRun?.readiness?.phase;
    if (phase === "dependency_sync") {
        return "Syncing Python dependencies with uv; first run can take a minute.";
    }
    if (phase === "polling_readiness") {
        return "Polling /readiness until the local agent is usable.";
    }
    if (localRun?.running) return "Starting local agent.";
    return "";
}

export function localFailureDetail(localRun) {
    const failure = localRun?.failure;
    if (!failure) return "";
    return [
        failure.rootCause,
        failure.suggestion,
        failure.command ? "Command: " + failure.command : "",
        failure.cwd ? "cwd: " + failure.cwd : "",
        Number.isFinite(failure.exitCode) ? "exit code: " + failure.exitCode : "",
        failure.stderrTail ? "stderr: " + failure.stderrTail : "",
    ].filter(Boolean).join(" · ");
}

export function isStartupReadinessPending(state) {
    return Boolean(
        state?.localRun?.running &&
        state.localRun.readiness?.status === "starting" &&
        state?.lastHealth?.source !== "manual",
    );
}

export function shouldOpenProjectEndpointDialog(state) {
    return Boolean(state?.projectEndpointPrompt?.open);
}

export function responseDetailsKey(turn, index = 0) {
    if (turn?.createdAt) return "created:" + String(turn.createdAt) + ":" + String(turn.target || "local");
    const responseId = turn?.response?.id || turn?.response?.body?.id || turn?.id;
    if (responseId) return "response:" + String(responseId);
    return "index:" + String(index);
}

export function responseDetailsPanelId(detailsKey) {
    const normalized = String(detailsKey || "details")
        .trim()
        .replace(/[^A-Za-z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "");
    return "response-details-" + (normalized || "details");
}

export function composerGate(state, { activeView = "chat", inFlight = false } = {}) {
    if (activeView !== "chat") {
        return { canSend: false, inputDisabled: true, disabledReason: "Open chat to send a prompt." };
    }
    if (!state) {
        return { canSend: false, inputDisabled: true, disabledReason: "Loading playground state." };
    }
    const activeProtocol = state.activeProtocol || "responses";
    const capability = state.protocols?.[activeProtocol] || (state.target === "hosted" && activeProtocol === "responses" && state.hosted?.responsesEndpoint
        ? { label: "Responses", status: "supported", endpoint: state.hosted.responsesEndpoint }
        : null);
    if (capability && capability.status !== "supported") {
        return { canSend: false, inputDisabled: true, disabledReason: capability.reason || activeProtocol + " protocol is not supported." };
    }
    if (state.target === "hosted") {
        if (!capability?.endpoint) {
            const label = capability?.label || ({ responses: "Responses", activity: "Activity", invocations: "Invocations" }[activeProtocol] || activeProtocol);
            return {
                canSend: false,
                inputDisabled: true,
                disabledReason: "Discover or deploy a hosted " + label + " endpoint before chatting.",
            };
        }
    } else {
        const readiness = localReadinessState(state);
        if (!readiness.ready) {
            return { canSend: false, inputDisabled: true, disabledReason: readiness.reason };
        }
    }
    if (inFlight) {
        return { canSend: false, inputDisabled: false, disabledReason: "Waiting for the current response." };
    }
    return { canSend: true, inputDisabled: false, disabledReason: null };
}

export function responseText(body) {
    if (typeof body === "string") {
        const envelope = parseResponseEnvelopeString(body);
        return envelope ? responseText(envelope) : body;
    }
    const extracted = textFromResponseBody(body);
    if (extracted) return extracted;
    if (body && typeof body === "object") {
        if (typeof body.output === "string") return body.output;
        if (typeof body.error === "string") return body.error;
        if (typeof body.error?.message === "string") return body.error.message;
        if (typeof body.error?.code === "string") return body.error.code;
        if (typeof body.detail === "string") return body.detail;
        if (typeof body.message === "string") return body.message;
        return "";
    }
    return body ?? "";
}

export function responseDisplayState(response) {
    const answer = responseText(response?.body);
    const hasAnswer = String(answer || "").trim();
    const status = String(response?.status || "").toLowerCase();
    const waitingForFirstToken = !hasAnswer && Boolean(
        response?.streaming ||
        response?.delivery?.active ||
        status === "waiting" ||
        status === "streaming"
    );
    return { answer, hasAnswer, waitingForFirstToken };
}

export function copyableAnswerText(response) {
    if (!response?.ok || response?.streaming || responseDisplayState(response).waitingForFirstToken) return "";
    const body = response.body;
    if (typeof body === "string") {
        const text = body.trim();
        if (/^[{[]/.test(text) && !parseResponseEnvelopeString(text)) return "";
    }
    return String(responseText(body) || "").trim();
}

function parseResponseEnvelopeString(value) {
    const text = String(value || "").trim();
    if (!text || !/^[{[]/.test(text)) return null;
    try {
        const parsed = JSON.parse(text);
        return isResponseEnvelope(parsed) ? parsed : null;
    } catch {
        return null;
    }
}

function isResponseEnvelope(body, seen = new Set()) {
    if (!body || typeof body !== "object" || seen.has(body)) return false;
    seen.add(body);
    if (Object.prototype.hasOwnProperty.call(body, "output_text")) return true;
    if (Array.isArray(body.output)) return true;
    return isResponseEnvelope(body.response, seen) || isResponseEnvelope(body.body, seen);
}

function textOrEmpty(value) {
    return typeof value === "string" && value.trim() ? value : "";
}

function textFromResponseBody(body, seen = new Set()) {
    if (!body || typeof body !== "object" || seen.has(body)) return "";
    seen.add(body);
    const direct = textOrEmpty(body.output_text);
    if (direct) return direct;
    const output = textFromResponsesOutput(body.output);
    if (output) return output;
    return textFromResponseBody(body.response, seen) || textFromResponseBody(body.body, seen);
}

function textFromResponsesOutput(output) {
    if (!Array.isArray(output)) return "";
    const parts = [];
    for (const item of output) {
        if (!item || typeof item !== "object") continue;
        if (typeof item.text === "string") {
            parts.push(item.text);
        }
        const content = item.content;
        if (!Array.isArray(content)) continue;
        for (const part of content) {
            if (part && typeof part === "object" && typeof part.text === "string") {
                parts.push(part.text);
            }
        }
    }
    return parts.join("").trim();
}

export const rendererClientScript = `
    const isStartupReadinessPending = ${isStartupReadinessPending.toString()};
    const localReadinessState = ${localReadinessState.toString()};
    const localStartupText = ${localStartupText.toString()};
    const localFailureDetail = ${localFailureDetail.toString()};
    const responseDetailsKey = ${responseDetailsKey.toString()};
    const responseDetailsPanelId = ${responseDetailsPanelId.toString()};
    const composerGate = ${composerGate.toString()};
    const shouldOpenProjectEndpointDialog = ${shouldOpenProjectEndpointDialog.toString()};
    const responseText = ${responseText.toString()};
    const responseDisplayState = ${responseDisplayState.toString()};
    const copyableAnswerText = ${copyableAnswerText.toString()};
    const parseResponseEnvelopeString = ${parseResponseEnvelopeString.toString()};
    const isResponseEnvelope = ${isResponseEnvelope.toString()};
    const textOrEmpty = ${textOrEmpty.toString()};
    const textFromResponseBody = ${textFromResponseBody.toString()};
    const textFromResponsesOutput = ${textFromResponsesOutput.toString()};
    const MERMAID_MAX_SOURCE_CHARS = 6000;
    const MERMAID_MAX_LINES = 240;
    const MERMAID_SUPPORTED_FAMILIES = ["flowchart", "graph", "sequencediagram", "statediagram", "statediagram-v2", "classdiagram", "erdiagram"];
    const codeBlockRenderers = [
      {
        id: "mermaid",
        canRender: ({ language }) => String(language || "").split(/\\s+/)[0] === "mermaid",
        render: ({ value, complete }) => renderMermaidBlock(value, { complete }),
      },
      {
        id: "json",
        canRender: ({ value, language }) => Boolean(renderJsonDocument(value, language)),
        render: ({ value, language }) => renderJsonDocument(value, language),
      },
    ];
    ${renderMarkdown.toString()}
    ${renderCodeBlock.toString()}
    ${renderMermaidBlock.toString()}
    ${renderVendoredMermaidBlock.toString()}
    ${renderMermaidExpandButton.toString()}
    ${mermaidSourceSupport.toString()}
    ${hasRawHtmlMermaidLabel.toString()}
    ${hydrateVendoredMermaidDiagrams.toString()}
    ${initializeVendoredMermaidRuntime.toString()}
    ${mermaidSourceHash.toString()}
    ${removeMermaidRenderArtifacts.toString()}
    ${sanitizeMermaidSvg.toString()}
    ${sanitizeSvgElement.toString()}
    ${hasUnsafeSvgCss.toString()}
    ${renderMermaidFallbackSource.toString()}
    ${renderSafeMermaidFlowchartBlock.toString()}
    ${parseMermaidFlowchart.toString()}
    ${parseMermaidEdges.toString()}
    ${parseMermaidNode.toString()}
    ${mermaidShapeLabel.toString()}
    ${wrapMermaidLabel.toString()}
    ${renderJsonDocument.toString()}
    ${highlightJson.toString()}
    ${renderTable.toString()}
    ${splitTableRow.toString()}
    ${isTableSeparator.toString()}
    ${renderInline.toString()}
    ${escapeHtml.toString()}
    const agentPickerButton = document.getElementById("agentPickerButton");
    const agentPickerLabel = document.getElementById("agentPickerLabel");
    const agentMenu = document.getElementById("agentMenu");
    const localStep = document.getElementById("localStep");
    const foundryStep = document.getElementById("foundryStep");
    const teamsStep = document.getElementById("teamsStep");
    const localStepText = document.getElementById("localStepText");
    const foundryStepText = document.getElementById("foundryStepText");
    const teamsStepText = document.getElementById("teamsStepText");
    const localStepState = document.getElementById("localStepState");
    const foundryStepState = document.getElementById("foundryStepState");
    const teamsStepState = document.getElementById("teamsStepState");
    const guideTitle = document.getElementById("guideTitle");
    const guideCopy = document.getElementById("guideCopy");
    const actionStateChip = document.getElementById("actionStateChip");
    const localEndpointBanner = document.getElementById("localEndpointBanner");
    const localTicker = document.getElementById("localTicker");
    const deployTicker = document.getElementById("deployTicker");
    const primaryGuideAction = document.getElementById("primaryGuideAction");
    const testHostedAction = document.getElementById("testHostedAction");
    const advancedToggle = document.getElementById("advancedToggle");
    const chatView = document.getElementById("chatView");
    const deployView = document.getElementById("deployView");
    const teamsView = document.getElementById("teamsView");
    const foundryStatus = document.getElementById("foundryStatus");
    const foundryStatusTitle = document.getElementById("foundryStatusTitle");
    const foundryStatusCopy = document.getElementById("foundryStatusCopy");
    const foundryStatusBadge = document.getElementById("foundryStatusBadge");
    const foundryStatusAgent = document.getElementById("foundryStatusAgent");
    const foundryStatusRelease = document.getElementById("foundryStatusRelease");
    const foundryStatusState = document.getElementById("foundryStatusState");
    const foundryStatusEndpoint = document.getElementById("foundryStatusEndpoint");
    const foundryTarget = document.getElementById("foundryTarget");
    const hostedAgent = document.getElementById("hostedAgent");
    const hostedVersion = document.getElementById("hostedVersion");
    const deployLog = document.getElementById("deployLog");
    const stopLocalAction = document.getElementById("stopLocalAction");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const turnCount = document.getElementById("turnCount");
    const passCount = document.getElementById("passCount");
    const failCount = document.getElementById("failCount");
    const avgLatency = document.getElementById("avgLatency");
    const protocolToggle = document.getElementById("protocolToggle");
    const activityLog = document.getElementById("activityLog");
    const activitySummary = document.getElementById("activitySummary");
    const activityCount = document.getElementById("activityCount");
    const activityItems = document.getElementById("activityItems");
    const transcript = document.getElementById("transcript");
    const promptInput = document.getElementById("prompt");
    const sendButton = document.getElementById("send");
    const provisionButton = document.getElementById("provisionButton");
    const deployButton = document.getElementById("deployButton");
    const cancelOperationButton = document.getElementById("cancelOperationButton");
    const clearButton = document.getElementById("clear");
    const projectEndpointDialog = document.getElementById("projectEndpointDialog");
    const projectEndpointForm = document.getElementById("projectEndpointForm");
    const projectEndpointDialogInput = document.getElementById("projectEndpointDialogInput");
    const projectEndpointCancel = document.getElementById("projectEndpointCancel");
    const projectEndpointSubmit = document.getElementById("projectEndpointSubmit");
    let inFlight = false;
    let lastSend = { input: "", at: 0 };
    let activeView = "chat";
    let latestState = null;
    let agentMenuOpen = false;
    let foundryPanelOpen = false;
    let localRefreshTimer = null;
    let hostedWaitTimer = null;
    let deployRefreshTimer = null;
    const expandedResponseDetails = new Set();
    const responseDetailsScroll = new Map();

    function setStatus(kind, text) {
      statusDot.className = "dot " + (kind || "");
      statusText.textContent = text;
    }

    function clearHostedWaitTimer() {
      if (hostedWaitTimer) {
        clearInterval(hostedWaitTimer);
        hostedWaitTimer = null;
      }
    }

    function setHostedWaitingStatus(startedAt) {
      const seconds = Math.max(0, Math.round((Date.now() - startedAt) / 1000));
      setStatus("warn", "Waiting on Foundry hosted agent... " + seconds + "s");
    }

    function renderSnapshot(state) {
      latestState = state;
      renderAgentPicker(state);
      turnCount.textContent = state.stats.total + " turns";
      passCount.textContent = state.stats.completed + " pass";
      failCount.textContent = state.stats.failed + " fail";
      avgLatency.textContent = state.stats.averageMs + "ms avg";
      renderProtocolToggle(state);
      renderMessages(state.visibleMessages || []);
      if (state.target !== "hosted" && state.lastHealth && !isStartupReadinessPending(state)) {
        const detail = !state.lastHealth.ok && state.lastHealth.body ? " · " + String(state.lastHealth.body).slice(0, 160) : "";
        setStatus(state.lastHealth.ok ? "ok" : "fail", "Readiness " + state.lastHealth.status + " in " + state.lastHealth.durationMs + "ms" + detail);
      } else if (activeView === "chat" && state.target !== "hosted" && state.localRun?.running) {
        setStatus("", localStartupText(state.localRun) || "Local agent is running; waiting for readiness.");
      } else if (activeView === "chat" && state.target !== "hosted" && state.localRun?.exitCode !== null && state.localRun?.exitCode !== undefined) {
        setStatus(state.localRun.exitCode === 0 ? "" : "fail", state.localRun.exitCode === 0 ? "Local agent stopped." : localStartupText(state.localRun) || "Local agent exited with code " + state.localRun.exitCode + ".");
      }
      renderFoundryStatus(state);
      renderDeploy(state);
      renderActionStateChip(state);
      renderLocalEndpointBanner(state);
      renderLocalTicker(state);
      renderActivity(state);
      renderDeployTicker(state);
      renderJourney(state);
      renderView();
      if (shouldOpenProjectEndpointDialog(state) && projectEndpointDialog.hidden) {
        showProjectEndpointDialog();
      } else if (!shouldOpenProjectEndpointDialog(state) && !projectEndpointDialog.hidden) {
        hideProjectEndpointDialog();
      }
    }

    function actionState(state) {
      if (activeView === "deploy") {
        if (state.deployment?.running) return { kind: "warn", label: "Deploying", detail: "Deploy operation is running." };
        if (state.deployment?.needsProvision) return { kind: "warn", label: "Prepare deploy", detail: "Provisioning is needed before hosted deployment." };
        if (state.hosted?.responsesEndpoint) return { kind: "ok", label: state.hosted.version ? "v" + state.hosted.version : "Hosted ready", detail: "Hosted Responses endpoint is available." };
        return { kind: "", label: "Not deployed", detail: "No hosted release has been discovered for this agent." };
      }
      if (activeView === "teams") {
        return { kind: "", label: "Info only", detail: "Teams publish and hire are manual external steps." };
      }
      if (state.target === "hosted") {
        if (state.hosted?.responsesEndpoint) return { kind: "ok", label: state.hosted.version ? "v" + state.hosted.version : "Hosted ready", detail: "Hosted Responses endpoint is selected." };
        return { kind: "warn", label: "Hosted missing", detail: "Discover or deploy a hosted Responses endpoint before chatting." };
      }
      if (state.localRun?.running && state.lastHealth?.ok) return { kind: "ok", label: "Local ready", detail: state.lastHealth.body || "Local readiness passed." };
      if (state.localRun?.running) return { kind: "warn", label: state.localRun?.readiness?.phase === "dependency_sync" ? "Installing" : "Starting", detail: localStartupText(state.localRun) || "Local agent is running; waiting for readiness." };
      if (state.lastHealth?.ok) return { kind: "ok", label: "Local ready", detail: state.lastHealth.body || "Local readiness passed." };
      if (state.lastHealth && !state.lastHealth.ok) return { kind: "fail", label: "Needs attention", detail: state.lastHealth.body || "Readiness check failed." };
      if (state.localRun?.exitCode !== null && state.localRun?.exitCode !== undefined) {
        return {
          kind: state.localRun.exitCode === 0 ? "" : "fail",
          label: state.localRun.exitCode === 0 ? "Stopped" : "Start failed",
          detail: state.localRun.exitCode === 0 ? "Local agent stopped." : localFailureDetail(state.localRun) || "Local agent exited with code " + state.localRun.exitCode + ".",
        };
      }
      return { kind: "", label: "Not checked", detail: "Start local or run a readiness check." };
    }

    function renderActionStateChip(state) {
      const current = actionState(state || {});
      actionStateChip.className = "action-state-chip " + (current.kind || "");
      actionStateChip.textContent = current.label;
      actionStateChip.title = current.detail || current.label;
      actionStateChip.setAttribute("aria-label", "Current state: " + current.label + (current.detail ? ". " + current.detail : ""));
    }

    function renderLocalEndpointBanner(state) {
      if (state.target === "hosted") {
        localEndpointBanner.hidden = true;
        localEndpointBanner.innerHTML = "";
        localEndpointBanner.removeAttribute("data-detail");
        localEndpointBanner.removeAttribute("tabindex");
        return;
      }
      const endpoint = state.activeEndpoint || state.localEndpoint || "";
      const selected = state.selectedAgent?.displayName || state.selectedAgent?.serviceName || "unknown";
      const readiness = state.lastHealth?.identity?.actual || state.lastHealth?.readiness?.agent?.name || "unknown";
      const port = state.activePort || "";
      const portWarning = state.localRun?.portWarning;
      const mismatch = state.localRun?.identityMismatch;
      const needsDiagnostic = Boolean(portWarning || (mismatch && !mismatch.autoSelected));
      if (!needsDiagnostic) {
        localEndpointBanner.hidden = true;
        localEndpointBanner.innerHTML = "";
        localEndpointBanner.removeAttribute("data-detail");
        localEndpointBanner.removeAttribute("tabindex");
        return;
      }
      const details = "Endpoint: " + endpoint + " · port=" + String(port || "unknown") +
        " · selected=" + selected + " · readiness=" + readiness;
      localEndpointBanner.hidden = false;
      localEndpointBanner.className = "local-endpoint-banner warn";
      localEndpointBanner.setAttribute("data-detail", details + (portWarning ? " · " + portWarning : "") + (mismatch?.message ? " · " + mismatch.message : ""));
      localEndpointBanner.setAttribute("tabindex", "0");
      localEndpointBanner.innerHTML =
        '<div class="endpoint-compact">' +
          '<span class="endpoint-label">Local</span>' +
          '<code>' + escapeHtml(formatEndpointShort(endpoint)) + '</code>' +
          '<span class="endpoint-meta">' + escapeHtml(readiness) + '</span>' +
        '</div>' +
        (portWarning ? '<div class="endpoint-warning"><span class="endpoint-warning-text">Fallback ' + escapeHtml(String(port || "unknown")) + '</span></div>' : "") +
        (mismatch && !mismatch.autoSelected
          ? '<div class="endpoint-warning"><span class="endpoint-warning-text">' + escapeHtml(mismatch.message || "Selected agent does not match readiness.") + '</span>' +
            (mismatch.canSwitch ? ' <button id="switchReadinessAgent" type="button">Switch to ' + escapeHtml(mismatch.actual) + '</button>' : "") +
            '</div>'
          : "");
      const switchButton = document.getElementById("switchReadinessAgent");
      if (switchButton) {
        switchButton.addEventListener("click", async () => {
          try {
            renderSnapshot(await request("/api/agent/switch-to-readiness", { method: "POST" }));
            setStatus("ok", "Selected agent updated from readiness.");
          } catch (error) {
            setStatus("fail", error.message);
          }
        });
      }
    }

    function renderLocalTicker(state) {
      const events = state.target === "hosted" ? [] : (state.localRun?.events || []);
      localTicker.hidden = !events.length || activeView !== "chat";
      if (!events.length || activeView !== "chat") {
        localTicker.innerHTML = "";
        localTicker.className = "local-ticker";
        localTicker.removeAttribute("data-detail");
        localTicker.removeAttribute("tabindex");
        return;
      }
      const event = events.at(-1);
      const kind = event.kind || "";
      localTicker.className = "local-ticker " + kind;
      localTicker.setAttribute("data-detail", [events.map((entry) => entry.text).filter(Boolean).join(" · "), localFailureDetail(state.localRun)].filter(Boolean).join(" · "));
      localTicker.setAttribute("tabindex", "0");
      localTicker.innerHTML = '<span class="ticker-mark" aria-hidden="true"></span>' +
        '<span class="ticker-text">' + escapeHtml(event.text) + '</span>';
    }

    function renderActivity(state) {
      const entries = (state.activity || []).slice(-8).reverse();
      activityLog.hidden = !entries.length || activeView !== "chat";
      if (!entries.length || activeView !== "chat") {
        activitySummary.textContent = "No canvas actions yet.";
        activityCount.textContent = "0";
        activityItems.innerHTML = "";
        return;
      }
      const latest = entries[0];
      activitySummary.textContent = (latest.actor || "Canvas") + " · " + (latest.summary || latest.kind || "Canvas action");
      activityCount.textContent = String(state.activity.length);
      activityItems.innerHTML = entries.map((entry) => {
        const status = entry.status || "info";
        return '<div class="activity-item ' + escapeHtml(status) + '">' +
          '<span class="ticker-mark" aria-hidden="true"></span>' +
          '<div class="activity-main"><span class="activity-actor">' + escapeHtml(entry.actor || "Canvas") + '</span> ' +
          escapeHtml(entry.summary || entry.kind || "Canvas action") + '</div>' +
          '<time class="activity-time" datetime="' + escapeHtml(entry.at || "") + '">' + escapeHtml(formatTime(entry.at)) + '</time>' +
          '</div>';
      }).join("");
    }

    function elapsedLabel(startedAt) {
      const start = startedAt ? new Date(startedAt).getTime() : NaN;
      if (!Number.isFinite(start)) return "00:00";
      const seconds = Math.max(0, Math.round((Date.now() - start) / 1000));
      const minutes = Math.floor(seconds / 60);
      return String(minutes).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
    }

    function latestDeployPhase(log) {
      const ansiPattern = new RegExp(String.fromCharCode(27) + "\\\\[[0-9;?]*[ -/]*[@-~]", "g");
      const lineBreakPattern = new RegExp("\\\\r\\\\n|\\\\r|\\\\n");
      const text = (log || []).join("").slice(-8192).replace(ansiPattern, "");
      const lines = text.split(lineBreakPattern).map((line) => line.trim()).filter(Boolean);
      const line = [...lines].reverse().find((entry) =>
        /Deploying|Provisioning|Packaging|Uploading|Registering|Polling|Ensuring|Preparing|Checking|Updating|Waiting|Resolving|Done/i.test(entry)
      );
      if (!line) return "Starting deployment workflow";
      const phaseStart = line.indexOf("(");
      const phaseEnd = line.lastIndexOf(") [");
      if (phaseStart >= 0 && phaseEnd > phaseStart) return line.slice(phaseStart + 1, phaseEnd);
      const withoutPrefix = line.includes(": ") ? line.slice(line.indexOf(": ") + 2) : line;
      const bracketIndex = withoutPrefix.lastIndexOf(" [");
      return bracketIndex >= 0 ? withoutPrefix.slice(0, bracketIndex) : withoutPrefix;
    }

    function renderDeployTicker(state) {
      if (!state.deployment?.running) {
        if (deployRefreshTimer) {
          clearInterval(deployRefreshTimer);
          deployRefreshTimer = null;
        }
        deployTicker.hidden = true;
        deployTicker.innerHTML = "";
        deployTicker.removeAttribute("data-detail");
        deployTicker.removeAttribute("tabindex");
        return;
      }
      deployTicker.hidden = false;
      deployTicker.setAttribute("data-detail", latestDeployPhase(state.deployment.log));
      deployTicker.setAttribute("tabindex", "0");
      deployTicker.innerHTML =
        '<div class="deploy-ticker-row"><span class="deploy-label">Deploying</span><span class="deploy-elapsed" aria-label="Elapsed deployment time">' +
        escapeHtml(elapsedLabel(state.deployment.startedAt)) +
        '</span><span class="ticker-mark" aria-hidden="true"></span><span class="ticker-text deploy-phase">' +
        escapeHtml(latestDeployPhase(state.deployment.log)) +
        '</span></div>';
      if (!deployRefreshTimer) {
        deployRefreshTimer = setInterval(() => {
          if (latestState?.deployment?.running) renderDeployTicker(latestState);
        }, 1000);
      }
    }

    function scheduleLocalRefresh() {
      if (localRefreshTimer) window.clearTimeout(localRefreshTimer);
      const tick = async () => {
        try {
          const state = await request("/api/state");
          renderSnapshot(state);
          if (state.localRun?.running) {
            localRefreshTimer = window.setTimeout(tick, state.lastHealth?.ok ? 2500 : 1000);
          }
        } catch (error) {
          setStatus("fail", error.message);
        }
      };
      localRefreshTimer = window.setTimeout(tick, 800);
    }

    function setActiveStep(step) {
      localStep.classList.toggle("active", step === "local");
      foundryStep.classList.toggle("active", step === "foundry");
      teamsStep.classList.toggle("active", step === "teams");
    }

    function renderJourney(state) {
      const localOk = state.lastHealth?.ok || state.messages?.some((message) => message.target !== "hosted" && message.response?.ok);
      const localRunning = Boolean(state.localRun?.running);
      const connected = Boolean(state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment);
      const foundryOk = Boolean(state.hosted?.version || state.hosted?.responsesEndpoint);
      const versionLabel = state.hosted?.version ? "v" + state.hosted.version : "";
      const showingFoundryContext = activeView === "chat" && (foundryPanelOpen || state.target === "hosted");
      localStep.classList.toggle("done", localOk);
      foundryStep.classList.toggle("done", foundryOk);
      teamsStep.classList.remove("done");
      localStepText.textContent = localRunning && !localOk ? "Starting" : localOk ? "Answered" : "Run it";
      foundryStepText.textContent = foundryOk ? "Hosted" : connected ? "Not deployed" : "Needs project";
      teamsStepText.textContent = "Manual steps";
      localStepState.textContent = localOk ? "Done" : "Start";
      foundryStepState.textContent = versionLabel || (foundryOk ? "Ready" : localOk ? "Next" : "Later");
      teamsStepState.textContent = "Info";
      if (activeView === "deploy") {
        const needsProvision = Boolean(state.deployment?.needsProvision);
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = !(connected && foundryOk);
        guideTitle.textContent = !connected ? "Connect Foundry project" : "Deploy";
        guideCopy.textContent = !connected
          ? "Use Start local to open the Foundry project endpoint dialog and bootstrap .env, then refresh discovery here."
          : needsProvision
          ? "Prepare this repo for hosted deployment into the connected Foundry project."
          : foundryOk
          ? "Current version: " + (state.hosted.version || "ready") + ". Deploy changes when local updates are ready."
          : "Deploy the selected agent, then use the same transcript against the hosted target.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : needsProvision ? "Prepare" : "Deploy";
        setActiveStep("foundry");
      } else if (activeView === "teams") {
        primaryGuideAction.hidden = true;
        testHostedAction.hidden = true;
        guideTitle.textContent = "Teams handoff";
        guideCopy.textContent = "Informational only: publish in Foundry, approve in Microsoft 365, hire in Teams, then manually run the same smoke prompt.";
        primaryGuideAction.textContent = "";
        setActiveStep("teams");
      } else if (showingFoundryContext) {
        const needsProvision = Boolean(state.deployment?.needsProvision);
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = !(connected && foundryOk && state.target !== "hosted");
        guideTitle.textContent = !connected ? "Connect Foundry project" : "Foundry Agent";
        guideCopy.textContent = !connected
          ? "Project endpoint needed before hosted chat."
          : needsProvision
          ? "Prepare deploy, then return here to test hosted."
          : foundryOk
          ? "Hosted " + (state.hosted?.version ? "v" + state.hosted.version : "agent") + " ready. Send a prompt below."
          : "No hosted release found. Deploy the first version when local is ready.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : needsProvision ? "Prepare" : "Deploy";
        setActiveStep("foundry");
      } else {
        primaryGuideAction.hidden = state.target !== "hosted" && localRunning;
        testHostedAction.hidden = true;
        guideTitle.textContent = state.target === "hosted" && !connected ? "Connect Foundry project" : state.target === "hosted" ? "Foundry Agent" : "Local agent";
        guideCopy.textContent = state.target === "hosted" && !connected
          ? "Use Start local to open the Foundry project endpoint dialog and bootstrap .env, then refresh discovery here."
          : state.target === "hosted"
          ? "Current version: " + (state.hosted?.version || "ready") + ". Send a prompt here, or switch to deploy."
          : localRunning
          ? (localStartupText(state.localRun) || "Local agent is running.")
          : "Start the local agent. If Foundry values are missing, this opens the project endpoint dialog.";
        primaryGuideAction.textContent = state.target === "hosted" && !connected ? "Refresh .env" : state.target === "hosted" ? "Deploy" : "Start local";
        setActiveStep(state.target === "hosted" ? "foundry" : "local");
      }
      advancedToggle.hidden = true;
      advancedToggle.textContent = "Refresh .env";
      const guideDetail = guideCopy.textContent || "";
      const guideLabel = (guideTitle.textContent || "Guidance") + (guideDetail ? ". " + guideDetail : "");
      guideCopy.parentElement?.setAttribute("aria-label", guideLabel);
    }

    function renderAgentPicker(state) {
      const selected = state.selectedAgent || (state.agents || [])[0];
      agentPickerLabel.textContent = selected
        ? (selected.displayName || selected.serviceName) + " · " + selected.rootLabel
        : "No hosted agents";
      agentMenu.innerHTML = (state.agents || []).map((agent) => {
        const active = agent.id === state.selectedAgentId;
        return '<button type="button" role="option" class="agent-option ' + (active ? "active" : "") + '" aria-selected="' + String(active) + '" data-agent-id="' + escapeHtml(agent.id) + '">' +
          '<span>' + escapeHtml(agent.displayName || agent.serviceName) + '</span>' +
          '<span class="agent-option-folder">' + escapeHtml(agent.rootLabel) + '</span>' +
          '</button>';
      }).join("");
      agentMenu.hidden = !agentMenuOpen;
      agentPickerButton.setAttribute("aria-expanded", String(agentMenuOpen));
    }

    function closeAgentMenu() {
      agentMenuOpen = false;
      agentMenu.hidden = true;
      agentPickerButton.setAttribute("aria-expanded", "false");
    }

    async function selectAgent(agentId) {
      const state = await request("/api/agent", {
        method: "POST",
        body: JSON.stringify({ agentId }),
      });
      closeAgentMenu();
      renderSnapshot(state);
      setStatus("", "Selected " + (state.selectedAgent?.displayName || state.selectedAgent?.serviceName || "agent") + ".");
    }

    function renderView() {
      const gate = composerGate(latestState, { activeView, inFlight });
      const deploymentRunning = Boolean(latestState?.deployment?.running);
      const cancellableOperation = Boolean(
        latestState?.operations?.active?.cancellable &&
        ["running", "cancel_requested"].includes(latestState.operations.active.status),
      );
      chatView.hidden = activeView !== "chat";
      deployView.hidden = activeView !== "deploy";
      teamsView.hidden = activeView !== "teams";
      sendButton.hidden = activeView !== "chat";
      sendButton.disabled = !gate.canSend;
      provisionButton.hidden = true;
      deployButton.hidden = true;
      cancelOperationButton.hidden = !(activeView === "deploy" && deploymentRunning && cancellableOperation);
      cancelOperationButton.disabled = !cancellableOperation || latestState?.operations?.active?.status === "cancel_requested";
      provisionButton.disabled = deploymentRunning;
      deployButton.disabled = deploymentRunning;
      promptInput.hidden = activeView !== "chat";
      promptInput.disabled = gate.inputDisabled;
      promptInput.placeholder = gate.inputDisabled
        ? gate.disabledReason
        : (latestState?.activeProtocol === "activity"
          ? "Send a Teams Activity message... Enter sends, Shift+Enter adds a line."
          : "Ask the agent something... Enter sends, Shift+Enter adds a line.");
      stopLocalAction.hidden = !(activeView === "chat" && latestState?.target !== "hosted" && latestState?.localRun?.running);
      stopLocalAction.disabled = false;
      if (activeView === "deploy") {
        provisionButton.hidden = !latestState?.deployment?.needsProvision;
        deployButton.hidden = Boolean(latestState?.deployment?.needsProvision);
        deployButton.textContent = "Deploy";
      }
      primaryGuideAction.disabled = deploymentRunning && activeView !== "teams" && (activeView === "deploy" || foundryPanelOpen || latestState?.target === "hosted");
      clearButton.hidden = activeView === "teams";
      clearButton.textContent = activeView === "deploy" ? "Clear deploy log" : "Clear transcript";
    }

    function renderProtocolToggle(state) {
      const protocols = state.protocols || {};
      const order = ["responses", "activity", "invocations"];
      protocolToggle.innerHTML = order.map((protocol) => {
        const capability = protocols[protocol] || { label: protocol, status: "unknown", reason: "Not discovered." };
        const active = (state.activeProtocol || "responses") === protocol;
        const disabled = capability.status !== "supported";
        const title = capability.reason || capability.endpoint || capability.label;
        return '<button type="button" role="tab" class="protocol-tab ' + (active ? "active " : "") + escapeHtml(capability.status || "unknown") + '" data-protocol="' + escapeHtml(protocol) + '" aria-selected="' + String(active) + '" ' + (disabled ? "disabled " : "") + 'title="' + escapeHtml(title) + '">' +
          '<span>' + escapeHtml(capability.label || protocol) + '</span>' +
          '<span class="protocol-state">' + escapeHtml(capability.status || "unknown") + '</span>' +
        '</button>';
      }).join("");
    }

    function renderFoundryStatus(state) {
      foundryStatus.hidden = true;
      return;
      const hosted = state.hosted || {};
      const connected = Boolean(state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment);
      const showingFoundryContext = foundryPanelOpen || state.target === "hosted";
      const hasHosted = Boolean(hosted.version || hosted.responsesEndpoint);
      foundryStatus.hidden = !connected || !showingFoundryContext;
      if (!connected || !showingFoundryContext) return;
      foundryStatusTitle.textContent = hasHosted ? "Foundry hosted agent is ready" : "Foundry hosted agent is not deployed yet";
      foundryStatusCopy.textContent = hasHosted
        ? "Use the same transcript against the hosted Responses endpoint, or deploy a new version intentionally when local changes are ready."
        : "Foundry discovery loaded the project but did not find a hosted release for this agent. Local testing remains available while you prepare the first deploy.";
      foundryStatusBadge.textContent = hasHosted ? "Hosted ready" : "First deploy needed";
      foundryStatusBadge.className = "badge " + (hasHosted ? "ok" : "");
      foundryStatusAgent.textContent = hosted.agentName || state.selectedAgent?.displayName || "Not resolved";
      foundryStatusRelease.textContent = hosted.version ? "Current release v" + hosted.version : "Not deployed";
      foundryStatusState.textContent = hosted.status || (hasHosted ? "deployed" : "not_deployed");
      foundryStatusEndpoint.textContent = hosted.responsesEndpoint || "Not discovered";
    }

    function renderDeploy(state) {
      const hosted = state.hosted || {};
      foundryTarget.textContent = hosted.projectEndpoint || "Not discovered";
      hostedAgent.textContent = (hosted.agentName || state.selectedAgent?.displayName || "minimal-agent") + " · " + (state.selectedAgent?.rootLabel || "");
      hostedVersion.textContent = hosted.version ? "Version " + hosted.version : "Not deployed";
      const lines = state.deployment?.log || [];
      const discoveryLine = hosted.lastRemoteDiscoveryStatus
        ? "Foundry discovery: " + hosted.lastRemoteDiscoveryStatus + (hosted.lastRemoteDiscoveryMessage ? " — " + hosted.lastRemoteDiscoveryMessage : "") + "\\n\\n"
        : "";
      deployLog.textContent = lines.length ? lines.join("") : [
        "$ azd env get-values\\n",
        discoveryLine,
        "Use Start local to collect the Foundry project endpoint in the canvas dialog, bootstrap .env, then refresh discovery here.\\n",
        "Then discover the deployed Foundry agent version and protocol endpoints.\\n\\n",
        "$ azd deploy " + (state.selectedAgent?.serviceName || "minimal-agent") + " --no-prompt\\n",
        "Deploy changes to Foundry and register a new hosted version.\\n",
      ].join("");
      deployLog.scrollTop = deployLog.scrollHeight;
    }

    function defineProtocolTurnElements() {
      const definitions = [
        ["responses-turn", "responses"],
        ["activity-turn", "activity"],
        ["invocation-turn", "invocations"],
      ];
      for (const [tagName, protocol] of definitions) {
        if (customElements.get(tagName)) continue;
        customElements.define(tagName, class extends HTMLElement {
          connectedCallback() {
            this.classList.add("turn", "protocol-turn", protocol + "-turn");
            this.dataset.protocol = protocol;
            if (!this.hasAttribute("role")) this.setAttribute("role", "article");
          }
        });
      }
    }

    function turnTargetLabel(turn, protocolLabel) {
      const base = turn.target === "hosted" ? "Foundry" : "Local";
      return protocolLabel ? base + " · " + protocolLabel : base;
    }

    function renderUserBubble(turn, { target, input, reactions = [] } = {}) {
      return '<section class="bubble user">' +
        '<div class="bubble-head"><span class="speaker user">You</span><span>' + escapeHtml(target || turnTargetLabel(turn)) + ' · ' + escapeHtml(formatTime(turn.createdAt)) + '</span></div>' +
        '<div class="bubble-body">' + renderMarkdown(input ?? turn.input ?? "") + '</div>' +
        renderReactionRow(reactions) +
        '</section>';
    }

    function renderTurnDetails(turn, detailsKey, detailsId, detailsOpen, label = "Details", copyableAnswer = "") {
      return '<div class="details-row"><button type="button" class="details-toggle" data-details-key="' + escapeHtml(detailsKey) + '" aria-expanded="' + String(detailsOpen) + '" aria-controls="' + escapeHtml(detailsId) + '"><span class="details-toggle-icon" aria-hidden="true"></span><span>' + escapeHtml(label) + '</span></button>' +
        (copyableAnswer ? '<button type="button" class="copy-answer" data-details-key="' + escapeHtml(detailsKey) + '">Copy answer</button>' : label === "Details" ? '<button type="button" class="copy-answer" data-details-key="' + escapeHtml(detailsKey) + '" disabled>Copy answer</button>' : "") +
        '</div>' +
        '<div id="' + escapeHtml(detailsId) + '" class="details-panel" data-details-key="' + escapeHtml(detailsKey) + '" role="region" aria-label="' + escapeHtml(label) + '" ' + (detailsOpen ? "" : "hidden") + '><pre>' + escapeHtml(JSON.stringify(turn, null, 2)) + '</pre></div>';
    }

    function renderResponsesTurn(turn, index) {
      const detailsKey = responseDetailsKey(turn, index);
      const detailsId = responseDetailsPanelId(detailsKey);
      const detailsOpen = expandedResponseDetails.has(detailsKey);
      const ok = turn.response?.ok;
      const streaming = turn.response?.streaming;
      const display = responseDisplayState(turn.response);
      const answer = display.answer;
      const hasAnswer = display.hasAnswer;
      const copyableAnswer = copyableAnswerText(turn.response);
      const waitingForFirstToken = display.waitingForFirstToken;
      const activeLabel = turn.response?.delivery?.upstreamStreaming ? "streaming" : "waiting";
      const body = waitingForFirstToken
        ? '<div class="first-token" role="status" aria-live="polite"><span>Thinking...</span><span class="token-dots" aria-hidden="true"><span></span><span></span><span></span></span></div>'
        : hasAnswer
        ? renderMarkdown(answer)
        : '<div class="no-answer">No answer text returned. Open Details for the raw response.</div>';
      return '<responses-turn>' +
        renderUserBubble(turn, { target: turnTargetLabel(turn, "Responses") }) +
        '<section class="bubble agent">' +
        '<div class="bubble-head"><span class="speaker agent">Agent</span><span class="badge ' + (streaming ? "" : ok ? "ok" : "fail") + '">' + escapeHtml(streaming ? activeLabel : String(turn.response?.status ?? "error")) + ' · ' + escapeHtml(String(turn.response?.durationMs ?? 0)) + 'ms</span></div>' +
        '<div class="bubble-body">' + body + '</div>' +
        renderTurnDetails(turn, detailsKey, detailsId, detailsOpen, "Details", copyableAnswer) +
        '</section>' +
        '</responses-turn>';
    }

    function renderInvocationTurn(turn, index) {
      const detailsKey = responseDetailsKey(turn, index);
      const detailsId = responseDetailsPanelId(detailsKey);
      const detailsOpen = expandedResponseDetails.has(detailsKey);
      const ok = turn.response?.ok;
      const display = responseDisplayState(turn.response);
      const streaming = turn.response?.streaming;
      const waitingForFirstToken = display.waitingForFirstToken;
      const answer = waitingForFirstToken
        ? '<div class="first-token" role="status" aria-live="polite"><span>Invoking...</span><span class="token-dots" aria-hidden="true"><span></span><span></span><span></span></span></div>'
        : display.hasAnswer
        ? renderMarkdown(display.answer)
        : '<div class="no-answer">No invocation output returned. Open Details for the raw result.</div>';
      const copyableAnswer = copyableAnswerText(turn.response);
      return '<invocation-turn>' +
        renderUserBubble(turn, { target: turnTargetLabel(turn, "Invocations") }) +
        '<section class="bubble agent invocation-result">' +
        '<div class="bubble-head"><span class="speaker agent">Invocation result</span><span class="badge ' + (streaming ? "" : ok ? "ok" : "fail") + '">' + escapeHtml(String(streaming ? "waiting" : turn.response?.status ?? "error")) + ' · ' + escapeHtml(String(turn.response?.durationMs ?? 0)) + 'ms</span></div>' +
        '<div class="bubble-body">' + answer + '</div>' +
        renderTurnDetails(turn, detailsKey, detailsId, detailsOpen, "Invocation details", copyableAnswer) +
        '</section>' +
        '</invocation-turn>';
    }

    function renderProtocolTurn(turn, index) {
      const protocol = turn.protocol || "responses";
      if (protocol === "activity") return renderActivityTurn(turn, index);
      if (protocol === "invocations") return renderInvocationTurn(turn, index);
      return renderResponsesTurn(turn, index);
    }

    function renderMessages(messages) {
      transcript.classList.toggle("empty-state", !messages.length);
      if (!messages.length) {
        const activeProtocol = latestState?.activeProtocol || "responses";
        const copy = activeProtocol === "activity"
          ? 'Send a prompt to test <code>POST /activity/messages</code> and connector egress.'
          : activeProtocol === "invocations"
          ? 'Send an invocation payload to test <code>POST /invocations</code>.'
          : 'Send a prompt to test <code>POST /responses</code>.';
        transcript.innerHTML = '<div class="empty">' + copy + '</div>';
        return;
      }
      transcript.querySelectorAll(".details-panel[data-details-key] pre").forEach((pre) => {
        const panel = pre.closest(".details-panel[data-details-key]");
        if (panel?.dataset?.detailsKey) responseDetailsScroll.set(panel.dataset.detailsKey, pre.scrollTop);
      });
      const shouldStickToBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 48;
      transcript.innerHTML = messages.map((turn, index) => renderProtocolTurn(turn, index)).join("");
      void hydrateVendoredMermaidDiagrams(transcript);
      const restoreDetailsScroll = () => transcript.querySelectorAll(".details-panel[data-details-key] pre").forEach((pre) => {
        const panel = pre.closest(".details-panel[data-details-key]");
        const top = panel?.dataset?.detailsKey ? responseDetailsScroll.get(panel.dataset.detailsKey) : undefined;
        if (top !== undefined) pre.scrollTop = top;
      });
      restoreDetailsScroll();
      if (shouldStickToBottom) transcript.scrollTop = transcript.scrollHeight;
      requestAnimationFrame(restoreDetailsScroll);
    }

    let activeMermaidLightbox = null;

    function openMermaidDiagramLightbox(trigger) {
      const diagram = trigger?.closest?.(".mermaid-diagram");
      if (!diagram) return;
      const svg = diagram.querySelector("svg");
      if (!svg) return;
      closeMermaidDiagramLightbox();
      const overlay = document.createElement("div");
      overlay.className = "mermaid-lightbox-backdrop";
      overlay.setAttribute("role", "presentation");
      overlay.innerHTML = '<div class="mermaid-lightbox" role="dialog" aria-modal="true" aria-label="Expanded Mermaid diagram">' +
        '<div class="mermaid-lightbox-head"><strong>Mermaid diagram</strong><button type="button" class="mermaid-lightbox-close" aria-label="Close expanded diagram">Close</button></div>' +
        '<div class="mermaid-lightbox-body"></div>' +
        '</div>';
      const clone = svg.cloneNode(true);
      clone.removeAttribute("width");
      clone.removeAttribute("height");
      overlay.querySelector(".mermaid-lightbox-body").appendChild(clone);
      document.body.appendChild(overlay);
      activeMermaidLightbox = { overlay, trigger };
      overlay.querySelector(".mermaid-lightbox-close")?.focus();
    }

    function closeMermaidDiagramLightbox() {
      if (!activeMermaidLightbox) return;
      const { overlay, trigger } = activeMermaidLightbox;
      activeMermaidLightbox = null;
      overlay.remove();
      if (trigger?.isConnected) trigger.focus();
    }

    function renderReaction(value) {
      const normalized = String(value || "like").toLowerCase();
      return {
        like: "👍",
        eyes: "👀",
        heart: "❤️",
        laugh: "😄",
        angry: "😠",
        sad: "😢",
      }[normalized] || ":" + normalized + ":";
    }

    function activityReactions(turn, targetId) {
      const reactions = [];
      for (const event of turn.events || []) {
        if (event.activityId !== targetId) continue;
        if (event.kind === "outbound.reaction.add") reactions.push(event.reaction || "like");
        if (event.kind === "outbound.reaction.remove") {
          const index = reactions.lastIndexOf(event.reaction || "like");
          if (index >= 0) reactions.splice(index, 1);
        }
      }
      return reactions;
    }

    function activityMessageItems(turn) {
      const items = [];
      const byId = new Map();
      for (const event of turn.events || []) {
        if (!event?.kind?.startsWith?.("outbound.")) continue;
        if (event.kind === "outbound.typing") {
          items.push({ type: "typing", event });
          continue;
        }
        if (event.kind === "outbound.reaction.add" || event.kind === "outbound.reaction.remove") continue;
        const activityId = event.activityId || "event-" + items.length;
        let item = byId.get(activityId);
        if (!item) {
          item = { type: "message", activityId, text: "", edited: false, deleted: false, event };
          byId.set(activityId, item);
          items.push(item);
        }
        item.event = event;
        if (event.kind === "outbound.message.update") {
          item.text = event.text || item.text;
          item.edited = true;
          item.deleted = false;
        } else if (event.kind === "outbound.message.delete") {
          item.deleted = true;
        } else {
          item.text = event.text || "";
        }
      }
      return items;
    }

    function renderReactionRow(reactions) {
      if (!reactions.length) return "";
      return '<div class="reaction-row">' + reactions.map((reaction) =>
        '<span class="reaction" title="' + escapeHtml(reaction) + '">' + escapeHtml(renderReaction(reaction)) + '</span>'
      ).join("") + '</div>';
    }

    function renderActivityTurn(turn, index) {
      const detailsKey = responseDetailsKey(turn, index);
      const detailsId = responseDetailsPanelId(detailsKey);
      const detailsOpen = expandedResponseDetails.has(detailsKey);
      const target = turn.target === "hosted" ? "Foundry Activity" : "Local Activity";
      const inbound = (turn.events || []).find((event) => event.kind === "inbound.message");
      const inboundReactions = activityReactions(turn, inbound?.activityId);
      const bubbles = activityMessageItems(turn).map((item) => {
        if (item.type === "typing") {
          return '<section class="teams-typing" aria-live="polite"><span class="avatar agent-avatar" aria-hidden="true">A</span><div class="typing-pill"><span>Agent is typing</span><span class="typing-dots" aria-hidden="true"><span></span><span></span><span></span></span></div></section>';
        }
        const reactions = activityReactions(turn, item.activityId);
        const meta = item.deleted ? "Deleted" : item.edited ? "Edited" : "Activity";
        return '<section class="bubble agent teams-message ' + (item.deleted ? "deleted" : "") + '">' +
          '<div class="bubble-head"><span class="speaker agent">Agent</span><span>' + escapeHtml(meta) + ' · ' + escapeHtml(formatTime(item.event?.at || turn.createdAt)) + '</span></div>' +
          '<div class="bubble-body">' + (item.deleted ? '<em>This message was deleted.</em>' : item.text ? renderMarkdown(item.text) : '<div class="no-answer">Connector event returned no text.</div>') + '</div>' +
          renderReactionRow(reactions) +
          '</section>';
      }).join("");
      return '<activity-turn>' +
        '<div class="teams-thread">' +
        '<div class="teams-row outgoing"><div class="teams-stack">' + renderUserBubble(turn, { target, input: inbound?.text || turn.input || "", reactions: inboundReactions }) + '</div><span class="avatar user-avatar" aria-hidden="true">You</span></div>' +
        '<div class="teams-row incoming"><span class="avatar agent-avatar" aria-hidden="true">A</span><div class="teams-stack">' +
        (bubbles || '<section class="bubble agent"><div class="first-token" role="status" aria-live="polite"><span>Waiting for connector events...</span><span class="token-dots" aria-hidden="true"><span></span><span></span><span></span></span></div></section>') +
        '</div></div>' +
        '</div>' +
        renderTurnDetails(turn, detailsKey, detailsId, detailsOpen, "Activity details") +
        '</activity-turn>';
    }

    transcript.addEventListener("scroll", (event) => {
      const pre = event.target?.closest?.(".details-panel[data-details-key] pre");
      if (!pre) return;
      const panel = pre.closest(".details-panel[data-details-key]");
      if (panel?.dataset?.detailsKey) responseDetailsScroll.set(panel.dataset.detailsKey, pre.scrollTop);
    }, true);

    transcript.addEventListener("click", (event) => {
      const expand = event.target.closest(".mermaid-expand-button");
      if (expand) {
        event.preventDefault();
        event.stopPropagation();
        openMermaidDiagramLightbox(expand);
        return;
      }
      const copy = event.target.closest(".copy-answer");
      if (copy) {
        event.preventDefault();
        event.stopPropagation();
        const bubble = copy.closest(".bubble.agent");
        const text = answerTextForDetailsKey(copy.dataset.detailsKey) || bubble?.querySelector(".bubble-body")?.innerText?.trim();
        if (!text) return;
        navigator.clipboard.writeText(text).then(() => {
          copy.textContent = "Copied";
          window.setTimeout(() => {
            copy.textContent = "Copy answer";
          }, 1200);
        }).catch((error) => {
          setStatus("fail", error.message || "Copy failed.");
        });
        return;
      }
      const toggle = event.target.closest(".details-toggle");
      if (!toggle) return;
      event.preventDefault();
      event.stopPropagation();
      const key = toggle.dataset.detailsKey;
      if (!key) return;
      if (expandedResponseDetails.has(key)) expandedResponseDetails.delete(key);
      else expandedResponseDetails.add(key);
      renderMessages(latestState?.visibleMessages || []);
    });

    document.addEventListener("click", (event) => {
      if (event.target?.classList?.contains("mermaid-lightbox-backdrop")) closeMermaidDiagramLightbox();
      if (event.target?.closest?.(".mermaid-lightbox-close")) closeMermaidDiagramLightbox();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && activeMermaidLightbox) {
        event.preventDefault();
        closeMermaidDiagramLightbox();
      }
    });

    function answerTextForDetailsKey(detailsKey) {
      const messages = latestState?.visibleMessages || [];
      for (let index = 0; index < messages.length; index += 1) {
        if (responseDetailsKey(messages[index], index) === detailsKey) {
          return copyableAnswerText(messages[index].response);
        }
      }
      return "";
    }

    async function sendPrompt() {
      if (inFlight) return;
      const gate = composerGate(latestState, { activeView, inFlight });
      if (!gate.canSend) {
        setStatus("fail", gate.disabledReason || "Prompt composer is not ready.");
        return;
      }
      const input = promptInput.value.trim();
      if (!input) return;
      const now = Date.now();
      if (lastSend.input === input && now - lastSend.at < 1500) return;
      lastSend = { input, at: now };
      inFlight = true;
      sendButton.disabled = true;
      const waitingOnHosted = latestState?.target === "hosted";
      const sendStartedAt = Date.now();
      clearHostedWaitTimer();
      if (waitingOnHosted) {
        setHostedWaitingStatus(sendStartedAt);
        hostedWaitTimer = setInterval(() => {
          if (inFlight) setHostedWaitingStatus(sendStartedAt);
        }, 1000);
      } else {
        setStatus("", "Sending prompt...");
      }
      try {
        await saveEndpointFromInput();
        if (latestState?.activeProtocol === "activity") {
          const state = await request("/api/activity", {
            method: "POST",
            body: JSON.stringify({ input }),
          });
          promptInput.value = "";
          renderSnapshot(state);
          const latest = state.messages[state.messages.length - 1];
          setStatus(latest?.response?.ok ? "ok" : "fail", "Activity " + (latest?.response?.status ?? "error") + " in " + (latest?.response?.durationMs ?? 0) + "ms");
          return;
        }
        if (latestState?.activeProtocol === "invocations") {
          const state = await request("/api/invocations", {
            method: "POST",
            body: JSON.stringify({ input }),
          });
          promptInput.value = "";
          renderSnapshot(state);
          const latest = state.messages[state.messages.length - 1];
          setStatus(latest?.response?.ok ? "ok" : "fail", "Invocation " + (latest?.response?.status ?? "error") + " in " + (latest?.response?.durationMs ?? 0) + "ms");
          return;
        }
        const response = await fetch("/api/responses/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input }),
        });
        if (!response.ok || !response.body) {
          const payload = await response.json().catch(() => ({ error: "Request failed." }));
          throw new Error(payload.error || "Request failed.");
        }
        promptInput.value = "";
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\\n\\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const dataLine = part.split("\\n").find((line) => line.startsWith("data: "));
            if (!dataLine) continue;
            const state = JSON.parse(dataLine.slice(6));
            renderSnapshot(state);
            const latest = state.messages[state.messages.length - 1];
            if (latest?.response?.streaming) {
              const text = responseText(latest.response.body);
              const hasText = String(text || "").trim();
              if (waitingOnHosted && !latest.response.delivery?.upstreamStreaming) {
                setHostedWaitingStatus(sendStartedAt);
              } else {
                setStatus("", hasText && latest.response.delivery?.upstreamStreaming ? "Streaming response..." : "Waiting for response...");
              }
            } else if (latest?.response) {
              setStatus(latest.response.ok ? "ok" : "fail", "Response " + latest.response.status + " in " + latest.response.durationMs + "ms");
            }
          }

        }
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        clearHostedWaitTimer();
        inFlight = false;
        renderView();
      }
    }

    function formatTime(value) {
      try {
        return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      } catch {
        return value;
      }
    }

    function formatEndpointShort(value) {
      try {
        const parsed = new URL(value);
        const host = parsed.hostname === "127.0.0.1" ? "localhost" : parsed.hostname;
        return host + (parsed.port ? ":" + parsed.port : "");
      } catch {
        return value || "";
      }
    }

    async function request(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Request failed.");
      }
      return payload;
    }

    async function load() {
      renderSnapshot(await request("/api/state"));
    }

    function connectStateEvents() {
      if (typeof EventSource === "undefined") return;
      const events = new EventSource("/api/events");
      events.addEventListener("snapshot", (event) => {
        try {
          renderSnapshot(JSON.parse(event.data));
        } catch {
          // Ignore malformed event payloads; the next valid snapshot will recover.
        }
      });
    }

    async function saveEndpointFromInput() {
      const endpoint = latestState?.target === "hosted"
        ? latestState?.hosted?.responsesEndpoint || ""
        : latestState?.localEndpoint || "http://127.0.0.1:8088";
      return request("/api/endpoint", {
        method: "POST",
        body: JSON.stringify({ endpoint, target: latestState?.target || "local" }),
      });
    }

    async function refreshConfigFromDisk() {
      primaryGuideAction.disabled = true;
      setStatus("", "Refreshing .env...");
      try {
        const state = await request("/api/config/refresh", { method: "POST" });
        renderSnapshot(state);
        const connected = Boolean(state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment);
        setStatus(connected ? "ok" : "fail", connected ? "Loaded .env configuration." : "Still missing .env Foundry values.");
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    async function startLocalFromCanvas() {
      activeView = "chat";
      foundryPanelOpen = false;
      primaryGuideAction.disabled = true;
      setStatus("", "Starting local agent...");
      try {
        const response = await fetch("/api/local/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const payload = await response.json();
        if (response.status === 409 && payload.needsProjectEndpoint) {
          renderSnapshot(payload.state);
          showProjectEndpointDialog();
          setStatus("", "Foundry project endpoint is required before starting local.");
          return;
        }
        if (!response.ok) {
          throw new Error(payload.error || "Failed to start local agent.");
        }
        const state = payload;
        renderSnapshot(state);
        setStatus("", state.localRun?.running ? "Local agent is running; waiting for readiness." : "Local agent starting.");
        scheduleLocalRefresh();
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    function showProjectEndpointDialog() {
      projectEndpointDialog.hidden = false;
      projectEndpointDialogInput.value = latestState?.foundryConnection?.projectEndpoint || latestState?.hosted?.projectEndpoint || "";
      window.setTimeout(() => projectEndpointDialogInput.focus(), 0);
    }

    function hideProjectEndpointDialog() {
      projectEndpointDialog.hidden = true;
      projectEndpointSubmit.disabled = false;
    }

    async function bootstrapProjectAndStartLocal() {
      const projectEndpoint = projectEndpointDialogInput.value.trim();
      if (!projectEndpoint) return;
      projectEndpointSubmit.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", "Saving Foundry project endpoint...");
      try {
        const payload = await request("/api/env/bootstrap", {
          method: "POST",
          body: JSON.stringify({
            projectEndpoint,
            overwrite: false,
            targetPaths: latestState?.localEnv?.path ? [latestState.localEnv.path] : undefined,
          }),
        });
        renderSnapshot(payload.state);
        hideProjectEndpointDialog();
        setStatus("ok", "Updated Foundry .env values. Starting local agent...");
        await startLocalFromCanvas();
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        projectEndpointSubmit.disabled = false;
        primaryGuideAction.disabled = false;
      }
    }

    async function stopLocalFromCanvas() {
      stopLocalAction.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", "Stopping local agent...");
      try {
        const state = await request("/api/local/stop", { method: "POST" });
        renderSnapshot(state);
        if (localRefreshTimer) window.clearTimeout(localRefreshTimer);
        setStatus("", "Local agent stopped.");
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    async function checkReadinessFromCanvas() {
      if (latestState?.target === "hosted" && !latestState?.hosted?.responsesEndpoint) {
        setStatus("fail", "No hosted Responses endpoint is discovered yet. Use Start local to bootstrap .env from the endpoint dialog, then refresh.");
        return;
      }
      primaryGuideAction.disabled = true;
      setStatus("", "Checking readiness...");
      try {
        await saveEndpointFromInput();
        renderSnapshot(await request("/api/health", { method: "POST" }));
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    stopLocalAction.addEventListener("click", async () => {
      await stopLocalFromCanvas();
    });

    projectEndpointCancel.addEventListener("click", () => {
      hideProjectEndpointDialog();
      request("/api/project-endpoint-prompt/clear", { method: "POST" }).catch(() => {});
      setStatus("fail", "Foundry project endpoint is required before starting local.");
    });

    projectEndpointForm.addEventListener("submit", (event) => {
      event.preventDefault();
      void bootstrapProjectAndStartLocal();
    });

    agentPickerButton.addEventListener("click", () => {
      agentMenuOpen = !agentMenuOpen;
      renderAgentPicker(latestState || {});
    });

    agentMenu.addEventListener("click", async (event) => {
      const option = event.target.closest(".agent-option");
      if (!option) return;
      await selectAgent(option.dataset.agentId);
    });

    document.addEventListener("click", (event) => {
      if (!event.target.closest(".agent-picker")) closeAgentMenu();
    });

    agentPickerButton.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeAgentMenu();
    });

    async function showLocal() {
      activeView = "chat";
      foundryPanelOpen = false;
      const state = await request("/api/target", {
        method: "POST",
        body: JSON.stringify({ target: "local" }),
      });
      renderSnapshot(state);
      if (state.lastHealth?.ok) {
        setStatus("ok", "Local agent ready.");
      } else if (state.localRun?.running) {
        setStatus("", "Local agent starting.");
      } else {
        setStatus("", "Local target selected. Start local when ready.");
      }
    }

    async function showFoundryChat() {
      activeView = "chat";
      foundryPanelOpen = true;
      const state = await request("/api/target", {
        method: "POST",
        body: JSON.stringify({ target: "hosted" }),
      });
      renderSnapshot(state);
      setStatus(state.hosted?.responsesEndpoint ? "ok" : "fail", state.hosted?.responsesEndpoint ? "Foundry target selected." : "Foundry endpoint not discovered.");
    }

    async function showDeploy() {
      activeView = "deploy";
      const state = await request("/api/hosted/refresh", { method: "POST" });
      renderSnapshot(state);
      setStatus("", "Foundry step.");
    }

    async function showFoundry() {
      activeView = "chat";
      foundryPanelOpen = true;
      const state = await request("/api/target", {
        method: "POST",
        body: JSON.stringify({ target: "hosted" }),
      });
      renderSnapshot(state);
      setStatus(state.hosted?.responsesEndpoint ? "ok" : "fail", state.hosted?.responsesEndpoint ? "Foundry target selected." : "Foundry endpoint not discovered.");
    }

    async function showTeams() {
      activeView = "teams";
      if (latestState) renderSnapshot(latestState);
      else renderView();
      setStatus("", "Teams step.");
      request("/api/hosted/refresh", { method: "POST" })
        .then((state) => {
          if (activeView === "teams") renderSnapshot(state);
        })
        .catch((error) => {
          if (activeView === "teams") setStatus("fail", error.message);
        });
    }

    localStep.addEventListener("click", () => {
      void showLocal();
    });

    foundryStep.addEventListener("click", () => {
      void showFoundry();
    });

    teamsStep.addEventListener("click", () => {
      void showTeams();
    });

    advancedToggle.addEventListener("click", () => {
      void refreshConfigFromDisk();
    });

    primaryGuideAction.addEventListener("click", () => {
      if (!(latestState?.foundryConnection?.projectEndpoint && latestState?.foundryConnection?.modelDeployment)) {
        if (latestState?.target === "hosted" || foundryPanelOpen) {
          void refreshConfigFromDisk();
        } else {
          void startLocalFromCanvas();
        }
        return;
      }
      if (activeView === "deploy") {
        if (latestState?.deployment?.needsProvision) {
          void runProvision();
        } else {
          void runDeploy();
        }
      } else if (foundryPanelOpen || latestState?.target === "hosted") {
        if (latestState?.deployment?.needsProvision) {
          void runProvision();
        } else {
          void runDeploy();
        }
      } else if (!latestState?.localRun?.running) {
        void startLocalFromCanvas();
      }
    });

    testHostedAction.addEventListener("click", () => {
      void showFoundryChat();
    });

    promptInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (event.repeat) return;
        void sendPrompt();
      }
    });

    sendButton.addEventListener("click", () => {
      void sendPrompt();
    });

    protocolToggle.addEventListener("click", async (event) => {
      const button = event.target.closest(".protocol-tab");
      if (!button || button.disabled) return;
      const protocol = button.dataset.protocol;
      try {
        renderSnapshot(await request("/api/protocol", {
          method: "POST",
          body: JSON.stringify({ protocol }),
        }));
        setStatus("ok", "Switched to " + button.innerText.split("\\n")[0] + ".");
      } catch (error) {
        setStatus("fail", error.message);
      }
    });

    clearButton.addEventListener("click", async () => {
      renderSnapshot(await request(activeView === "deploy" ? "/api/deploy/clear" : "/api/clear", { method: "POST" }));
      setStatus("", activeView === "deploy" ? "Deploy log cleared." : "Transcript cleared.");
    });

    async function streamCommand({ path, button, confirmText, runningText, successText, failureText }) {
      if (!confirm(confirmText)) return;
      button.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", runningText);
      try {
        const response = await fetch(path, { method: "POST" });
        if (!response.ok || !response.body) {
          const payload = await response.json().catch(() => ({ error: failureText }));
          throw new Error(payload.error || failureText);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\\n\\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const dataLine = part.split("\\n").find((line) => line.startsWith("data: "));
            if (!dataLine) continue;
            renderSnapshot(JSON.parse(dataLine.slice(6)));
          }
        }
        buffer += decoder.decode();
        if (buffer.trim()) {
          const dataLine = buffer.split("\\n").find((line) => line.startsWith("data: "));
          if (dataLine) renderSnapshot(JSON.parse(dataLine.slice(6)));
        }
        const finalState = await request("/api/state");
        renderSnapshot(finalState);
        setStatus(finalState.deployment?.exitCode === 0 ? "ok" : "fail", finalState.deployment?.exitCode === 0 ? successText : failureText);
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        button.disabled = false;
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    async function cancelOperationFromCanvas() {
      cancelOperationButton.disabled = true;
      setStatus("", "Requesting cancellation...");
      try {
        const payload = await request("/api/operation/cancel", {
          method: "POST",
          body: JSON.stringify({ operationId: latestState?.operations?.active?.id || null }),
        });
        renderSnapshot(payload.state);
        setStatus(payload.accepted ? "warn" : "fail", payload.accepted ? (payload.operation?.cancellation?.message || "Cancellation requested.") : "No cancellable operation is running.");
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        renderView();
      }
    }

    async function runProvision() {
      await streamCommand({
        path: "/api/provision/stream",
        button: provisionButton,
        confirmText: "Prepare this repo for hosted deployment into the connected Foundry project?",
        runningText: "Preparing deploy...",
        successText: "Deploy prep complete.",
        failureText: "Deploy prep failed.",
      });
    }

    async function runDeploy() {
      await streamCommand({
        path: "/api/deploy/stream",
        button: deployButton,
        confirmText: "Deploy changes to Foundry and create a new hosted agent version?",
        runningText: "Deploying to Foundry...",
        successText: "Deploy complete.",
        failureText: "Deploy failed.",
      });
    }

    provisionButton.addEventListener("click", () => {
      void runProvision();
    });

    deployButton.addEventListener("click", () => {
      void runDeploy();
    });

    cancelOperationButton.addEventListener("click", () => {
      void cancelOperationFromCanvas();
    });

    defineProtocolTurnElements();
    connectStateEvents();
    load().catch((error) => setStatus("fail", error.message));
`;

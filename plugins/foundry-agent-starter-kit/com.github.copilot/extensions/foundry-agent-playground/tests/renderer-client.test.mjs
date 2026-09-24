import assert from "node:assert/strict";
import { test } from "node:test";
import {
    composerGate,
    copyableAnswerText,
    localFailureDetail,
    localReadinessState,
    localStartupText,
    hasRawHtmlMermaidLabel,
    hasUnsafeSvgCss,
    mermaidSourceSupport,
    rendererClientScript,
    renderMarkdown,
    renderMermaidBlock,
    renderVendoredMermaidBlock,
    sanitizeMermaidLabelHtml,
    responseDetailsKey,
    responseDetailsPanelId,
    responseDisplayState,
    responseText,
    shouldOpenProjectEndpointDialog,
} from "../renderer/renderer-client.mjs";
import { rendererStyles } from "../renderer/renderer-styles.mjs";

test("renderer styles bind to Copilot canvas dark-mode theme attributes", () => {
    assert.match(rendererStyles, /html\[data-color-mode="dark"\]/);
    assert.match(rendererStyles, /body\[data-color-mode="dark"\]/);
    assert.match(rendererStyles, /--background-color-default/);
    assert.match(rendererStyles, /--text-color-default/);
    assert.match(rendererStyles, /--border-color-default/);
});

test("renderer styles force structural surfaces back to light in light mode", () => {
    assert.match(rendererStyles, /html\[data-theme="light"\]/);
    assert.match(rendererStyles, /html\[data-color-mode="light"\]/);
    assert.match(rendererStyles, /body\[data-color-mode="light"\]/);
    assert.match(rendererStyles, /--cp-panel:\s*#ffffff;/);
    assert.match(rendererStyles, /--cp-surface-soft:\s*#f5f5f5;/);
    assert.match(rendererStyles, /--cp-shadow:\s*0 1px 2px rgba\(31, 35, 40, 0\.08\);/);
    assert.match(rendererStyles, /html\[data-theme="light"\]\s+\.hero[\s\S]*border-bottom:\s*1px solid var\(--cp-border\);/);
    assert.match(rendererStyles, /html\[data-theme="light"\]\s+\.empty[\s\S]*background:\s*var\(--cp-surface\);/);
});

test("mermaid diagrams inherit theme-safe colors in light and dark modes", () => {
    assert.match(rendererStyles, /html\[data-theme="light"\][\s\S]*--cp-surface:\s*#ffffff;/);
    assert.match(rendererStyles, /html\[data-color-mode="dark"\][\s\S]*--cp-surface:\s*var\(--background-color-default/);
    assert.match(rendererStyles, /\.bubble-body \.mermaid-diagram,[\s\S]*background:\s*var\(--cp-surface\);/);
    assert.match(rendererStyles, /\.mermaid-node rect\s*{[\s\S]*fill:\s*var\(--cp-surface-soft\);/);
    assert.match(rendererStyles, /\.mermaid-node text,[\s\S]*fill:\s*var\(--cp-text\);/);
    assert.match(rendererStyles, /\.mermaid-edge\s*{[\s\S]*stroke:\s*var\(--cp-text-muted\);/);
    assert.match(rendererStyles, /\.mermaid-diagram marker path\s*{[\s\S]*fill:\s*var\(--cp-text-muted\);/);
    assert.match(rendererStyles, /\.mermaid-lightbox\s*{[\s\S]*width:\s*min\(90vw, 1120px\);/);
    assert.match(rendererStyles, /\.mermaid-lightbox\s*{[\s\S]*height:\s*min\(82vh, 860px\);/);
});

test("composer keeps version visible and groups clear beside send", () => {
    assert.match(rendererStyles, /\.composer-actions\s*{[^}]*justify-content:\s*space-between;/);
    assert.match(rendererStyles, /\.composer-actions\s*{[^}]*align-items:\s*center;/);
    assert.match(rendererStyles, /\.plugin-version\s*{[^}]*font-family:\s*var\(--font-mono/);
    assert.match(rendererStyles, /\.right-actions\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.icon-button\s*{[^}]*inline-size:\s*36px;/);
    assert.match(rendererStyles, /\.icon-button\s*{[^}]*border-color:\s*var\(--cp-border\);/);
    assert.match(rendererStyles, /\.icon-button\s*{[^}]*background:\s*var\(--cp-surface\);/);
    assert.match(rendererStyles, /\.icon-button:hover\s*{[^}]*background:\s*var\(--cp-surface-soft\);/);
    assert.match(rendererStyles, /\.icon-button\.primary\s*{[^}]*background:\s*var\(--cp-accent\);/);
    assert.match(rendererStyles, /\.icon-button\.danger\s*{[^}]*background:\s*var\(--cp-danger\);/);
    assert.match(rendererStyles, /\.icon-button svg\s*{[^}]*stroke:\s*currentColor;/);
    assert.match(rendererClientScript, /const iconSvg = \{/);
    assert.match(rendererClientScript, /function setIconButton\(button, icon, label\)/);
    assert.match(rendererClientScript, /setIconButton\(sendButton, "send", "Send"\);/);
    assert.match(rendererClientScript, /trash: '<svg aria-hidden="true"/);
    assert.match(rendererClientScript, /setIconButton\(clearButton, "trash", activeView === "deploy" \? "Clear deploy log" : "Clear"\);/);
    assert.doesNotMatch(rendererClientScript, /clearButton\.textContent = "Clear";/);
});

test("transcript spacing uses padding and Mermaid toolbar overlays the card", () => {
    assert.match(rendererStyles, /\.content\s*{[^}]*background:\s*var\(--cp-surface\);/);
    assert.match(rendererClientScript, /panelMeta\.hidden = !state\.stats\.total;/);
    assert.match(rendererStyles, /\.panel\s*{[^}]*grid-template-rows:\s*auto auto auto minmax\(0, 1fr\);/);
    assert.match(rendererStyles, /\.activity-log\s*{[^}]*position:\s*absolute;/);
    assert.match(rendererStyles, /\.activity-log\s*{[^}]*right:\s*12px;/);
    assert.match(rendererStyles, /\.activity-log\s*{[^}]*bottom:\s*12px;/);
    assert.match(rendererStyles, /\.activity-log:not\(\[open\]\) \.activity-head\s*{[^}]*width:\s*36px;/);
    assert.match(rendererStyles, /\.activity-log:not\(\[open\]\) \.activity-head\s*{[^}]*height:\s*36px;/);
    assert.match(rendererStyles, /\.activity-log:not\(\[open\]\) \.activity-head > div,[\s\S]*?\.activity-log:not\(\[open\]\) \.activity-head \.badge\s*{[^}]*display:\s*none;/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s*{[^}]*left:\s*12px;/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s*{[^}]*right:\s*12px;/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s*{[^}]*max-height:\s*min\(520px, 72vh\);/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s*{[^}]*box-shadow:\s*var\(--cp-shadow\);/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-head\s*{[^}]*min-height:\s*44px;/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-icon\s*{[^}]*min-height:\s*34px;/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-icon::after\s*{[^}]*content:\s*"Minimize";/);
    assert.match(rendererStyles, /\.activity-log:not\(\[open\]\) \.activity-summary\s*{[^}]*display:\s*none;/);
    assert.match(rendererStyles, /\.activity-log:not\(\[open\]\) \.activity-head\s*{[^}]*opacity:\s*0\.86;/);
    assert.match(rendererStyles, /\.activity-icon svg\s*{[^}]*stroke:\s*currentColor;/);
    assert.match(rendererStyles, /\.panel-header\s*{[^}]*padding:\s*4px 4px 6px;/);
    assert.match(rendererStyles, /\.protocol-tab\s*{[^}]*min-height:\s*26px;/);
    assert.match(rendererStyles, /\.foundry-status\s*{[^}]*padding:\s*4px 6px;/);
    assert.match(rendererStyles, /\.foundry-status-item\s*{[^}]*padding:\s*5px 6px;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*min-height:\s*24px;/);
    assert.match(rendererStyles, /\.transcript\s*{[^}]*grid-row:\s*4;/);
    assert.match(rendererStyles, /\.transcript\s*{[^}]*padding:\s*6px 10px 32px;/);
    assert.match(rendererStyles, /#statusText\s*{[^}]*text-overflow:\s*ellipsis;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.panel-header\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.panel-header\s*{[^}]*flex-wrap:\s*wrap;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.panel-header\s*{[^}]*padding:\s*4px 10px 6px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.activity-log\[open\]\s*{[^}]*gap:\s*3px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.activity-log\[open\] \.activity-icon\s*{[^}]*min-height:\s*32px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.activity-log:not\(\[open\]\) \.activity-items\s*{[^}]*max-height:\s*56px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.activity-log\[open\] \.activity-items\s*{[^}]*max-height:\s*min\(440px, 58vh\);/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.foundry-status-grid\s*{[^}]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\);/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.activity-items\s*{[^}]*max-height:\s*56px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.transcript\s*{[^}]*padding:\s*6px 10px 28px;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.bubble\.user,\s*\n\s*\.bubble\.agent\s*{[^}]*width:\s*calc\(100% - 28px\);/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.bubble\.user\s*{[^}]*margin-left:\s*auto;/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.bubble\.agent\s*{[^}]*margin-right:\s*auto;/);
    assert.match(rendererStyles, /\.bubble-body \.mermaid-diagram\s*{[^}]*padding:\s*10px;/);
    assert.match(rendererStyles, /\.mermaid-toolbar\s*{[^}]*position:\s*absolute;/);
    assert.match(rendererStyles, /\.mermaid-toolbar\s*{[^}]*top:\s*6px;/);
    assert.match(rendererStyles, /\.mermaid-toolbar\s*{[^}]*right:\s*6px;/);
    assert.match(rendererStyles, /\.mermaid-toolbar\s*{[^}]*margin:\s*0;/);
    assert.match(rendererStyles, /\.mermaid-expand-button\s*{[^}]*width:\s*28px;/);
    assert.match(rendererStyles, /\.mermaid-expand-button\s*{[^}]*height:\s*28px;/);
});

test("empty transcript state fills the available transcript space", () => {
    assert.doesNotMatch(rendererStyles, /\.panel:has\(\.transcript\.empty-state\)/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*height:\s*100%;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*overflow:\s*auto;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*padding:\s*6px 10px 28px;/);
    assert.match(rendererStyles, /\.empty\s*{[^}]*gap:\s*6px;/);
    assert.match(rendererStyles, /\.empty\s*{[^}]*min-height:\s*100%;/);
});

test("renderer uses protocol composite web components", () => {
    assert.match(rendererClientScript, /\["responses-turn", "responses"\]/);
    assert.match(rendererClientScript, /\["activity-turn", "activity"\]/);
    assert.match(rendererClientScript, /\["invocation-turn", "invocations"\]/);
    assert.match(rendererClientScript, /customElements\.define\(tagName/);
    assert.match(rendererClientScript, /function renderResponsesTurn/);
    assert.match(rendererClientScript, /function renderActivityTurn/);
    assert.match(rendererClientScript, /function renderInvocationTurn/);
    assert.match(rendererClientScript, /function activityMessageItems/);
    assert.match(rendererClientScript, /setAttribute\("role", "article"\)/);
    assert.match(rendererClientScript, /Invoking\.\.\./);
    assert.match(rendererClientScript, /<responses-turn>/);
    assert.match(rendererClientScript, /<activity-turn>/);
    assert.match(rendererClientScript, /<invocation-turn>/);
    assert.match(rendererStyles, /responses-turn,\s*\n\s*activity-turn,\s*\n\s*invocation-turn\s*{/);
    assert.match(rendererStyles, /contain:\s*layout style;/);
});

test("activity composite uses Teams-like grouped chat affordances", () => {
    assert.match(rendererClientScript, /<div class="teams-thread">/);
    assert.match(rendererClientScript, /<div class="teams-row outgoing">/);
    assert.match(rendererClientScript, /<div class="teams-row incoming">/);
    assert.match(rendererClientScript, /teams-message/);
    assert.match(rendererClientScript, /This message was deleted\./);
    assert.match(rendererClientScript, /item\.edited \? "Edited"/);
    assert.match(rendererStyles, /\.teams-thread\s*{[^}]*display:\s*grid;/);
    assert.match(rendererStyles, /\.teams-row\s*{[^}]*grid-template-columns:\s*28px minmax\(0, 1fr\);/);
    assert.match(rendererStyles, /\.teams-row\.outgoing\s*{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 32px;/);
    assert.match(rendererStyles, /\.avatar\s*{[^}]*border-radius:\s*999px;/);
    assert.match(rendererStyles, /\.teams-message\s*{[^}]*border-bottom-left-radius:\s*4px;/);
    assert.match(rendererStyles, /\.typing-dots span\s*{[^}]*animation:\s*tokenPulse/);
});

test("protocol composite elements are registered before initial render", () => {
    const registrationCall = rendererClientScript.lastIndexOf("defineProtocolTurnElements();");
    assert.ok(registrationCall < rendererClientScript.indexOf("connectStateEvents();"));
    assert.ok(registrationCall < rendererClientScript.indexOf("load().catch"));
});

test("activity details render as an expandable bottom-sheet log", () => {
    assert.match(rendererStyles, /\.activity-log\s*{[^}]*background:\s*transparent;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*grid-template-columns:\s*auto minmax\(0, 1fr\) auto;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*border-radius:\s*6px;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*font-family:\s*var\(--font-mono/);
    assert.match(rendererStyles, /\.activity-head::before\s*{[^}]*content:\s*"▸";/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-head::before\s*{[^}]*display:\s*none;/);
    assert.match(rendererStyles, /\.activity-head > div\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.activity-items\s*{[^}]*border-top:\s*1px solid var\(--cp-border\);/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-head\s*{[^}]*background:\s*var\(--cp-surface-soft\);/);
    assert.match(rendererStyles, /\.activity-log\[open\] \.activity-icon::after\s*{[^}]*content:\s*"Minimize";/);
});

test("header guidance and diagnostics use compact hoverable details", () => {
    assert.match(rendererStyles, /\.action-card\s*{[^}]*grid-template-columns:\s*minmax\(16rem, 1fr\) minmax\(22rem, auto\);/);
    assert.match(rendererStyles, /\.action-summary\s*{[^}]*grid-template-columns:\s*minmax\(0, auto\) minmax\(0, 1fr\);/);
    assert.match(rendererStyles, /\.action-summary\s*{[^}]*align-items:\s*center;/);
    assert.match(rendererStyles, /\.action-state-chip\s*{[^}]*display:\s*inline-flex;/);
    assert.match(rendererClientScript, /guideTitle\.textContent = !connected \? "Connect Foundry project" : "Deploy";/);
    assert.match(rendererClientScript, /guideTitle\.textContent = !connected \? "Connect Foundry project" : "Foundry Agent";/);
    assert.match(rendererClientScript, /setIconButton\(primaryGuideAction,[\s\S]*"upload"[\s\S]*"Deploy"/);
    assert.match(rendererClientScript, /localRunning \? "stop" : "play"/);
    assert.match(rendererClientScript, /localRunning \? "Stop local" : "Start local"/);
    assert.match(rendererClientScript, /primaryGuideAction\.classList\.toggle\("danger"[\s\S]*localRunning/);
    assert.match(rendererClientScript, /if \(activeView === "chat" && latestState\?\.target !== "hosted" && latestState\?\.localRun\?\.running\)[\s\S]*stopLocalFromCanvas/);
    assert.match(rendererClientScript, /button:\s*primaryGuideAction/);
    assert.doesNotMatch(rendererClientScript, /document\.getElementById\("deployButton"\)/);
    assert.doesNotMatch(rendererClientScript, /document\.getElementById\("provisionButton"\)/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.action-card\s*{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) minmax\(12rem, 0\.8fr\);/);
    assert.match(rendererStyles, /@media \(max-width: 520px\)[\s\S]*?\.action-card\s*{[^}]*grid-template-columns:\s*1fr;/);
    assert.match(rendererStyles, /\.action-detail-trigger:hover \.action-copy/);
    assert.match(rendererStyles, /\.status-row\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.local-ticker\[data-detail\]::after/);
    assert.match(rendererClientScript, /function renderActionStateChip/);
    assert.match(rendererClientScript, /setAttribute\("data-detail"/);
    assert.match(rendererStyles, /\.a365-logo\s*{[^}]*opacity:\s*0\.88;/);
    assert.doesNotMatch(rendererStyles, /\.a365-logo\s*{[^}]*width:\s*32px;/);
});

test("composer gate enables local chat when readiness is ok", () => {
    const state = {
        target: "local",
        lastHealth: { ok: true, status: 200 },
        localRun: { running: true },
    };

    assert.deepEqual(composerGate(state), {
        canSend: true,
        inputDisabled: false,
        disabledReason: null,
    });
});

test("local readiness treats healthy endpoint as usable even after a previous managed start failed", () => {
    const state = {
        target: "local",
        lastHealth: { ok: true, status: 200 },
        localRun: {
            running: false,
            exitCode: 1,
            failure: {
                rootCause: "ModuleNotFoundError: No module named 'castia'",
            },
        },
    };

    assert.deepEqual(localReadinessState(state), {
        ready: true,
        reason: null,
    });
    assert.equal(composerGate(state).canSend, true);
});

test("local startup text and failure details expose root cause and uv remediation", () => {
    assert.equal(
        localStartupText({ running: true, readiness: { phase: "dependency_sync" } }),
        "Syncing Python dependencies with uv; first run can take a minute.",
    );
    assert.equal(
        localStartupText({ running: true, readiness: { phase: "polling_readiness" } }),
        "Polling /readiness until the local agent is usable.",
    );

    const localRun = {
        failure: {
            command: "uv run --directory C:\\agent python main.py",
            cwd: "C:\\agent",
            exitCode: 1,
            rootCause: "ModuleNotFoundError: No module named 'castia'",
            suggestion: "Start with uv run --directory C:\\agent python main.py.",
            stderrTail: "ModuleNotFoundError: No module named 'castia'",
        },
    };

    assert.match(localStartupText(localRun), /uv run --directory/);
    assert.match(localFailureDetail(localRun), /Command: uv run --directory/);
    assert.match(localFailureDetail(localRun), /cwd: C:\\agent/);
    assert.match(localFailureDetail(localRun), /exit code: 1/);
    assert.match(localFailureDetail(localRun), /stderr:/);
});

test("composer gate reports precise local readiness states", () => {
    assert.equal(
        composerGate({ target: "local", lastHealth: null, localRun: { running: true } }).disabledReason,
        "Local agent is running; waiting for readiness.",
    );
    assert.equal(
        composerGate({ target: "local", lastHealth: null, localRun: { running: false, exitCode: 1 } }).disabledReason,
        "Local agent stopped. Start local again before chatting.",
    );
    assert.equal(
        composerGate({ target: "local", lastHealth: { ok: false, status: 503, body: "warming" } }).disabledReason,
        "Readiness check failed (503): warming",
    );
});

test("composer gate reports independent readiness failure dimensions", () => {
    assert.equal(
        composerGate({
            target: "local",
            lastHealth: {
                ok: false,
                body: "Endpoint belongs to contract-expert; selected minimal-agent.",
                identity: { expected: "minimal-agent", actual: "contract-expert", status: "mismatch" },
            },
        }).disabledReason,
        "Selected agent does not match endpoint: Endpoint belongs to contract-expert; selected minimal-agent.",
    );
    assert.equal(
        composerGate({
            target: "local",
            lastHealth: {
                ok: false,
                configurationStatus: { status: "missing", missingRequired: ["FOUNDRY_PROJECT_ENDPOINT"] },
            },
        }).disabledReason,
        "Missing required configuration: FOUNDRY_PROJECT_ENDPOINT",
    );
    assert.equal(
        composerGate({
            target: "local",
            lastHealth: {
                ok: false,
                protocolSupport: { status: "missing", missing: ["responses"] },
            },
        }).disabledReason,
        "Missing required protocol support: responses",
    );
});

test("startup readiness failures remain waiting until startup completes", () => {
    const state = {
        target: "local",
        lastHealth: { ok: false, status: 0, body: "fetch failed", source: "startup" },
        localRun: { running: true, readiness: { status: "starting" } },
    };

    assert.deepEqual(localReadinessState(state), {
        ready: false,
        reason: "Local agent is running; waiting for readiness.",
    });
});

test("manual and completed startup readiness failures remain visible", () => {
    assert.match(
        localReadinessState({
            target: "local",
            lastHealth: { ok: false, status: 0, body: "fetch failed", source: "manual" },
            localRun: { running: true, readiness: { status: "starting" } },
        }).reason,
        /Readiness check failed/,
    );
    assert.match(
        localReadinessState({
            target: "local",
            lastHealth: { ok: false, status: 503, body: "identity mismatch", source: "startup" },
            localRun: { running: true, readiness: { status: "failed" } },
        }).reason,
        /identity mismatch/,
    );
});

test("composer gate keeps input enabled while a ready request is in flight", () => {
    const state = {
        target: "local",
        lastHealth: { ok: true, status: 200 },
        localRun: { running: true },
    };

    assert.deepEqual(composerGate(state, { inFlight: true }), {
        canSend: false,
        inputDisabled: false,
        disabledReason: "Waiting for the current response.",
    });
});

test("composer gate blocks hosted chat until a Responses endpoint exists", () => {
    assert.deepEqual(composerGate({ target: "hosted", hosted: {} }), {
        canSend: false,
        inputDisabled: true,
        disabledReason: "Discover or deploy a hosted Responses endpoint before chatting.",
    });
    assert.equal(composerGate({ target: "hosted", hosted: { responsesEndpoint: "https://example.test/responses" } }).canSend, true);
});

test("local readiness ignores prior transcript success for send gating", () => {
    const state = {
        target: "local",
        lastHealth: null,
        localRun: { running: true },
        messages: [{ target: "local", response: { ok: true } }],
    };

    assert.deepEqual(localReadinessState(state), {
        ready: false,
        reason: "Local agent is running; waiting for readiness.",
    });
});

test("local readiness reports stopped agent before stale health can enable send", () => {
    const state = {
        target: "local",
        lastHealth: null,
        localRun: { running: false, exitCode: 1 },
    };

    assert.deepEqual(composerGate(state), {
        canSend: false,
        inputDisabled: true,
        disabledReason: "Local agent stopped. Start local again before chatting.",
    });
});

test("response details keys prefer stable turn identity across response updates", () => {
    assert.equal(
        responseDetailsKey({ response: { id: "resp-1" }, createdAt: "2026-09-19T00:00:00Z", target: "local" }, 3),
        "created:2026-09-19T00:00:00Z:local",
    );
    assert.equal(
        responseDetailsKey({ response: { id: "resp-1" } }, 3),
        "response:resp-1",
    );
});

test("response details panel ids are stable attribute-safe ids", () => {
    assert.equal(
        responseDetailsPanelId("created:2026-09-19T00:00:00Z:hosted"),
        "response-details-created-2026-09-19T00-00-00Z-hosted",
    );
});

test("response details panel ids are stable and attribute-safe", () => {
    assert.equal(responseDetailsPanelId("response:resp-1"), "response-details-response-resp-1");
    assert.equal(
        responseDetailsPanelId("created:2026-09-19T00:00:00Z:hosted"),
        "response-details-created-2026-09-19T00-00-00Z-hosted",
    );
});

test("empty Responses envelopes render as pending instead of JSON answer text", () => {
    assert.deepEqual(responseDisplayState({
        status: "waiting",
        body: { output_text: "" },
    }), {
        answer: "",
        hasAnswer: "",
        waitingForFirstToken: true,
    });
    assert.deepEqual(responseDisplayState({
        status: "waiting",
        body: '{ "output_text": "" }',
    }), {
        answer: "",
        hasAnswer: "",
        waitingForFirstToken: true,
    });
});

test("Responses envelope strings still surface final output text", () => {
    assert.equal(responseText('{ "output_text": "final answer" }'), "final answer");
    assert.equal(responseDisplayState({
        status: 200,
        body: '{ "output_text": "final answer" }',
    }).hasAnswer, "final answer");
});

test("non-envelope JSON strings remain raw diagnostics for markdown/details paths", () => {
    const diagnostics = '{ "trace": "abc123" }';

    assert.equal(responseText(diagnostics), diagnostics);
});

test("answer copy text extracts final answer text", () => {
    assert.equal(copyableAnswerText({ ok: true, body: { output_text: "final answer" } }), "final answer");
});

test("answer copy text does not copy raw JSON details", () => {
    assert.equal(copyableAnswerText({ ok: true, status: 200, body: '{ "trace": "abc123" }' }), "");
});

test("answer copy text skips failed responses with error text", () => {
    assert.equal(copyableAnswerText({ ok: false, status: 500, body: { error: { message: "boom" } } }), "");
});

test("normal Markdown renders without Mermaid treatment", () => {
    const html = renderMarkdown("A **Canvas moment** with `evidence` and [docs](https://example.test).");

    assert.match(html, /<strong>Canvas moment<\/strong>/);
    assert.match(html, /<code>evidence<\/code>/);
    assert.match(html, /<a href="https:\/\/example\.test"/);
    assert.doesNotMatch(html, /mermaid-diagram/);
});

test("valid Mermaid fenced blocks render as inline diagrams", () => {
    const markdown = [
        "Before",
        "```mermaid",
        "flowchart LR",
        '    A["Invoice evidence review"] --> B["Pricing variance + evidence gaps"]',
        '    B --> C["Human reviews dispute scope"]',
        "```",
        "After",
    ].join("\n");

    const html = renderMarkdown(markdown);

    assert.match(html, /<p>Before<\/p>/);
    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /class="mermaid-expand-button"/);
    assert.match(html, /aria-label="Expand diagram"/);
    assert.match(html, /title="Expand diagram"/);
    assert.match(html, />⤢<\/button>/);
    assert.match(html, /<svg role="img"/);
    assert.match(html, /Invoice evidence/);
    assert.match(html, /review/);
    assert.match(html, /Pricing variance \+/);
    assert.match(html, /evidence gaps/);
    assert.match(html, /Human reviews dispute/);
    assert.match(html, /scope/);
    assert.match(html, /<p>After<\/p>/);
});

test("vendored Mermaid path emits a safe browser placeholder when runtime is available", () => {
    const previousRuntime = globalThis.__castiaMermaid;
    globalThis.__castiaMermaid = { initialize() {}, render() {} };
    try {
        const rendered = renderVendoredMermaidBlock([
            "sequenceDiagram",
            "participant User",
            "User->>Agent: Explain status",
        ].join("\n"));

        assert.equal(rendered.ok, true);
        assert.match(rendered.html, /class="mermaid-diagram mermaid-vendor-diagram"/);
        assert.match(rendered.html, /data-mermaid-state="pending"/);
        assert.match(rendered.html, /class="mermaid-expand-button" aria-label="Expand diagram" title="Expand diagram" disabled>⤢<\/button>/);
        assert.match(rendered.html, /sequenceDiagram/);
    } finally {
        if (previousRuntime === undefined) delete globalThis.__castiaMermaid;
        else globalThis.__castiaMermaid = previousRuntime;
    }
});

test("vendored Mermaid contract rejects risky source before browser rendering", () => {
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nA[First<br/>Second] --> B"), {
        ok: true,
        family: "flowchart",
    });
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nA[<b onclick=\"alert(1)\">raw</b>] --> B"), {
        ok: true,
        family: "flowchart",
    });
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nA[<div class=\"card\">raw</div>] --> B"), {
        ok: true,
        family: "flowchart",
    });
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nA[<img src=x onerror=alert(1)>] --> B"), {
        ok: false,
        reason: "unsafe",
        error: "unsupported raw HTML labels are disabled",
    });
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nclick A javascript:alert(1)"), {
        ok: false,
        reason: "unsafe",
        error: "interactive Mermaid links and callbacks are disabled",
    });
    assert.deepEqual(mermaidSourceSupport("mindmap\n  root"), {
        ok: false,
        reason: "unsupported",
        error: "only flowchart, sequence, state, class, and ER Mermaid diagrams are supported",
    });
});

test("Mermaid source sanitizer strips attributes while preserving most HTML labels", () => {
    assert.equal(
        sanitizeMermaidLabelHtml('flowchart LR\nA[<b onclick="alert(1)">Bold</b><span style="color:red">Red</span>] --> B'),
        "flowchart LR\nA[<b>Bold</b><span>Red</span>] --> B"
    );
    assert.equal(
        sanitizeMermaidLabelHtml('flowchart LR\nA[<div class="card" data-x="1">Block</div>] --> B'),
        "flowchart LR\nA[<div>Block</div>] --> B"
    );
    assert.equal(
        sanitizeMermaidLabelHtml('flowchart LR\nA[<img src="x" onerror="alert(1)">] --> B'),
        'flowchart LR\nA[<img src="x" onerror="alert(1)">] --> B'
    );
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[First<br>Second] --> B"), false);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[First<br/>Second] --> B"), false);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[First<br />Second] --> B"), false);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[<b onclick=\"alert(1)\">raw</b>] --> B"), false);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[<table><tr><td>raw</td></tr></table>] --> B"), false);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[<img src=x onerror=alert(1)>] --> B"), true);
    assert.equal(hasRawHtmlMermaidLabel("flowchart LR\nA[<script>alert(1)</script>] --> B"), true);
});

test("vendored Mermaid sanitizer allows static CSS but rejects URL-capable CSS", () => {
    assert.equal(hasUnsafeSvgCss(".node rect { fill: currentColor; }"), false);
    assert.equal(hasUnsafeSvgCss("path { marker-end: url(#flowchart-pointEnd); }"), false);
    assert.equal(hasUnsafeSvgCss("path { fill: url(https://example.test/pattern.svg); }"), true);
    assert.equal(hasUnsafeSvgCss("@import 'https://example.test/style.css';"), true);
    assert.equal(hasUnsafeSvgCss("rect { background: javascript:alert(1); }"), true);
});

test("unsafe Mermaid blocks fall back to escaped source instead of a diagram", () => {
    const html = renderMermaidBlock("flowchart LR\nA[<img src=x onerror=alert(1)>] --> B");

    assert.match(html, /class="mermaid-fallback"/);
    assert.match(html, /unsupported raw HTML labels are disabled/);
    assert.match(html, /A\[&lt;img src=x onerror=alert\(1\)&gt;\] --&gt; B/);
    assert.doesNotMatch(html, /<img/);
    assert.doesNotMatch(html, /class="mermaid-diagram"/);
    assert.doesNotMatch(html, /mermaid-expand-button/);
});

test("unterminated Mermaid fences do not hydrate while streaming", () => {
    const html = renderMarkdown([
        "```mermaid",
        "sequenceDiagram",
        "User->>Agent: still streaming",
    ].join("\n"));

    assert.match(html, /class="mermaid-fallback"/);
    assert.match(html, /Mermaid diagram is still streaming/);
    assert.doesNotMatch(html, /mermaid-vendor-diagram/);
    assert.doesNotMatch(html, /data-mermaid-state="pending"/);
});

test("Mermaid dotted edges render as dashed inline diagrams", () => {
    const html = renderMermaidBlock([
        "flowchart LR",
        'A["Detected"] -.-> B["Triaged"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /class="mermaid-edge mermaid-edge-dotted"/);
    assert.match(html, /Detected/);
    assert.match(html, /Triaged/);
});

test("Mermaid labeled dotted edges render with escaped labels", () => {
    const html = renderMermaidBlock([
        "flowchart TD",
        'H["Human handoff"] -. "Human-controlled <downstream> context only" .-> R["Detected \u2192 Triaged \u2192 Assigned"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /class="mermaid-edge mermaid-edge-dotted"/);
    assert.match(html, /Human-controlled &lt;downstream&gt; context only/);
    assert.doesNotMatch(html, /Human-controlled <downstream>/);
    assert.match(html, /Detected \u2192/);
});

test("Mermaid plain solid edge labels render with escaped labels", () => {
    const html = renderMermaidBlock([
        "flowchart LR",
        'A["Invoice"] -- needs <review> --> B["Human"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /needs &lt;review&gt;/);
    assert.doesNotMatch(html, /needs <review>/);
});

test("Mermaid safe flowchart renderer turns br labels into SVG line breaks", () => {
    const html = renderMermaidBlock([
        "flowchart LR",
        'A["Invoice<br/>Evidence"] --> B["Human<br>Review"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /Invoice/);
    assert.match(html, /Evidence/);
    assert.match(html, /Human/);
    assert.match(html, /Review/);
    assert.doesNotMatch(html, /&lt;br/);
    assert.equal((html.match(/<tspan /g) || []).length, 4);
});

test("Mermaid safe flowchart fallback strips sanitized label HTML tags to text", () => {
    const html = renderMermaidBlock([
        "flowchart LR",
        'A["<b onclick=\\"alert(1)\\">Invoice</b> <div class=\\"x\\">Evidence</div>"] --> B["Human"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /Invoice Evidence/);
    assert.doesNotMatch(html, /&lt;b/);
    assert.doesNotMatch(html, /onclick/);
    assert.doesNotMatch(html, /class=&quot;x&quot;/);
});

test("Mermaid chained solid edges render as bounded individual edges", () => {
    const html = renderMermaidBlock([
        "flowchart LR",
        'A["Detected"] --> B["Triaged"] --> C["Assigned"]',
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /Detected/);
    assert.match(html, /Triaged/);
    assert.match(html, /Assigned/);
    assert.equal((html.match(/class="mermaid-edge"/g) || []).length, 2);
});

test("Mermaid subgraph grouping syntax is accepted without unsafe rendering", () => {
    const html = renderMermaidBlock([
        "flowchart TD",
        "subgraph Human review",
        'A["Detected"] --> B["Assigned"]',
        "end",
    ].join("\n"));

    assert.match(html, /class="mermaid-diagram"/);
    assert.match(html, /Detected/);
    assert.match(html, /Assigned/);
    assert.doesNotMatch(html, /Human review/);
});

test("malformed Mermaid fenced blocks fall back to source with an error", () => {
    const html = renderMermaidBlock("flowchart LR\nA -->\n");

    assert.match(html, /class="mermaid-fallback"/);
    assert.match(html, /Mermaid diagram could not be rendered/);
    assert.match(html, /unsupported Mermaid syntax/);
    assert.match(html, /A --&gt;/);
});

test("unsupported Mermaid edge syntax still falls back to source with an error", () => {
    const html = renderMermaidBlock("flowchart LR\nA ==> B\n");

    assert.match(html, /class="mermaid-fallback"/);
    assert.match(html, /unsupported Mermaid syntax/);
    assert.match(html, /A ==&gt; B/);
});

test("answer copy helpers preserve original Markdown fences", () => {
    const answer = [
        "Canvas moment",
        "```mermaid",
        "flowchart LR",
        "A --> B",
        "```",
    ].join("\n");

    assert.equal(copyableAnswerText({ ok: true, body: { output_text: answer } }), answer);
});

test("renderer script defines activity rendering at top level", () => {
    const tickerIndex = rendererClientScript.indexOf("function renderLocalTicker");
    const activityIndex = rendererClientScript.indexOf("function renderActivity");
    const elapsedIndex = rendererClientScript.indexOf("function elapsedLabel");

    assert.ok(tickerIndex > 0);
    assert.ok(activityIndex > tickerIndex);
    assert.ok(activityIndex < elapsedIndex);
});

test("renderer script includes code block registry before markdown rendering", () => {
    const registryIndex = rendererClientScript.indexOf("const codeBlockRenderers = [");
    const markdownIndex = rendererClientScript.indexOf("function renderMarkdown");
    const codeBlockIndex = rendererClientScript.indexOf("function renderCodeBlock");

    assert.ok(registryIndex > 0);
    assert.ok(registryIndex < markdownIndex);
    assert.ok(registryIndex < codeBlockIndex);
    assert.match(rendererClientScript, /id: "mermaid"/);
    assert.match(rendererClientScript, /id: "json"/);
});

test("renderer script removes Mermaid render artifacts after failed browser renders", () => {
    assert.match(rendererClientScript, /function removeMermaidRenderArtifacts/);
    assert.match(rendererClientScript, /getElementById\("d" \+ renderId\)\?\.remove\(\)/);
    assert.match(rendererClientScript, /removeMermaidRenderArtifacts\(renderId\)/);
});

test("renderer script wires panel-local Mermaid diagram expansion", () => {
    assert.match(rendererClientScript, /function openMermaidDiagramLightbox/);
    assert.match(rendererClientScript, /role="dialog" aria-modal="true" aria-label="Expanded Mermaid diagram"/);
    assert.match(rendererClientScript, /Drag to pan · Wheel to zoom/);
    assert.match(rendererClientScript, /aria-label="Close expanded diagram"/);
    assert.match(rendererClientScript, /aria-label="Zoom in"/);
    assert.match(rendererClientScript, /aria-label="Zoom out"/);
    assert.match(rendererClientScript, /function zoomMermaidLightbox/);
    assert.match(rendererClientScript, /function fitMermaidLightboxToViewport/);
    assert.match(rendererClientScript, /function mermaidSvgContentBox/);
    assert.match(rendererClientScript, /Math\.exp\(-event\.deltaY \* 0\.0015\)/);
    assert.doesNotMatch(rendererClientScript, /space-pan/);
    assert.match(rendererClientScript, /trigger\?\.isConnected\) trigger\.focus\(\)/);
    assert.match(rendererClientScript, /event\.key === "Escape" && activeMermaidLightbox/);
    assert.match(rendererClientScript, /contains\("mermaid-lightbox-backdrop"\)/);
    assert.match(rendererClientScript, /closest\("\.mermaid-expand-button"\)/);
});

test("renderer client script remains syntactically valid after function assembly", () => {
    assert.doesNotThrow(() => new Function(rendererClientScript));
});

test("renderer subscribes to canvas action state updates", () => {
    assert.match(rendererClientScript, /new EventSource\("\/api\/events"\)/);
    assert.match(rendererClientScript, /addEventListener\("snapshot"/);
});

test("renderer wires protocol toggle once outside the send loop", () => {
    const matches = rendererClientScript.match(/protocolToggle\.addEventListener\("click"/g) || [];
    assert.equal(matches.length, 1);
    assert.ok(rendererClientScript.indexOf('protocolToggle.addEventListener("click"') > rendererClientScript.indexOf('sendButton.addEventListener("click"'));
});

test("renderer wires operation cancellation through shared route", () => {
    assert.match(rendererClientScript, /cancelOperationButton/);
    assert.match(rendererClientScript, /function cancelOperationFromCanvas/);
    assert.match(rendererClientScript, /\/api\/operation\/cancel/);
});

test("project endpoint prompt state opens and closes the endpoint dialog", () => {
    assert.equal(shouldOpenProjectEndpointDialog({ projectEndpointPrompt: { open: true } }), true);
    assert.equal(shouldOpenProjectEndpointDialog({ projectEndpointPrompt: null }), false);
    assert.match(rendererClientScript, /shouldOpenProjectEndpointDialog\(state\).*showProjectEndpointDialog/s);
    assert.match(rendererClientScript, /!shouldOpenProjectEndpointDialog\(state\).*hideProjectEndpointDialog/s);
});

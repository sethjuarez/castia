import assert from "node:assert/strict";
import { test } from "node:test";
import {
    composerGate,
    latestVisibleAnswerText,
    localReadinessState,
    rendererClientScript,
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

test("empty transcript state fills the available transcript space", () => {
    assert.doesNotMatch(rendererStyles, /\.panel:has\(\.transcript\.empty-state\)/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*height:\s*100%;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*overflow:\s*auto;/);
    assert.match(rendererStyles, /\.empty\s*{[^}]*gap:\s*6px;/);
    assert.match(rendererStyles, /\.empty\s*{[^}]*min-height:\s*100%;/);
});

test("activity details render as a compact terminal log disclosure", () => {
    assert.match(rendererStyles, /\.activity-log\s*{[^}]*background:\s*transparent;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*grid-template-columns:\s*auto minmax\(0, 1fr\) auto;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*border-radius:\s*6px;/);
    assert.match(rendererStyles, /\.activity-head\s*{[^}]*font-family:\s*var\(--font-mono/);
    assert.match(rendererStyles, /\.activity-head::before\s*{[^}]*content:\s*"▸";/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s+\.activity-head::before\s*{[^}]*content:\s*"▾";/);
    assert.match(rendererStyles, /\.activity-head > div\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.activity-items\s*{[^}]*border-top:\s*1px solid var\(--cp-border\);/);
    assert.match(rendererStyles, /\.activity-log\[open\]\s+\.activity-head\s*{[^}]*background:\s*transparent;/);
});

test("header guidance and diagnostics use compact hoverable details", () => {
    assert.match(rendererStyles, /\.action-card\s*{[^}]*grid-template-columns:\s*minmax\(16rem, 1fr\) minmax\(22rem, auto\);/);
    assert.match(rendererStyles, /\.action-summary\s*{[^}]*grid-template-columns:\s*minmax\(0, auto\) minmax\(0, 1fr\);/);
    assert.match(rendererStyles, /\.action-summary\s*{[^}]*align-items:\s*center;/);
    assert.match(rendererStyles, /\.action-state-chip\s*{[^}]*display:\s*inline-flex;/);
    assert.match(rendererClientScript, /guideTitle\.textContent = !connected \? "Connect Foundry project" : "Deploy";/);
    assert.match(rendererClientScript, /guideTitle\.textContent = !connected \? "Connect Foundry project" : "Foundry Agent";/);
    assert.match(rendererClientScript, /deployButton\.textContent = "Deploy";/);
    assert.match(rendererStyles, /@media \(max-width: 820px\)[\s\S]*?\.action-card\s*{[^}]*grid-template-columns:\s*minmax\(12rem, 1fr\) minmax\(16rem, 0\.9fr\);/);
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

test("latest visible answer text skips pending envelopes and copies latest final answer", () => {
    assert.equal(latestVisibleAnswerText([
        { response: { ok: true, body: { output_text: "first answer" } } },
        { response: { ok: false, status: "waiting", body: '{ "output_text": "" }' } },
        { response: { ok: true, body: { output_text: "final answer" } } },
    ]), "final answer");
});

test("latest visible answer text does not copy raw JSON details", () => {
    assert.equal(latestVisibleAnswerText([
        { response: { ok: true, body: { output_text: "first answer" } } },
        { response: { ok: true, status: 200, body: '{ "trace": "abc123" }' } },
    ]), "first answer");
});

test("latest visible answer text skips failed responses with error text", () => {
    assert.equal(latestVisibleAnswerText([
        { response: { ok: true, body: { output_text: "first answer" } } },
        { response: { ok: false, status: 500, body: { error: { message: "boom" } } } },
    ]), "first answer");
});

test("renderer script defines activity rendering at top level", () => {
    const tickerIndex = rendererClientScript.indexOf("function renderLocalTicker");
    const activityIndex = rendererClientScript.indexOf("function renderActivity");
    const elapsedIndex = rendererClientScript.indexOf("function elapsedLabel");

    assert.ok(tickerIndex > 0);
    assert.ok(activityIndex > tickerIndex);
    assert.ok(activityIndex < elapsedIndex);
});

test("renderer subscribes to canvas action state updates", () => {
    assert.match(rendererClientScript, /new EventSource\("\/api\/events"\)/);
    assert.match(rendererClientScript, /addEventListener\("snapshot"/);
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

import assert from "node:assert/strict";
import { test } from "node:test";
import {
    composerGate,
    copyableAnswerText,
    localFailureDetail,
    localReadinessState,
    localStartupText,
    hasUnsafeSvgCss,
    mermaidSourceSupport,
    rendererClientScript,
    renderMarkdown,
    renderMermaidBlock,
    renderVendoredMermaidBlock,
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
});

test("empty transcript state fills the available transcript space", () => {
    assert.doesNotMatch(rendererStyles, /\.panel:has\(\.transcript\.empty-state\)/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*display:\s*flex;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*height:\s*100%;/);
    assert.match(rendererStyles, /\.transcript\.empty-state\s*{[^}]*overflow:\s*auto;/);
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
        assert.match(rendered.html, /sequenceDiagram/);
    } finally {
        if (previousRuntime === undefined) delete globalThis.__castiaMermaid;
        else globalThis.__castiaMermaid = previousRuntime;
    }
});

test("vendored Mermaid contract rejects risky source before browser rendering", () => {
    assert.deepEqual(mermaidSourceSupport("flowchart LR\nA[<b>raw</b>] --> B"), {
        ok: false,
        reason: "unsafe",
        error: "raw HTML labels are disabled",
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

test("vendored Mermaid sanitizer allows static CSS but rejects URL-capable CSS", () => {
    assert.equal(hasUnsafeSvgCss(".node rect { fill: currentColor; }"), false);
    assert.equal(hasUnsafeSvgCss("path { fill: url(https://example.test/pattern.svg); }"), true);
    assert.equal(hasUnsafeSvgCss("@import 'https://example.test/style.css';"), true);
    assert.equal(hasUnsafeSvgCss("rect { background: javascript:alert(1); }"), true);
});

test("unsafe Mermaid blocks fall back to escaped source instead of a diagram", () => {
    const html = renderMermaidBlock("flowchart LR\nA[<b>raw</b>] --> B");

    assert.match(html, /class="mermaid-fallback"/);
    assert.match(html, /raw HTML labels are disabled/);
    assert.match(html, /A\[&lt;b&gt;raw&lt;\/b&gt;\] --&gt; B/);
    assert.doesNotMatch(html, /<b>raw<\/b>/);
    assert.doesNotMatch(html, /class="mermaid-diagram"/);
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

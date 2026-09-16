import { createServer } from "node:http";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8088";
const servers = new Map();

function normalizeEndpoint(value) {
    const endpoint = String(value || DEFAULT_ENDPOINT).trim().replace(/\/+$/, "");
    if (!/^https?:\/\/[^/\s]+/i.test(endpoint)) {
        throw new CanvasError("invalid_endpoint", "Endpoint must be an http(s) URL.");
    }
    return endpoint;
}

function instanceState(ctx) {
    const entry = servers.get(ctx.instanceId);
    if (!entry) {
        throw new CanvasError("instance_not_open", "Open the canvas before invoking actions.");
    }
    return entry.state;
}

async function readBody(req) {
    const chunks = [];
    for await (const chunk of req) {
        chunks.push(chunk);
    }
    if (chunks.length === 0) {
        return {};
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function sendJson(res, status, payload) {
    res.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(JSON.stringify(payload));
}

function sendHtml(res, html) {
    res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
    });
    res.end(html);
}

function sendNoContent(res) {
    res.writeHead(204, { "Cache-Control": "no-store" });
    res.end();
}

function responseText(body) {
    if (body && typeof body === "object") {
        return body.output_text ?? body.output ?? JSON.stringify(body, null, 2);
    }
    return body ?? "";
}

function transcriptStats(messages) {
    const completed = messages.filter((message) => message.response?.ok);
    const failed = messages.filter(
        (message) => message.response && !message.response.streaming && !message.response.ok,
    );
    const latencies = messages
        .map((message) => message.response?.durationMs)
        .filter((value) => Number.isFinite(value));
    const averageMs = latencies.length
        ? Math.round(latencies.reduce((sum, value) => sum + value, 0) / latencies.length)
        : 0;
    return {
        total: messages.length,
        completed: completed.length,
        failed: failed.length,
        averageMs,
        lastStatus: messages.at(-1)?.response?.status ?? null,
    };
}

async function callAgentStream(endpoint, payload, onDelta) {
    const started = Date.now();
    try {
        const response = await fetch(`${endpoint}/responses`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Accept: "text/event-stream",
            },
            body: JSON.stringify({ ...payload, stream: true }),
            signal: AbortSignal.timeout(60000),
        });
        const contentType = response.headers.get("content-type") || "";
        if (!response.ok || !contentType.includes("text/event-stream") || !response.body) {
            const text = await response.text();
            let body = text;
            try {
                body = text ? JSON.parse(text) : null;
            } catch {
                // Keep non-JSON error bodies readable in the tester.
            }
            return {
                ok: response.ok,
                status: response.status,
                durationMs: Date.now() - started,
                body,
                delivery: {
                    mode: contentType.includes("text/event-stream") ? "upstream-stream" : "single-response",
                    upstreamStreaming: contentType.includes("text/event-stream"),
                    active: false,
                },
            };
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let outputText = "";
        let completedBody = null;

        const handlePart = (part) => {
            const lines = part.split("\n");
            const eventLine = lines.find((line) => line.startsWith("event: "));
            const eventName = eventLine ? eventLine.slice(7).trim() : "message";
            const data = lines
                .filter((line) => line.startsWith("data: "))
                .map((line) => line.slice(6))
                .join("\n");
            if (!data) return;
            const payload = JSON.parse(data);
            if (eventName === "response.output_text.delta") {
                const delta = String(payload.delta || "");
                if (delta) {
                    outputText += delta;
                    onDelta(delta, outputText, Date.now() - started);
                }
            } else if (eventName === "response.completed") {
                completedBody = payload;
            }
        };

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split("\n\n");
            buffer = parts.pop() || "";
            for (const part of parts) {
                handlePart(part);
            }
        }
        buffer += decoder.decode();
        if (buffer.trim()) {
            handlePart(buffer);
        }

        return {
            ok: true,
            status: response.status,
            durationMs: Date.now() - started,
            body: completedBody || { output_text: outputText },
            delivery: {
                mode: "upstream-stream",
                upstreamStreaming: true,
                active: false,
            },
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
            delivery: {
                mode: "error",
                upstreamStreaming: false,
                active: false,
            },
        };
    }
}

function writeEvent(res, name, payload) {
    res.write(`event: ${name}\n`);
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function callAgent(endpoint, path, payload) {
    const started = Date.now();
    try {
        const response = await fetch(`${endpoint}${path}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: AbortSignal.timeout(60000),
        });
        const text = await response.text();
        let body = text;
        try {
            body = text ? JSON.parse(text) : null;
        } catch {
            // Keep non-JSON error bodies readable in the tester.
        }
        return {
            ok: response.ok,
            status: response.status,
            durationMs: Date.now() - started,
            body,
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
        };
    }
}

async function checkReadiness(endpoint) {
    const started = Date.now();
    try {
        const response = await fetch(`${endpoint}/readiness`, {
            signal: AbortSignal.timeout(10000),
        });
        const text = await response.text();
        return {
            ok: response.ok,
            status: response.status,
            durationMs: Date.now() - started,
            body: text,
        };
    } catch (error) {
        return {
            ok: false,
            status: 0,
            durationMs: Date.now() - started,
            body: error instanceof Error ? error.message : String(error),
        };
    }
}

function stateSnapshot(state) {
    return {
        endpoint: state.endpoint,
        messages: state.messages,
        lastHealth: state.lastHealth,
        stats: transcriptStats(state.messages),
    };
}

function renderHtml() {
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Agent Playground</title>
  <script>
    (() => {
      const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
      const theme =
        param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      document.documentElement.setAttribute("data-theme", theme);
    })();
  </script>
  <style>
    :root {
      color-scheme: light;
      --cp-bg: #f7f4ef;
      --cp-bg-elevated: #fcfbf8;
      --cp-surface: #ffffff;
      --cp-surface-soft: #f5f5f5;
      --cp-border: #dedede;
      --cp-border-strong: #919191;
      --cp-text: #242424;
      --cp-text-muted: #5c5c5c;
      --cp-text-soft: #6f6f6f;
      --cp-accent: #b11f4b;
      --cp-accent-hover: #9a1a41;
      --cp-accent-soft: rgba(177, 31, 75, 0.08);
      --cp-accent-fg: #ffffff;
      --cp-success: #16a34a;
      --cp-danger: #dc2626;
      --cp-warning: #f59e0b;
      --cp-link: #0078d4;
      --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.12);
      --cp-overlay: rgba(255, 255, 255, 0.8);
      --cp-panel: rgba(255, 255, 255, 0.86);
      --cp-panel-strong: rgba(255, 255, 255, 0.96);
      --cp-sheen: rgba(255, 255, 255, 0.55);
      --cp-highlight: rgba(177, 31, 75, 0.12);
    }
    html[data-theme="dark"] {
      color-scheme: dark;
      --cp-bg: #3d3b3a;
      --cp-bg-elevated: #343231;
      --cp-surface: #292929;
      --cp-surface-soft: #2e2e2e;
      --cp-border: #474747;
      --cp-border-strong: #5f5f5f;
      --cp-text: #dedede;
      --cp-text-muted: #919191;
      --cp-text-soft: #b0b0b0;
      --cp-accent: #fd8ea1;
      --cp-accent-hover: #fb7b91;
      --cp-accent-soft: rgba(253, 142, 161, 0.14);
      --cp-accent-fg: #1a1a1a;
      --cp-success: #4ade80;
      --cp-danger: #f87171;
      --cp-warning: #fbbf24;
      --cp-link: #4da6ff;
      --cp-shadow: 0 18px 48px rgba(0, 0, 0, 0.32);
      --cp-overlay: rgba(41, 41, 41, 0.88);
      --cp-panel: rgba(41, 41, 41, 0.72);
      --cp-panel-strong: rgba(41, 41, 41, 0.96);
      --cp-sheen: rgba(255, 255, 255, 0.04);
      --cp-highlight: rgba(253, 142, 161, 0.12);
    }
    :root,
    html[data-theme="dark"] {
      color-scheme: light dark;
      --cp-bg: var(--background-color-default, #ffffff);
      --cp-bg-elevated: var(--background-color-default, #ffffff);
      --cp-surface: var(--background-color-default, #ffffff);
      --cp-surface-soft: var(--background-color-subtle, #f5f5f5);
      --cp-border: var(--border-color-default, #dedede);
      --cp-border-strong: var(--border-color-muted, var(--border-color-default, #919191));
      --cp-text: var(--text-color-default, #242424);
      --cp-text-muted: var(--text-color-muted, #5c5c5c);
      --cp-text-soft: var(--text-color-muted, #6f6f6f);
      --cp-accent: var(--color-focus-outline, var(--cp-link));
      --cp-accent-hover: var(--color-focus-outline, var(--cp-link));
      --cp-accent-soft: color-mix(in srgb, var(--cp-accent) 10%, var(--cp-surface));
      --cp-accent-fg: var(--color-white, #ffffff);
      --cp-success: var(--true-color-green, #16a34a);
      --cp-danger: var(--true-color-red, #dc2626);
      --cp-warning: var(--true-color-yellow, #f59e0b);
      --cp-link: var(--true-color-blue, var(--color-focus-outline, #0078d4));
      --cp-shadow: 0 0 2px rgba(0, 0, 0, 0.12), 0 1px 2px rgba(0, 0, 0, 0.14);
      --cp-overlay: var(--background-color-default, #ffffff);
      --cp-panel: var(--background-color-default, #ffffff);
      --cp-panel-strong: var(--background-color-default, #ffffff);
      --cp-sheen: var(--background-color-subtle, #f5f5f5);
      --cp-highlight: var(--background-color-subtle, #f5f5f5);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; overflow: hidden; }
    html {
      scrollbar-color: var(--cp-border-strong) var(--cp-bg);
    }
    ::-webkit-scrollbar {
      width: 12px;
      height: 12px;
    }
    ::-webkit-scrollbar-track {
      background: var(--cp-bg);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--cp-border-strong);
      border: 3px solid var(--cp-bg);
      border-radius: 999px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--cp-text-muted);
    }
    body {
      margin: 0;
      background: var(--cp-bg);
      color: var(--cp-text);
      font-family: var(--font-sans, "Segoe UI", Aptos, Calibri, -apple-system, BlinkMacSystemFont, sans-serif);
      font-size: var(--text-body-medium, 14px);
      line-height: var(--leading-body-medium, 20px);
    }
    button, input, textarea { font: inherit; }
    button {
      border: 1px solid transparent;
      border-radius: 0.625rem;
      padding: 8px 12px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { border-color: var(--cp-border); }
    button:disabled { cursor: not-allowed; opacity: 0.62; }
    button.primary {
      border-color: var(--cp-accent);
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    button.primary:hover { background: var(--cp-accent-hover); }
    input, textarea {
      width: 100%;
      border: 1px solid var(--cp-border);
      border-radius: 0.625rem;
      padding: 10px 12px;
      background: var(--cp-surface);
      color: var(--cp-text);
    }
    textarea { min-height: 108px; resize: none; }
    input:focus, textarea:focus, button:focus {
      outline: 2px solid var(--cp-accent);
      outline-offset: 2px;
    }
    code, pre {
      font-family: var(--font-mono, Consolas, "Courier New", Courier, monospace);
      font-size: var(--text-code-inline, 12px);
    }
    .app {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100dvh;
      overflow: hidden;
    }
    .hero {
      padding: 8px 12px;
      background: var(--cp-bg-elevated);
    }
    .endpoint-card {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 8px;
      align-items: center;
    }
    .content {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: minmax(0, 1fr);
      gap: 8px;
      min-height: 0;
      padding: 8px 12px;
      overflow: hidden;
    }
    .panel {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-height: 0;
      background: var(--cp-panel);
      overflow: hidden;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 4px 10px;
      background: var(--cp-panel-strong);
    }
    .panel-title {
      font-weight: 700;
    }
    .panel-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 4px;
    }
    .meta-pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 0;
      background: transparent;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .muted { color: var(--cp-text-muted); }
    .health {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--cp-text-muted);
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--cp-warning);
    }
    .dot.ok { background: var(--cp-success); }
    .dot.fail { background: var(--cp-danger); }
    .transcript {
      height: 100%;
      overflow: auto;
      padding: 8px 2px 16px;
    }
    .empty {
      display: grid;
      place-items: center;
      min-height: 100%;
      border-radius: 16px;
      color: var(--cp-text-muted);
      text-align: center;
      padding: 24px;
      background: var(--cp-surface-soft);
    }
    .turn {
      display: grid;
      gap: 12px;
      margin-bottom: 12px;
    }
    .bubble {
      border-radius: 16px;
      background: var(--cp-surface);
      overflow: hidden;
      box-shadow: var(--cp-shadow);
    }
    .bubble.user {
      justify-self: end;
      width: min(calc(100% - 48px), 1120px);
      max-width: calc(100% - 48px);
      background: var(--cp-surface-soft);
    }
    .bubble.agent {
      justify-self: start;
      width: min(calc(100% - 48px), 1120px);
      max-width: calc(100% - 48px);
      background: var(--cp-accent-soft);
    }
    .bubble-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px 0;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .speaker {
      letter-spacing: 0.01em;
    }
    .speaker.user {
      color: var(--cp-text-muted);
    }
    .speaker.agent {
      color: var(--cp-accent);
      font-weight: 700;
    }
    .bubble-body {
      padding: 12px;
      white-space: pre-wrap;
    }
    .first-token {
      display: inline-flex;
      align-items: center;
      color: var(--cp-text-muted);
    }
    .token-dots {
      display: inline-flex;
      gap: 3px;
    }
    .token-dots span {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: var(--cp-text-muted);
      animation: tokenPulse 1.1s ease-in-out infinite;
      opacity: 0.45;
    }
    .token-dots span:nth-child(2) { animation-delay: 0.15s; }
    .token-dots span:nth-child(3) { animation-delay: 0.3s; }
    @keyframes tokenPulse {
      0%, 80%, 100% { transform: translateY(0); opacity: 0.35; }
      40% { transform: translateY(-3px); opacity: 1; }
    }
    details {
      padding: 0 12px 10px;
    }
    summary {
      cursor: pointer;
      color: var(--cp-link);
      font-size: 12px;
      font-weight: 500;
      list-style-position: inside;
    }
    pre {
      max-height: 240px;
      overflow: auto;
      margin: 8px 0 0;
      padding: 10px;
      border-radius: 0.625rem;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 0;
      background: transparent;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .badge.ok {
      background: transparent;
      color: var(--cp-success);
    }
    .badge.fail {
      background: transparent;
      color: var(--cp-danger);
    }
    .composer {
      display: grid;
      gap: 10px;
      padding: 10px 12px 12px;
      background: var(--cp-bg-elevated);
    }
    .composer-actions {
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .right-actions {
      display: flex;
      gap: 8px;
    }
    @media (max-width: 820px) {
      .endpoint-card,
      .content {
        grid-template-columns: 1fr;
      }
      .bubble.user,
      .bubble.agent {
        margin-left: 0;
        margin-right: 0;
        max-width: calc(100% - 24px);
        width: calc(100% - 24px);
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <section class="hero">
      <div class="endpoint-card">
        <input id="endpoint" aria-label="Agent endpoint" spellcheck="false" placeholder="http://127.0.0.1:8088" />
        <button id="saveEndpoint" type="button">Save endpoint</button>
        <button id="checkHealth" type="button">Check readiness</button>
      </div>
    </section>
    <main class="content">
      <section class="panel">
        <div class="panel-header">
          <div>
            <div class="panel-title">Transcript</div>
            <div class="panel-meta">
              <span class="meta-pill" id="turnCount">0 turns</span>
              <span class="meta-pill" id="passCount">0 pass</span>
              <span class="meta-pill" id="failCount">0 fail</span>
              <span class="meta-pill" id="avgLatency">0ms avg</span>
            </div>
          </div>
          <div class="health"><span id="statusDot" class="dot"></span><span id="statusText">Not checked yet.</span></div>
        </div>
        <div id="transcript" class="transcript">
          <div class="empty">Send a prompt to test <code>POST /responses</code>.</div>
        </div>
      </section>
    </main>
    <section id="composer" class="composer">
      <textarea id="prompt" placeholder="Ask the agent something... Enter sends, Shift+Enter adds a line." required></textarea>
      <div class="composer-actions">
        <button id="clear" type="button">Clear transcript</button>
        <div class="right-actions">
          <button class="primary" id="send" type="button">Send</button>
        </div>
      </div>
    </section>
  </div>
  <script>
    const endpointInput = document.getElementById("endpoint");
    const saveEndpoint = document.getElementById("saveEndpoint");
    const checkHealthButton = document.getElementById("checkHealth");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const turnCount = document.getElementById("turnCount");
    const passCount = document.getElementById("passCount");
    const failCount = document.getElementById("failCount");
    const avgLatency = document.getElementById("avgLatency");
    const transcript = document.getElementById("transcript");
    const promptInput = document.getElementById("prompt");
    const sendButton = document.getElementById("send");
    const clearButton = document.getElementById("clear");
    let inFlight = false;
    let lastSend = { input: "", at: 0 };

    function setStatus(kind, text) {
      statusDot.className = "dot " + (kind || "");
      statusText.textContent = text;
    }

    function renderSnapshot(state) {
      endpointInput.value = state.endpoint;
      turnCount.textContent = state.stats.total + " turns";
      passCount.textContent = state.stats.completed + " pass";
      failCount.textContent = state.stats.failed + " fail";
      avgLatency.textContent = state.stats.averageMs + "ms avg";
      renderMessages(state.messages || []);
      if (state.lastHealth) {
        setStatus(state.lastHealth.ok ? "ok" : "fail", "Readiness " + state.lastHealth.status + " in " + state.lastHealth.durationMs + "ms");
      }
    }

    function renderMessages(messages) {
      if (!messages.length) {
        transcript.innerHTML = '<div class="empty">Send a prompt to test <code>POST /responses</code>.</div>';
        return;
      }
      transcript.innerHTML = messages.map((turn, index) => {
        const ok = turn.response?.ok;
        const streaming = turn.response?.streaming;
        const answer = responseText(turn.response?.body);
        const waitingForFirstToken = streaming && !String(answer || "").trim();
        const activeLabel = turn.response?.delivery?.upstreamStreaming ? "streaming" : "waiting";
        const body = waitingForFirstToken
          ? '<div class="first-token" aria-label="Waiting for response"><span class="token-dots" aria-hidden="true"><span></span><span></span><span></span></span></div>'
          : renderMarkdown(answer);
        return '<article class="turn">' +
          '<section class="bubble user">' +
          '<div class="bubble-head"><span class="speaker user">You</span><span>' + escapeHtml(formatTime(turn.createdAt)) + '</span></div>' +
          '<div class="bubble-body">' + escapeHtml(turn.input) + '</div>' +
          '</section>' +
          '<section class="bubble agent">' +
          '<div class="bubble-head"><span class="speaker agent">Agent</span><span class="badge ' + (streaming ? "" : ok ? "ok" : "fail") + '">' + escapeHtml(streaming ? activeLabel : String(turn.response?.status ?? "error")) + ' · ' + escapeHtml(String(turn.response?.durationMs ?? 0)) + 'ms</span></div>' +
          '<div class="bubble-body">' + body + '</div>' +
          '<details><summary>Details</summary><pre>' + escapeHtml(JSON.stringify(turn, null, 2)) + '</pre></details>' +
          '</section>' +
          '</article>';
      }).join("");
      transcript.scrollTop = transcript.scrollHeight;
    }

    async function sendPrompt() {
      if (inFlight) return;
      const input = promptInput.value.trim();
      if (!input) return;
      const now = Date.now();
      if (lastSend.input === input && now - lastSend.at < 1500) return;
      lastSend = { input, at: now };
      inFlight = true;
      sendButton.disabled = true;
      setStatus("", "Sending prompt...");
      try {
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
              setStatus("", hasText && latest.response.delivery?.upstreamStreaming ? "Streaming response..." : "Waiting for response...");
            } else if (latest?.response) {
              setStatus(latest.response.ok ? "ok" : "fail", "Response " + latest.response.status + " in " + latest.response.durationMs + "ms");
            }
          }
        }
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        inFlight = false;
        sendButton.disabled = false;
      }
    }

    function responseText(body) {
      if (body && typeof body === "object") {
        return body.output_text ?? body.output ?? JSON.stringify(body, null, 2);
      }
      return body ?? "";
    }

    function renderMarkdown(value) {
      return escapeHtml(String(value || ""))
        .replace(/\\*\\*(.+?)\\*\\*/g, "<strong>$1</strong>")
        .replace(/^- (.+)$/gm, "• $1")
        .replace(/\\n/g, "<br>");
    }

    function formatTime(value) {
      try {
        return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      } catch {
        return value;
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
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

    saveEndpoint.addEventListener("click", async () => {
      try {
        const state = await request("/api/endpoint", {
          method: "POST",
          body: JSON.stringify({ endpoint: endpointInput.value }),
        });
        renderSnapshot(state);
        setStatus("", "Endpoint saved.");
      } catch (error) {
        setStatus("fail", error.message);
      }
    });

    checkHealthButton.addEventListener("click", async () => {
      checkHealthButton.disabled = true;
      setStatus("", "Checking readiness...");
      try {
        renderSnapshot(await request("/api/health", { method: "POST" }));
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        checkHealthButton.disabled = false;
      }
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

    clearButton.addEventListener("click", async () => {
      renderSnapshot(await request("/api/clear", { method: "POST" }));
      setStatus("", "Transcript cleared.");
    });

    load().catch((error) => setStatus("fail", error.message));
  </script>
</body>
</html>`;
}

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
        if (req.method === "GET" && url.pathname === "/api/state") {
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/endpoint") {
            const body = await readBody(req);
            state.endpoint = normalizeEndpoint(body.endpoint);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/health") {
            state.lastHealth = await checkReadiness(state.endpoint);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/responses") {
            const body = await readBody(req);
            const input = String(body.input || "").trim();
            if (!input) {
                sendJson(res, 400, { error: "Input is required." });
                return;
            }
            const turn = {
                input,
                createdAt: new Date().toISOString(),
                request: { endpoint: state.endpoint, path: "/responses", body: { input } },
                response: await callAgent(state.endpoint, "/responses", { input }),
            };
            state.messages.push(turn);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/responses/stream") {
            const body = await readBody(req);
            const input = String(body.input || "").trim();
            if (!input) {
                sendJson(res, 400, { error: "Input is required." });
                return;
            }
            res.writeHead(200, {
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-store",
                Connection: "keep-alive",
            });
            const turn = {
                input,
                createdAt: new Date().toISOString(),
                request: { endpoint: state.endpoint, path: "/responses", body: { input, stream: true } },
                response: {
                    ok: false,
                    status: "waiting",
                    durationMs: 0,
                    body: { output_text: "" },
                    streaming: true,
                    delivery: {
                        mode: "pending-stream",
                        upstreamStreaming: false,
                        active: true,
                    },
                },
            };
            state.messages.push(turn);
            writeEvent(res, "snapshot", stateSnapshot(state));
            const result = await callAgentStream(state.endpoint, { input }, (_delta, outputText, durationMs) => {
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
                writeEvent(res, "snapshot", stateSnapshot(state));
            });
            turn.response = result;
            writeEvent(res, "snapshot", stateSnapshot(state));
            res.end();
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/clear") {
            state.messages.length = 0;
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        sendJson(res, 404, { error: "Not found." });
    } catch (error) {
        sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) });
    }
}

async function startServer(ctx) {
    const state = {
        endpoint: normalizeEndpoint(ctx.input?.endpoint),
        messages: [],
        lastHealth: null,
    };
    const server = createServer((req, res) => {
        void handleRequest(req, res, state);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    return { server, state, url: `http://127.0.0.1:${port}/` };
}

await joinSession({
    canvases: [
        createCanvas({
            id: "agent-playground",
            displayName: "Agent Playground",
            description: "Chat with an agent through the Responses protocol.",
            inputSchema: {
                type: "object",
                properties: {
                    endpoint: {
                        type: "string",
                        description: "Base URL of the agent, for example http://127.0.0.1:8088.",
                    },
                },
                additionalProperties: false,
            },
            actions: [
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
                        state.endpoint = normalizeEndpoint(ctx.input?.endpoint);
                        return stateSnapshot(state);
                    },
                },
                {
                    name: "health_check",
                    description: "Call GET /readiness on the configured agent endpoint.",
                    handler: async (ctx) => {
                        const state = instanceState(ctx);
                        state.lastHealth = await checkReadiness(state.endpoint);
                        return state.lastHealth;
                    },
                },
                {
                    name: "send_response",
                    description: "Send a prompt to POST /responses and append the result to the transcript.",
                    inputSchema: {
                        type: "object",
                        properties: { input: { type: "string" } },
                        required: ["input"],
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const state = instanceState(ctx);
                        const input = String(ctx.input?.input || "").trim();
                        if (!input) {
                            throw new CanvasError("input_required", "Input is required.");
                        }
                        const turn = {
                            input,
                            createdAt: new Date().toISOString(),
                            request: { endpoint: state.endpoint, path: "/responses", body: { input } },
                            response: await callAgent(state.endpoint, "/responses", { input }),
                        };
                        state.messages.push(turn);
                        return turn;
                    },
                },
                {
                    name: "clear_transcript",
                    description: "Clear the tester transcript for this canvas instance.",
                    handler: async (ctx) => {
                        const state = instanceState(ctx);
                        state.messages.length = 0;
                        return stateSnapshot(state);
                    },
                },
            ],
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx);
                    servers.set(ctx.instanceId, entry);
                } else if (ctx.input?.endpoint) {
                    entry.state.endpoint = normalizeEndpoint(ctx.input.endpoint);
                }
                return {
                    title: "Agent Playground",
                    status: entry.state.endpoint,
                    url: entry.url,
                };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});

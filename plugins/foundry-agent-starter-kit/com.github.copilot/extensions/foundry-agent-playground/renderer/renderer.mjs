import { rendererClientScript } from "./renderer-client.mjs";
import { rendererStyles } from "./renderer-styles.mjs";

export function renderHtml() {
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Foundry Agent Playground</title>
  <script>
    (() => {
      const param = new URLSearchParams(window.location.search).get("clawpilotTheme");
      const theme =
        param || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      document.documentElement.setAttribute("data-theme", theme);
      document.documentElement.setAttribute("data-color-mode", theme);
    })();
  </script>
  <style>${rendererStyles}</style>
</head>
<body>
  <div class="app">
    <section class="hero">
      <div class="journey" aria-label="Agent journey">
        <button id="localStep" class="step active" type="button">
          <span class="step-index">1</span>
          <span><span class="step-title">Local</span><span id="localStepText" class="step-subtitle">Project</span></span>
          <span id="localStepState" class="step-state">First</span>
        </button>
        <button id="foundryStep" class="step" type="button">
          <span class="step-index">2</span>
          <span><span class="step-title">Foundry</span><span id="foundryStepText" class="step-subtitle">Deploy it</span></span>
          <span id="foundryStepState" class="step-state">Next</span>
        </button>
        <button id="teamsStep" class="step auxiliary" type="button">
          <span class="step-index">3</span>
          <span><span class="step-title">Teams</span><span id="teamsStepText" class="step-subtitle">Hire it</span></span>
          <span id="teamsStepState" class="step-state">Later</span>
        </button>
      </div>
      <div class="action-card">
        <div class="action-summary">
          <div class="action-heading">
            <div id="guideTitle" class="action-title">Make it work locally</div>
            <div id="actionStateChip" class="action-state-chip" title="Current agent state">Not checked</div>
            <span class="action-detail-trigger" tabindex="0" aria-describedby="guideCopy">
              Details
              <span id="guideCopy" class="action-copy" role="tooltip">Start or stop the local agent.</span>
            </span>
          </div>
          <div class="status-row" aria-live="polite">
            <div id="localEndpointBanner" class="local-endpoint-banner" hidden></div>
            <div id="localTicker" class="local-ticker" hidden></div>
            <div id="deployTicker" class="deploy-ticker" hidden></div>
          </div>
        </div>
        <div class="action-buttons">
          <div class="agent-picker hero-picker">
            <button id="agentPickerButton" class="agent-picker-button" type="button" aria-haspopup="listbox" aria-expanded="false">
              <span id="agentPickerLabel" class="agent-picker-label">Agent</span>
              <span class="agent-picker-chevron" aria-hidden="true"></span>
            </button>
            <div id="agentMenu" class="agent-menu" role="listbox" hidden></div>
          </div>
          <button id="primaryGuideAction" class="primary" type="button">Start local</button>
          <button id="stopLocalAction" class="secondary danger" type="button" hidden>Stop local</button>
          <button id="testHostedAction" class="secondary" type="button" hidden>Test hosted</button>
          <button id="advancedToggle" class="secondary" type="button" hidden>Refresh .env</button>
        </div>
      </div>
    </section>
    <main class="content">
      <section id="chatView" class="panel view">
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
        <section id="foundryStatus" class="foundry-status" hidden>
          <div class="foundry-status-head">
            <div>
              <div id="foundryStatusTitle" class="foundry-status-title">Foundry hosted agent</div>
              <div id="foundryStatusCopy" class="foundry-status-copy">Discovering hosted state...</div>
            </div>
            <span id="foundryStatusBadge" class="badge">Not deployed</span>
          </div>
          <div class="foundry-status-grid">
            <div class="foundry-status-item">
              <div class="foundry-status-label">Agent</div>
              <div id="foundryStatusAgent" class="foundry-status-value">Not resolved</div>
            </div>
            <div class="foundry-status-item">
              <div class="foundry-status-label">Hosted release</div>
              <div id="foundryStatusRelease" class="foundry-status-value">Not deployed</div>
            </div>
            <div class="foundry-status-item">
              <div class="foundry-status-label">State</div>
              <div id="foundryStatusState" class="foundry-status-value">Unknown</div>
            </div>
            <div class="foundry-status-item">
              <div class="foundry-status-label">Responses endpoint</div>
              <div id="foundryStatusEndpoint" class="foundry-status-value endpoint">Not discovered</div>
            </div>
          </div>
        </section>
        <details id="activityLog" class="activity-log" aria-live="polite" hidden>
          <summary class="activity-head">
            <div>
              <div class="activity-title">Activity</div>
              <div id="activitySummary" class="activity-summary">No canvas actions yet.</div>
            </div>
            <span id="activityCount" class="badge">0</span>
          </summary>
          <div id="activityItems" class="activity-items"></div>
        </details>
        <div id="transcript" class="transcript empty-state">
          <div class="empty">Send a prompt to test <code>POST /responses</code>.</div>
        </div>
        <button id="copyLatestAnswer" class="transcript-copy-latest" type="button" aria-label="Copy latest answer" hidden disabled>Copy latest answer</button>
      </section>
      <section id="deployView" class="deploy-view view" hidden>
        <div class="deploy-summary">
          <div class="deploy-card">
            <div class="deploy-label">Foundry target</div>
            <div id="foundryTarget" class="deploy-value">Not discovered</div>
          </div>
          <div class="deploy-card">
            <div class="deploy-label">Hosted agent</div>
            <div id="hostedAgent" class="deploy-value">minimal-agent</div>
          </div>
          <div class="deploy-card">
            <div class="deploy-label">Active version</div>
            <div id="hostedVersion" class="deploy-value">Not deployed</div>
          </div>
        </div>
        <pre id="deployLog" class="terminal"></pre>
      </section>
      <section id="teamsView" class="teams-view view" hidden>
        <div class="guide-card">
          <div class="teams-gates">
            <div class="teams-gate">
              <span class="gate-icon" aria-hidden="true"><img class="gate-logo" src="/assets/icon-service-AI-Foundry.svg" alt="" /></span>
              <div><div class="gate-title">Publish in Foundry</div><div class="gate-meta">Confirm the active hosted version, then use Publish to Teams and Microsoft 365 Copilot. Review name, version, descriptions, developer, and scope.</div></div>
            </div>
            <div class="teams-gate">
              <span class="gate-icon a365-gate-icon" aria-hidden="true"><img class="gate-logo a365-logo" src="/assets/icon-a365-agents.svg" alt="" /></span>
              <div><div class="gate-title">Approve request in A365</div><div class="gate-meta">Complete the Microsoft 365 publish request or admin approval flow for the chosen tenant scope.</div></div>
            </div>
            <div class="teams-gate">
              <span class="gate-icon" aria-hidden="true"><img class="gate-logo" src="/assets/icon-teams.svg" alt="" /></span>
              <div><div class="gate-title">Hire in Teams</div><div class="gate-meta">Install or hire the agent from Teams, send the same smoke-test prompt, and confirm it matches hosted Foundry behavior.</div></div>
            </div>
          </div>
        </div>
      </section>
    </main>
    <section id="composer" class="composer">
      <textarea id="prompt" placeholder="Ask the agent something... Enter sends, Shift+Enter adds a line." required></textarea>
      <div class="composer-actions">
        <button id="clear" type="button">Clear transcript</button>
        <div class="right-actions">
          <button class="primary" id="send" type="button">Send</button>
          <button class="primary" id="provisionButton" type="button" hidden>Prepare deploy</button>
          <button class="primary" id="deployButton" type="button" hidden>Deploy changes</button>
          <button class="secondary danger" id="cancelOperationButton" type="button" hidden>Cancel operation</button>
        </div>
      </div>
    </section>
    <div id="projectEndpointDialog" class="modal-backdrop" hidden>
      <form id="projectEndpointForm" class="modal-card">
        <div>
          <div class="modal-title">Foundry project endpoint</div>
          <div class="modal-copy">Paste the endpoint ending in <code>/api/projects/&lt;project&gt;</code>. The playground writes only non-secret values into gitignored <code>.env</code> files, then starts the local agent.</div>
        </div>
        <input id="projectEndpointDialogInput" aria-label="Foundry project endpoint" spellcheck="false" placeholder="https://<account>.services.ai.azure.com/api/projects/<project>" required />
        <div class="modal-actions">
          <button id="projectEndpointCancel" class="secondary" type="button">Cancel</button>
          <button id="projectEndpointSubmit" class="primary" type="submit">Save and start</button>
        </div>
      </form>
    </div>
  </div>
  <script>${rendererClientScript}</script>
</body>
</html>`;
}

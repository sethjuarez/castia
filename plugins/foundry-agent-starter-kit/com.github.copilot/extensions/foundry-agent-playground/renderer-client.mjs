export const rendererClientScript = `
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
    const teamsStatus = document.getElementById("teamsStatus");
    const teamsAgent = document.getElementById("teamsAgent");
    const teamsVersion = document.getElementById("teamsVersion");
    const stopLocalAction = document.getElementById("stopLocalAction");
    const statusDot = document.getElementById("statusDot");
    const statusText = document.getElementById("statusText");
    const turnCount = document.getElementById("turnCount");
    const passCount = document.getElementById("passCount");
    const failCount = document.getElementById("failCount");
    const avgLatency = document.getElementById("avgLatency");
    const transcript = document.getElementById("transcript");
    const promptInput = document.getElementById("prompt");
    const sendButton = document.getElementById("send");
    const provisionButton = document.getElementById("provisionButton");
    const deployButton = document.getElementById("deployButton");
    const teamsTestedButton = document.getElementById("teamsTestedButton");
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
      renderMessages(state.visibleMessages || []);
      if (state.target !== "hosted" && state.lastHealth) {
        setStatus(state.lastHealth.ok ? "ok" : "fail", "Readiness " + state.lastHealth.status + " in " + state.lastHealth.durationMs + "ms");
      } else if (activeView === "chat" && state.target !== "hosted" && state.localRun?.running) {
        setStatus("ok", "Local agent started.");
      } else if (activeView === "chat" && state.target !== "hosted" && state.localRun?.exitCode !== null && state.localRun?.exitCode !== undefined) {
        setStatus(state.localRun.exitCode === 0 ? "" : "fail", state.localRun.exitCode === 0 ? "Local agent stopped." : "Local agent exited with code " + state.localRun.exitCode + ".");
      }
      renderFoundryStatus(state);
      renderDeploy(state);
      renderTeams(state);
      renderLocalTicker(state);
      renderDeployTicker(state);
      renderJourney(state);
      renderView();
    }

    function renderLocalTicker(state) {
      const events = state.target === "hosted" ? [] : (state.localRun?.events || []);
      localTicker.hidden = !events.length || activeView !== "chat";
      if (!events.length || activeView !== "chat") {
        localTicker.innerHTML = "";
        localTicker.className = "local-ticker";
        return;
      }
      const event = events.at(-1);
      const kind = event.kind || "";
      localTicker.className = "local-ticker " + kind;
      localTicker.innerHTML = '<span class="ticker-mark" aria-hidden="true"></span>' +
        '<span class="ticker-text">' + escapeHtml(event.text) + '</span>';
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
        return;
      }
      deployTicker.hidden = false;
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
          if (state.localRun?.running && !state.lastHealth) {
            localRefreshTimer = window.setTimeout(tick, 1000);
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
      const teamsOk = Boolean(state.teams?.testedAt);
      const showingFoundryContext = activeView === "chat" && (foundryPanelOpen || state.target === "hosted");
      localStep.classList.toggle("done", localOk);
      foundryStep.classList.toggle("done", foundryOk);
      teamsStep.classList.toggle("done", teamsOk);
      localStepText.textContent = localRunning && !localOk ? "Starting" : localOk ? "Answered" : "Run it";
      foundryStepText.textContent = foundryOk ? "Hosted" : connected ? "Not deployed" : "Needs project";
      teamsStepText.textContent = teamsOk ? "Tested" : "Hire it";
      localStepState.textContent = localOk ? "Done" : "Start";
      foundryStepState.textContent = versionLabel || (foundryOk ? "Ready" : localOk ? "Next" : "Later");
      teamsStepState.textContent = teamsOk ? "Done" : foundryOk ? "Next" : "Later";
      if (activeView === "deploy") {
        const needsProvision = Boolean(state.deployment?.needsProvision);
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = !(connected && foundryOk);
        guideTitle.textContent = !connected ? "Connect Foundry project" : "Make it work in Foundry";
        guideCopy.textContent = !connected
          ? "Ask Copilot for the Foundry project endpoint so it can bootstrap .env, then refresh discovery here."
          : needsProvision
          ? "Prepare this repo for hosted deployment into the connected Foundry project."
          : foundryOk
          ? "Current version: " + (state.hosted.version || "ready") + ". Deploy changes when local updates are ready."
          : "Deploy the selected agent, then use the same transcript against the hosted target.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : needsProvision ? "Prepare deploy" : foundryOk ? "Deploy new version" : "Deploy first version";
        setActiveStep("foundry");
      } else if (activeView === "teams") {
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = true;
        guideTitle.textContent = "Publish and hire it in Teams";
        guideCopy.textContent = "Confirm the active Foundry version, publish it to Microsoft 365, approve it if needed, hire it in Teams, then run the same smoke prompt.";
        primaryGuideAction.textContent = teamsOk ? "Teams tested" : "Mark Teams tested";
        setActiveStep("teams");
      } else if (showingFoundryContext) {
        const needsProvision = Boolean(state.deployment?.needsProvision);
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = !(connected && foundryOk && state.target !== "hosted");
        guideTitle.textContent = !connected ? "Connect Foundry project" : foundryOk ? "Test it in Foundry" : "Deploy to Foundry";
        guideCopy.textContent = !connected
          ? "Project endpoint needed before hosted chat."
          : needsProvision
          ? "Prepare deploy, then return here to test hosted."
          : foundryOk
          ? "Hosted " + (state.hosted?.version ? "v" + state.hosted.version : "agent") + " ready. Send a prompt below."
          : "No hosted release found. Deploy the first version when local is ready.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : needsProvision ? "Prepare deploy" : foundryOk ? "Deploy new version" : "Deploy first version";
        setActiveStep("foundry");
      } else {
        primaryGuideAction.hidden = state.target !== "hosted" && localRunning;
        testHostedAction.hidden = true;
        guideTitle.textContent = state.target === "hosted" && !connected ? "Connect Foundry project" : state.target === "hosted" ? "Test it in Foundry" : "Local agent";
        guideCopy.textContent = state.target === "hosted" && !connected
          ? "Ask Copilot for the Foundry project endpoint so it can bootstrap .env, then refresh discovery here."
          : state.target === "hosted"
          ? "Current version: " + (state.hosted?.version || "ready") + ". Send a prompt here, or switch to deploy."
          : localRunning
          ? "Local agent is running."
          : "Start the local agent. If Foundry values are missing, this will ask for the project endpoint.";
        primaryGuideAction.textContent = state.target === "hosted" && !connected ? "Refresh .env" : state.target === "hosted" ? "Deploy" : "Start local";
        setActiveStep(state.target === "hosted" ? "foundry" : "local");
      }
      advancedToggle.hidden = true;
      advancedToggle.textContent = "Refresh .env";
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
      const connected = Boolean(latestState?.foundryConnection?.projectEndpoint && latestState?.foundryConnection?.modelDeployment);
      const hostedBlocked = activeView === "chat" && latestState?.target === "hosted" && !latestState?.hosted?.responsesEndpoint;
      const localBlocked = activeView === "chat" && latestState?.target !== "hosted" && !latestState?.lastHealth?.ok;
      const deploymentRunning = Boolean(latestState?.deployment?.running);
      chatView.hidden = activeView !== "chat";
      deployView.hidden = activeView !== "deploy";
      teamsView.hidden = activeView !== "teams";
      sendButton.hidden = activeView !== "chat";
      sendButton.disabled = inFlight || hostedBlocked || localBlocked;
      provisionButton.hidden = true;
      deployButton.hidden = true;
      provisionButton.disabled = deploymentRunning;
      deployButton.disabled = deploymentRunning;
      teamsTestedButton.hidden = true;
      promptInput.hidden = activeView !== "chat";
      promptInput.disabled = hostedBlocked || localBlocked;
      promptInput.placeholder = localBlocked
        ? "Start local and wait for readiness before chatting."
        : "Ask the agent something... Enter sends, Shift+Enter adds a line.";
      stopLocalAction.hidden = !(activeView === "chat" && latestState?.target !== "hosted" && latestState?.localRun?.running);
      stopLocalAction.disabled = false;
      if (activeView === "deploy") {
        provisionButton.hidden = !latestState?.deployment?.needsProvision;
        deployButton.hidden = Boolean(latestState?.deployment?.needsProvision);
        deployButton.textContent = latestState?.hosted?.version || latestState?.hosted?.responsesEndpoint ? "Deploy new version" : "Deploy first version";
      }
      primaryGuideAction.disabled = deploymentRunning && activeView !== "teams" && (activeView === "deploy" || foundryPanelOpen || latestState?.target === "hosted");
      clearButton.hidden = activeView === "teams";
      clearButton.textContent = activeView === "deploy" ? "Clear deploy log" : "Clear transcript";
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
        "Ask Copilot to collect the Foundry project endpoint, bootstrap .env, then refresh discovery here.\\n",
        "Then discover the deployed Foundry agent version and protocol endpoints.\\n\\n",
        "$ azd deploy " + (state.selectedAgent?.serviceName || "minimal-agent") + " --no-prompt\\n",
        "Deploy changes to Foundry and register a new hosted version.\\n",
      ].join("");
      deployLog.scrollTop = deployLog.scrollHeight;
    }

    function renderTeams(state) {
      const hosted = state.hosted || {};
      teamsStatus.textContent = state.teams?.testedAt ? "Tested " + formatTime(state.teams.testedAt) : "Not tested";
      teamsAgent.textContent = hosted.agentName || state.selectedAgent?.displayName || "Not resolved";
      teamsVersion.textContent = hosted.version ? "Version " + hosted.version : "Not deployed";
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
        const target = turn.target === "hosted" ? "Foundry" : "Local";
        return '<article class="turn">' +
          '<section class="bubble user">' +
          '<div class="bubble-head"><span class="speaker user">You</span><span>' + escapeHtml(target) + ' · ' + escapeHtml(formatTime(turn.createdAt)) + '</span></div>' +
          '<div class="bubble-body">' + renderMarkdown(turn.input) + '</div>' +
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
      if (latestState?.target === "hosted" && !latestState?.hosted?.responsesEndpoint) {
        setStatus("fail", "No hosted Responses endpoint is discovered yet. Ask Copilot for the Foundry project endpoint, bootstrap .env, then refresh.");
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

    function responseText(body) {
      if (body && typeof body === "object") {
        return body.output_text ?? body.output ?? JSON.stringify(body, null, 2);
      }
      return body ?? "";
    }

    function renderMarkdown(value) {
      const jsonDocument = renderJsonDocument(value);
      if (jsonDocument) return '<div class="md">' + jsonDocument + "</div>";
      const blocks = [];
      let inFence = false;
      let fence = [];
      let fenceLanguage = "";
      let list = null;
      let paragraph = [];

      function flushParagraph() {
        if (!paragraph.length) return;
        blocks.push("<p>" + renderInline(paragraph.join(" ")) + "</p>");
        paragraph = [];
      }

      function flushList() {
        if (!list) return;
        blocks.push("<" + list.type + ">" + list.items.map((item) => "<li>" + renderInline(item) + "</li>").join("") + "</" + list.type + ">");
        list = null;
      }

      const lines = String(value || "").split(/\\r?\\n/);
      for (let index = 0; index < lines.length; index += 1) {
        const rawLine = lines[index];
        const line = rawLine.replace(/\\s+$/, "");
        if (line.trim().startsWith(String.fromCharCode(96, 96, 96))) {
          if (inFence) {
            blocks.push(renderCodeBlock(fence.join("\\n"), fenceLanguage));
            fence = [];
            fenceLanguage = "";
            inFence = false;
          } else {
            flushParagraph();
            flushList();
            fenceLanguage = line.trim().slice(3).trim().toLowerCase();
            inFence = true;
          }
          continue;
        }
        if (inFence) {
          fence.push(rawLine);
          continue;
        }
        if (!line.trim()) {
          flushParagraph();
          flushList();
          continue;
        }
        const heading = line.match(/^\\s*(#{1,3})\\s+(.+)$/);
        if (heading) {
          flushParagraph();
          flushList();
          blocks.push("<h" + heading[1].length + ">" + renderInline(heading[2]) + "</h" + heading[1].length + ">");
          continue;
        }
        const table = renderTable(lines, index);
        if (table) {
          flushParagraph();
          flushList();
          blocks.push(table.html);
          index = table.nextIndex - 1;
          continue;
        }
        const unordered = line.match(/^\\s*[-*]\\s+(.+)$/);
        const ordered = line.match(/^\\s*\\d+[.)]\\s+(.+)$/);
        if (unordered || ordered) {
          flushParagraph();
          const type = unordered ? "ul" : "ol";
          if (!list || list.type !== type) flushList();
          list ||= { type, items: [] };
          list.items.push((unordered || ordered)[1]);
          continue;
        }
        flushList();
        paragraph.push(line.trim());
      }
      if (inFence) blocks.push(renderCodeBlock(fence.join("\\n"), fenceLanguage));
      flushParagraph();
      flushList();
      return '<div class="md">' + (blocks.join("") || "<p></p>") + "</div>";
    }

    function renderCodeBlock(value, language) {
      const json = renderJsonDocument(value, language);
      if (json) return json;
      return "<pre><code>" + escapeHtml(value) + "</code></pre>";
    }

    function renderJsonDocument(value, language = "") {
      const text = String(value || "").trim();
      if (!text || (!["json", "jsonc"].includes(language) && !/^[\\[{]/.test(text))) return null;
      try {
        const parsed = JSON.parse(text);
        return '<pre class="json-pre"><code>' + highlightJson(JSON.stringify(parsed, null, 2)) + "</code></pre>";
      } catch {
        return null;
      }
    }

    function highlightJson(value) {
      const tokenPattern = /("(?:\\\\.|[^"\\\\])*")(\\s*:)?|\\b(true|false|null)\\b|-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?/g;
      let output = "";
      let lastIndex = 0;
      String(value || "").replace(tokenPattern, (match, stringToken, keySuffix, literal, offset) => {
        output += escapeHtml(value.slice(lastIndex, offset));
        if (stringToken) {
          const className = keySuffix ? "json-key" : "json-string";
          output += '<span class="' + className + '">' + escapeHtml(stringToken) + "</span>" + escapeHtml(keySuffix || "");
        } else if (literal) {
          output += '<span class="json-literal">' + escapeHtml(match) + "</span>";
        } else {
          output += '<span class="json-number">' + escapeHtml(match) + "</span>";
        }
        lastIndex = offset + match.length;
        return match;
      });
      return output + escapeHtml(value.slice(lastIndex));
    }

    function renderTable(lines, start) {
      if (!String(lines[start] || "").includes("|") || !isTableSeparator(lines[start + 1] || "")) return null;
      const header = splitTableRow(lines[start]);
      const separator = splitTableRow(lines[start + 1]);
      if (!header.length || separator.length < header.length) return null;
      const alignments = separator.map((cell) =>
        cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : cell.startsWith(":") ? "left" : ""
      );
      const rows = [];
      let index = start + 2;
      while (index < lines.length && String(lines[index] || "").trim() && String(lines[index] || "").includes("|")) {
        if (String(lines[index]).trim().startsWith(String.fromCharCode(96, 96, 96))) break;
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      const cellAttr = (column) => alignments[column] ? ' style="text-align:' + alignments[column] + '"' : "";
      const head = "<thead><tr>" + header.map((cell, column) => "<th" + cellAttr(column) + ">" + renderInline(cell) + "</th>").join("") + "</tr></thead>";
      const body = "<tbody>" + rows.map((row) => "<tr>" + header.map((_, column) => "<td" + cellAttr(column) + ">" + renderInline(row[column] || "") + "</td>").join("") + "</tr>").join("") + "</tbody>";
      return { html: '<div class="table-scroll"><table>' + head + body + "</table></div>", nextIndex: index };
    }

    function splitTableRow(line) {
      return String(line || "").trim().replace(/^\\|/, "").replace(/\\|$/, "").split("|").map((cell) => cell.trim());
    }

    function isTableSeparator(line) {
      return /^\\s*\\|?\\s*:?-{3,}:?\\s*(\\|\\s*:?-{3,}:?\\s*)+\\|?\\s*$/.test(String(line || ""));
    }

    function renderInline(value) {
      const tick = String.fromCharCode(96);
      const inlineCode = new RegExp(tick + "([^" + tick + "]+)" + tick, "g");
      return escapeHtml(value)
        .replace(inlineCode, "<code>$1</code>")
        .replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
        .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
        .replace(/\\*([^*]+)\\*/g, "<em>$1</em>");
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
        setStatus(state.localRun?.running ? "ok" : "", state.localRun?.running ? "Local agent started." : "Local agent starting.");
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
            modelDeployment: "gpt-6-astra",
            toolboxName: "contract-toolbox",
            overwrite: true,
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

    async function markTeamsTested() {
      primaryGuideAction.disabled = true;
      teamsTestedButton.disabled = true;
      try {
        const state = await request("/api/teams/tested", { method: "POST" });
        renderSnapshot(state);
        setStatus("ok", "Teams test marked complete.");
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        teamsTestedButton.disabled = false;
      }
    }

    async function checkReadinessFromCanvas() {
      if (latestState?.target === "hosted" && !latestState?.hosted?.responsesEndpoint) {
        setStatus("fail", "No hosted Responses endpoint is discovered yet. Ask Copilot for the Foundry project endpoint, bootstrap .env, then refresh.");
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
      } else if (activeView === "teams") {
        void markTeamsTested();
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
        setStatus((latestState?.deployment?.exitCode ?? 1) === 0 ? "ok" : "fail", (latestState?.deployment?.exitCode ?? 1) === 0 ? successText : failureText);
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        button.disabled = false;
        primaryGuideAction.disabled = false;
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

    teamsTestedButton.addEventListener("click", async () => {
      await markTeamsTested();
    });

    load().catch((error) => setStatus("fail", error.message));
`;

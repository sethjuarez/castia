import { spawn, spawnSync } from "node:child_process";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createConnection } from "node:net";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import {
    callAgent,
    checkReadiness,
    configureAgentClient,
    azureAccessToken,
    isLoopbackEndpoint,
    responseText,
} from "./client/agent-client.mjs";
import { DEFAULT_ENDPOINT, DEFAULT_MODEL_DEPLOYMENT, DEFAULT_TOOLBOX_NAME } from "./domain/constants.mjs";
import {
    activeEndpoint,
    activeProtocolEndpoint,
    addActivity,
    addLocalEvent,
    clearTargetHealth,
    clearMessagesForTarget,
    copyableAnswerText,
    emptyFoundryConnection,
    emptyHostedContext,
    emptyLocalRun,
    endpointPort,
    endpointWithPort,
    hasFoundryProjectValues,
    normalizeEndpoint,
    selectedAgent,
    selectedLocalEndpoint,
    setActiveProtocol,
    setTarget,
    setTargetHealth,
    switchSelectedAgent,
    protocolState,
    responseDisplayText,
    responsePendingLabel,
    stateSnapshot,
    transcriptState,
    updateActivity,
} from "./state/snapshot.mjs";
import {
    beginOperation,
    completeOperation,
    emptyOperationState,
    enterIrreversiblePhase,
    updateOperation,
} from "./domain/operations.mjs";
import {
    appendOperationRecord,
    configureRuntimeStore,
    persistStateSnapshot,
    pluginRuntimeRoot,
    readStateSnapshot,
} from "./state/persistence.mjs";
import { rehydratePlaygroundState } from "./state/rehydration.mjs";
import { localStartupStillPending } from "./state/local-readiness.mjs";
import { discoverAgents, serviceEnvPrefix } from "./client/agent-discovery.mjs";
import {
    discoverHostedContextFromFoundry,
    hostedContextFromAzd,
} from "./client/hosted-discovery.mjs";
import { writeEvent } from "./routes/http.mjs";
import { createRequestHandler } from "./routes/playground-routes.mjs";
import { createCanvasActions } from "./actions/canvas-actions.mjs";
import { createActivityTurn, finishActivityTurn } from "./protocols/activity-protocol.mjs";
import {
    clearProjectEndpointPrompt,
    createPlaygroundCommands,
} from "./commands/playground-commands.mjs";
import {
    localStartCommand,
    localStartupFailure,
    stderrTail,
} from "./domain/local-launcher.mjs";

const EXTENSION_ROOT = dirname(fileURLToPath(import.meta.url));
const ICON_PATH = join(EXTENSION_ROOT, "assets", "castia-mark.png");
const servers = new Map();

function cleanupManagedLocalRuns() {
    for (const entry of servers.values()) {
        const child = entry.state?.localRun?.process;
        if (child && !child.killed) {
            killLocalProcessTreeSync(child.pid);
        }
    }
}

for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, () => {
        cleanupManagedLocalRuns();
        process.exit(0);
    });
}

process.once("exit", cleanupManagedLocalRuns);

async function exists(path) {
    try {
        await access(path);
        return true;
    } catch {
        return false;
    }
}

function normalizeWorkspacePath(path) {
    const workspace = resolve(process.cwd());
    const resolved = isAbsolute(String(path || "")) ? resolve(path) : resolve(workspace, String(path || ""));
    const rel = relative(workspace, resolved);
    if (rel.startsWith("..") || isAbsolute(rel)) {
        throw new CanvasError("path_outside_workspace", "Bootstrap targets must stay inside the current workspace.");
    }
    return { path: resolved, label: rel ? rel.split(/[\\/]+/).join("\\") : "." };
}



function instanceState(ctx) {
    const entry = servers.get(ctx.instanceId);
    if (!entry) {
        throw new CanvasError("instance_not_open", "Open the canvas before invoking actions.");
    }
    return entry.state;
}



















function parseFoundryProjectEndpoint(endpoint) {
    try {
        const parsed = new URL(endpoint);
        const projectMatch = parsed.pathname.match(/\/api\/projects\/([^/]+)$/i);
        return {
            accountName: parsed.hostname.split(".")[0],
            projectName: projectMatch ? decodeURIComponent(projectMatch[1]) : null,
        };
    } catch {
        return { accountName: null, projectName: null };
    }
}

function normalizeFoundryProjectEndpoint(endpoint) {
    const normalized = String(endpoint || "").trim().replace(/\/+$/, "");
    if (!/^https:\/\/[^/\s]+\/api\/projects\/[^/\s]+$/i.test(normalized)) {
        throw new CanvasError("invalid_foundry_endpoint", "Enter a Foundry project endpoint ending in /api/projects/<project>.");
    }
    return normalized;
}

function bootstrapEnvValues({ projectEndpoint, modelDeployment, toolboxName }) {
    const endpoint = normalizeFoundryProjectEndpoint(projectEndpoint);
    const deployment = String(modelDeployment || DEFAULT_MODEL_DEPLOYMENT).trim() || DEFAULT_MODEL_DEPLOYMENT;
    const toolbox = String(toolboxName || "").trim();
    const parsed = parseFoundryProjectEndpoint(endpoint);
    const values = {
        FOUNDRY_PROJECT_ENDPOINT: endpoint,
        AZURE_AI_PROJECT_ENDPOINT: endpoint,
        AZURE_AIPROJECT_ENDPOINT: endpoint,
        AZURE_AI_ACCOUNT_NAME: parsed.accountName || "",
        AZURE_AI_PROJECT_NAME: parsed.projectName || "",
        AZURE_AI_MODEL_DEPLOYMENT_NAME: deployment,
    };
    if (toolbox) {
        const toolboxKey = toolbox.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
        values.TOOLBOX_NAME = toolbox;
        values[`TOOLBOX_${toolboxKey}_MCP_ENDPOINT`] = `${endpoint}/toolboxes/${encodeURIComponent(toolbox)}/mcp?api-version=v1`;
    }
    return Object.fromEntries(Object.entries(values).filter(([, value]) => value));
}

function subscriptionFromResourceId(id) {
    return String(id || "").match(/\/subscriptions\/([^/]+)/i)?.[1] || null;
}

function resourceGroupFromResourceId(id) {
    return String(id || "").match(/\/resourceGroups\/([^/]+)/i)?.[1] || null;
}

async function setAzdEnvValues(cwd, values, log) {
    let exitCode = 0;
    for (const [key, value] of Object.entries(values)) {
        if (!value) continue;
        const result = await runCommand("azd", ["env", "set", key, value], { cwd });
        exitCode ||= result.code;
        const displayValue = key.includes("ENDPOINT") ? value : "<set>";
        log?.push(`$ azd env set ${key} ${displayValue}\n`);
        if (result.code !== 0) {
            log?.push(result.output || `Failed to set ${key}.\n`);
        }
    }
    return exitCode;
}

async function deploymentStamp(cwd) {
    const revision = await runCommand("git", ["rev-parse", "--short=12", "HEAD"], { cwd });
    const source = revision.code === 0 && revision.output.trim() ? revision.output.trim() : "unknown";
    return `${source}-${new Date().toISOString()}`;
}

async function discoverManagementContext(agent, endpoint) {
    const parsed = parseFoundryProjectEndpoint(endpoint);
    if (!parsed.accountName || !parsed.projectName) {
        return { ...parsed, message: "Could not parse account/project from endpoint." };
    }
    const accountResult = await runCommand("az", ["cognitiveservices", "account", "list", "--output", "json"], {
        cwd: agent.root,
    });
    if (accountResult.code !== 0) {
        return { ...parsed, message: accountResult.output || "Azure CLI account lookup failed." };
    }
    let accounts = [];
    try {
        accounts = JSON.parse(accountResult.output || "[]");
    } catch {
        return { ...parsed, message: "Azure CLI returned unreadable account data." };
    }
    const matches = accounts.filter((account) => account?.name === parsed.accountName);
    if (matches.length !== 1) {
        return {
            ...parsed,
            message: matches.length
                ? `Found ${matches.length} accounts named ${parsed.accountName}; choose the subscription manually.`
                : `Could not find Azure AI account ${parsed.accountName} in the current Azure login.`,
        };
    }
    const account = matches[0];
    const accountId = account.id;
    return {
        ...parsed,
        subscriptionId: subscriptionFromResourceId(accountId),
        resourceGroup: account.resourceGroup || resourceGroupFromResourceId(accountId),
        location: account.location,
        projectId: `${accountId}/projects/${parsed.projectName}`,
        message: "Derived deployment context from the Foundry project endpoint.",
    };
}

async function discoverTenantId(agent) {
    const result = await runCommand("az", ["account", "show", "--query", "tenantId", "--output", "tsv"], {
        cwd: agent.root,
    });
    return result.code === 0 ? result.output.trim() : null;
}

async function findEnvExampleFiles(dir, depth = 0) {
    if (depth > 6) return [];
    const entries = await readdir(dir, { withFileTypes: true }).catch(() => []);
    const files = [];
    for (const entry of entries) {
        if (["node_modules", ".git", ".venv", "__pycache__", ".azure"].includes(entry.name)) continue;
        const path = join(dir, entry.name);
        if (entry.isFile() && entry.name === ".env.example") {
            files.push(path);
        } else if (entry.isDirectory()) {
            files.push(...(await findEnvExampleFiles(path, depth + 1)));
        }
    }
    return files;
}

function uniqueTargets(targets) {
    const seen = new Set();
    return targets.filter((target) => {
        const key = target.path.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

async function commonEnvTargets(agents) {
    const targets = [];
    for (const file of await findEnvExampleFiles(process.cwd())) {
        targets.push({ path: join(dirname(file), ".env"), source: ".env.example" });
    }
    for (const agent of agents || []) {
        targets.push({ path: join(agent.root, ".env"), source: "discovered hosted agent" });
    }
    const modulesAgents = join(process.cwd(), "modules", "agents");
    if (await exists(modulesAgents)) {
        targets.push({ path: join(modulesAgents, ".env"), source: "modules\\agents layout" });
        const entries = await readdir(modulesAgents, { withFileTypes: true }).catch(() => []);
        for (const entry of entries) {
            if (entry.isDirectory() && !entry.name.startsWith(".")) {
                targets.push({ path: join(modulesAgents, entry.name, ".env"), source: "modules\\agents child" });
            }
        }
    }
    return uniqueTargets(targets)
        .map((target) => ({ ...normalizeWorkspacePath(target.path), source: target.source }))
        .filter((target) => basename(target.path) === ".env");
}

async function isGitIgnored(path) {
    const { label } = normalizeWorkspacePath(path);
    const result = await runCommand("git", ["check-ignore", "--quiet", "--", label], { cwd: process.cwd() });
    return result.code === 0;
}

async function inspectEnvBootstrapTargets(agents, requestedTargets) {
    const candidates = Array.isArray(requestedTargets) && requestedTargets.length
        ? requestedTargets.map((target) => ({ ...normalizeWorkspacePath(target), source: "requested" }))
        : await commonEnvTargets(agents);
    const inspected = [];
    for (const target of uniqueTargets(candidates)) {
        const isEnv = basename(target.path) === ".env";
        const ignored = isEnv ? await isGitIgnored(target.path) : false;
        inspected.push({
            path: target.path,
            label: target.label,
            source: target.source,
            exists: await exists(target.path),
            ignored,
            writable: isEnv && ignored,
            reason: !isEnv ? "Only .env files can be bootstrapped." : ignored ? null : "Not ignored by git.",
        });
    }
    return inspected.sort((a, b) => a.label.localeCompare(b.label));
}

function mergeDotEnv(existing, values, overwrite) {
    const touched = new Set();
    const changed = [];
    const preserved = [];
    const lines = String(existing || "").split(/\r?\n/);
    const output = lines.map((line) => {
        const match = line.match(/^([A-Z0-9_]+)\s*=/);
        if (!match || !(match[1] in values)) return line;
        const key = match[1];
        touched.add(key);
        const current = line.slice(line.indexOf("=") + 1).trim();
        if (current && !overwrite) {
            preserved.push(key);
            return line;
        }
        changed.push(key);
        return `${key}=${values[key]}`;
    });
    for (const [key, value] of Object.entries(values)) {
        if (touched.has(key)) continue;
        output.push(`${key}=${value}`);
        changed.push(key);
    }
    while (output.length && output[0] === "") output.shift();
    while (output.length && output.at(-1) === "") output.pop();
    return { content: `${output.join("\n")}\n`, changed, preserved };
}

async function bootstrapLocalEnv(state, input = {}) {
    const values = bootstrapEnvValues({
        projectEndpoint: input.projectEndpoint,
        modelDeployment: input.modelDeployment,
        toolboxName: input.toolboxName === undefined ? DEFAULT_TOOLBOX_NAME : input.toolboxName,
    });
    const targets = await inspectEnvBootstrapTargets(state.agents, input.targetPaths);
    const writable = targets.filter((target) => target.writable);
    if (!writable.length && !input.dryRun) {
        throw new CanvasError("no_ignored_env_targets", "No gitignored .env targets were discovered. Add .env to .gitignore or pass ignored .env targetPaths.");
    }
    const overwrite = Boolean(input.overwrite);
    const written = [];
    const skipped = targets.filter((target) => !target.writable);
    if (!input.dryRun) {
        for (const target of writable) {
            const existing = await readFile(target.path, "utf8").catch(() => "");
            const merged = mergeDotEnv(existing, values, overwrite);
            await mkdir(dirname(target.path), { recursive: true });
            await writeFile(target.path, merged.content, "utf8");
            written.push({
                path: target.label,
                existed: target.exists,
                changedKeys: merged.changed,
                preservedKeys: merged.preserved,
            });
        }
        state.foundryConnection = {
            ...state.foundryConnection,
            projectEndpoint: values.FOUNDRY_PROJECT_ENDPOINT,
            modelDeployment: values.AZURE_AI_MODEL_DEPLOYMENT_NAME,
            accountName: values.AZURE_AI_ACCOUNT_NAME || null,
            projectName: values.AZURE_AI_PROJECT_NAME || null,
            connectedAt: new Date().toISOString(),
            lastConnectExitCode: 0,
            lastDiscoveryMessage: `Bootstrapped ${written.length} gitignored .env file${written.length === 1 ? "" : "s"}.`,
        };
        const agent = selectedAgent(state);
        const hosted = state.hostedByAgent[agent.id] || emptyHostedContext(agent);
        state.hostedByAgent[agent.id] = {
            ...hosted,
            projectEndpoint: values.FOUNDRY_PROJECT_ENDPOINT,
            modelDeployment: values.AZURE_AI_MODEL_DEPLOYMENT_NAME,
        };
        state.hosted = state.hostedByAgent[agent.id];
    }
    state.envBootstrap = {
        previewedAt: new Date().toISOString(),
        dryRun: Boolean(input.dryRun),
        overwrite,
        values,
        targets: targets.map((target) => ({
            path: target.label,
            source: target.source,
            exists: target.exists,
            ignored: target.ignored,
            writable: target.writable,
            reason: target.reason,
        })),
        written,
        skipped: skipped.map((target) => ({
            path: target.label,
            source: target.source,
            reason: target.reason,
        })),
    };
    return state.envBootstrap;
}



function parseAzdEnv(output) {
    const values = {};
    for (const line of String(output || "").split(/\r?\n/)) {
        const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
        if (!match) continue;
        let value = match[2].trim();
        if (
            (value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))
        ) {
            value = value.slice(1, -1);
        }
        values[match[1]] = value;
    }
    return values;
}

async function readAgentEnv(agent) {
    const envPath = join(agent.root, ".env");
    const output = await readFile(envPath, "utf8").catch(() => "");
    return { envPath, exists: Boolean(output), values: parseAzdEnv(output) };
}

function localEnvHasFoundryProjectValues(env) {
    const values = localEnvFoundryProjectValues(env);
    return Boolean(values.projectEndpoint && values.modelDeployment);
}

function localEnvFoundryProjectValues(env) {
    const values = env?.values || {};
    const projectEndpoint =
        values.FOUNDRY_PROJECT_ENDPOINT ||
        values.AZURE_AI_PROJECT_ENDPOINT ||
        values.AZURE_AIPROJECT_ENDPOINT ||
        null;
    const modelDeployment =
        values.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        values.AZURE_OPENAI_DEPLOYMENT_NAME ||
        null;
    return { projectEndpoint, modelDeployment };
}

async function refreshLocalEnvState(state) {
    const env = await readAgentEnv(selectedAgent(state));
    state.localEnv = {
        path: env.envPath,
        exists: env.exists,
        hasFoundryProjectValues: localEnvHasFoundryProjectValues(env),
        loadedAt: new Date().toISOString(),
    };
    return env;
}

async function syncLocalBootstrapForStart(state) {
    const agent = selectedAgent(state);
    let env = await refreshLocalEnvState(state);
    let localValues = localEnvFoundryProjectValues(env);
    if (localValues.projectEndpoint && localValues.modelDeployment) {
        if (!hasFoundryProjectValues(state)) {
            await hydrateFoundryConnectionFromDotEnv(state);
        }
        const azd = parseAzdEnv((await runCommand("azd", ["env", "get-values"], { cwd: agent.root })).output);
        const azdProjectEndpoint =
            azd.FOUNDRY_PROJECT_ENDPOINT ||
            azd.AZURE_AI_PROJECT_ENDPOINT ||
            azd.AZURE_AIPROJECT_ENDPOINT ||
            null;
        const azdModelDeployment =
            azd.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
            azd.AZURE_OPENAI_DEPLOYMENT_NAME ||
            null;
        if (!azdProjectEndpoint || !azdModelDeployment) {
            await setAzdEnvValues(agent.root, bootstrapEnvValues({
                projectEndpoint: localValues.projectEndpoint,
                modelDeployment: localValues.modelDeployment,
                toolboxName: null,
            }));
        }
        return { ok: true, source: "env" };
    }
    if (state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment) {
        const targetPath = relative(process.cwd(), join(agent.root, ".env"));
        await bootstrapLocalEnv(state, {
            projectEndpoint: state.foundryConnection.projectEndpoint,
            modelDeployment: state.foundryConnection.modelDeployment,
            toolboxName: DEFAULT_TOOLBOX_NAME,
            overwrite: false,
            targetPaths: [targetPath],
        });
        env = await refreshLocalEnvState(state);
        localValues = localEnvFoundryProjectValues(env);
        return {
            ok: Boolean(localValues.projectEndpoint && localValues.modelDeployment),
            source: "azd",
        };
    }
    return { ok: false, source: "missing" };
}

async function commandRuntimeCwd() {
    const cwd = pluginRuntimeRoot();
    await mkdir(cwd, { recursive: true });
    return cwd;
}

async function runCommand(command, args, { cwd, onOutput, signal, onChild } = {}) {
    const effectiveCwd = cwd || await commandRuntimeCwd();
    return new Promise((resolve) => {
        if (signal?.aborted) {
            resolve({ code: 130, output: "Command cancelled before start.\n", cancelled: true });
            return;
        }
        const output = [];
        let cancelled = false;
        const child = spawn(command, args, {
            cwd: effectiveCwd,
            shell: process.platform === "win32",
            env: { ...process.env, AZURE_DEV_USER_AGENT: "agent_playground" },
        });
        onChild?.(child);
        const abort = () => {
            cancelled = true;
            if (child.killed || child.exitCode !== null || child.signalCode) return;
            if (process.platform === "win32") {
                void killLocalProcessTree(child.pid);
            } else {
                child.kill("SIGTERM");
            }
        };
        signal?.addEventListener?.("abort", abort, { once: true });
        const append = (chunk) => {
            const text = chunk.toString();
            output.push(text);
            onOutput?.(text);
        };
        child.stdout.on("data", append);
        child.stderr.on("data", append);
        child.on("error", (error) => {
            signal?.removeEventListener?.("abort", abort);
            const text = `${error.name}: ${error.message}`;
            output.push(text);
            onOutput?.(text);
            resolve({ code: 1, output: output.join(""), cancelled });
        });
        child.on("close", (code) => {
            signal?.removeEventListener?.("abort", abort);
            resolve({ code: cancelled ? 130 : code ?? 0, output: output.join(""), cancelled });
        });
    });
}

configureAgentClient({ runCommand });

async function startLocalAgent(state) {
    if (state.localRun?.running) return;
    clearTargetHealth(state, "local");
    const agent = selectedAgent(state);
    const local = await localStartCommand(agent);
    if (!local) {
        throw new CanvasError("local_start_unsupported", "No local start command was discovered for this agent.");
    }
    let endpoint = state.localEndpoints[agent.id] || selectedLocalEndpoint(state);
    const requestedEndpoint = endpoint;
    let portWarning = null;
    await cleanupStaleWorkspaceLocalPorts(state, endpoint);
    if (await isEndpointPortOpen(endpoint)) {
        portWarning = `${endpoint} is already in use.`;
        addLocalEvent(state, "warn", portWarning);
        endpoint = await nextAvailableLocalEndpoint(endpoint);
        portWarning = `${portWarning} Using fallback endpoint ${endpoint}.`;
        addLocalEvent(state, "warn", portWarning);
    }
    const port = endpointPort(endpoint);
    clearMessagesForTarget(state, "local");
    const runId = `${agent.id}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
    const startupTimeoutMs = Number.isFinite(local.startupTimeoutMs) ? local.startupTimeoutMs : 15000;
    const readinessDeadlineAt = new Date(Date.now() + startupTimeoutMs).toISOString();
    const commandText = `${local.command} ${local.args.join(" ")}`;
    state.localRun = {
        running: true,
        command: commandText,
        cwd: local.cwd || agent.root,
        startedAt: new Date().toISOString(),
        completedAt: null,
        exitCode: null,
        log: [`$ ${commandText}\n`],
        stderr: [],
        events: state.localRun?.events || [],
        process: null,
        agentId: agent.id,
        agentName: agent.serviceName,
        endpoint,
        runId,
        readiness: {
            status: "starting",
            phase: local.startupPhase || "starting",
            deadlineAt: readinessDeadlineAt,
            timeoutMs: startupTimeoutMs,
        },
        launcher: {
            managed: Boolean(local.managed),
            manager: local.manager || null,
            managedCommand: commandText,
        },
        failure: null,
        requestedEndpoint,
        portWarning,
        identityMismatch: null,
    };
    if (local.managed) {
        addLocalEvent(state, "", "Syncing Python dependencies with uv; first run can take a minute.");
    }
    addLocalEvent(state, "", `Starting local agent on ${endpoint}.`);
    const envValues = localEnvFoundryProjectValues(await readAgentEnv(agent));
    const childEnv = {
        ...process.env,
        ...(local.env || {}),
        ...(port ? { PORT: String(port) } : {}),
    };
    for (const [key, value] of Object.entries({
        FOUNDRY_PROJECT_ENDPOINT:
            envValues.projectEndpoint ||
            state.foundryConnection.projectEndpoint ||
            process.env.FOUNDRY_PROJECT_ENDPOINT,
        AZURE_AI_MODEL_DEPLOYMENT_NAME:
            envValues.modelDeployment ||
            state.foundryConnection.modelDeployment ||
            process.env.AZURE_AI_MODEL_DEPLOYMENT_NAME,
    })) {
        if (value) {
            childEnv[key] = value;
        } else {
            delete childEnv[key];
        }
    }
    const child = spawn(local.command, local.args, {
        cwd: local.cwd || agent.root,
        shell: false,
        env: childEnv,
    });
    state.localRun.process = child;
    const appendStdout = (chunk) => {
        if (state.localRun?.runId === runId) state.localRun.log.push(chunk.toString());
    };
    const appendStderr = (chunk) => {
        if (state.localRun?.runId !== runId) return;
        const text = chunk.toString();
        state.localRun.log.push(text);
        state.localRun.stderr = [...(state.localRun.stderr || []), text].slice(-20);
    };
    child.stdout.on("data", appendStdout);
    child.stderr.on("data", appendStderr);
    child.on("error", (error) => {
        if (state.localRun?.runId !== runId) return;
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = 1;
        state.localRun.readiness = state.localRun.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "failed", completedAt: new Date().toISOString() }
            : state.localRun.readiness;
        state.localRun.log.push(`${error.name}: ${error.message}\n`);
        state.localRun.failure = localStartupFailure({
            localRun: state.localRun,
            stderr: stderrTail(state.localRun.stderr),
            error,
        });
        addLocalEvent(state, "fail", state.localRun.failure.suggestion || state.localRun.failure.rootCause);
    });
    child.on("close", (code) => {
        if (state.localRun?.runId !== runId) return;
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = code ?? 0;
        state.localRun.readiness = state.localRun.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "stopped", completedAt: new Date().toISOString() }
            : state.localRun.readiness;
        if ((code ?? 0) !== 0) {
            state.localRun.failure = localStartupFailure({
                localRun: state.localRun,
                stderr: stderrTail(state.localRun.stderr),
            });
        }
        addLocalEvent(
            state,
            code === 0 ? "" : "fail",
            code === 0
                ? "Local agent stopped."
                : state.localRun.failure?.suggestion || state.localRun.failure?.rootCause || `Local agent exited with code ${code ?? 0}.`,
        );
        delete state.localRun.process;
    });
    waitForLocalReadiness(state, { agent, endpoint, runId }).then((health) => {
        if (state.localRun?.runId !== runId || !state.localRun?.running) return;
        state.lastHealth = setTargetHealth(state, { ...health, source: "startup" }, "local");
        reconcileSelectedAgentFromReadiness(state, state.lastHealth, { source: "startup" });
        if (health.ok) {
            state.localEndpoints[agent.id] = endpoint;
        }
        state.localRun.readiness = {
            ...(state.localRun.readiness || {}),
            status: health.ok ? "ready" : "failed",
            completedAt: new Date().toISOString(),
        };
        addLocalEvent(state, health.ok ? "ok" : "fail", health.ok ? `Local agent ready at ${endpoint}.` : `Local readiness failed: ${health.body || health.status}`);
    }).catch((error) => {
        if (state.localRun?.runId !== runId) return;
        addLocalEvent(state, "fail", error instanceof Error ? error.message : String(error));
    });
}

function reconcileLocalReadinessAfterHealth(state) {
    if (state.target !== "local" || !state.localRun?.running || !state.lastHealth) return;
    if (localStartupStillPending(state, state.lastHealth)) return;
    const previousStatus = state.localRun.readiness?.status;
    state.localRun.readiness = {
        ...(state.localRun.readiness || {}),
        status: state.lastHealth.ok ? "ready" : "failed",
        completedAt: new Date().toISOString(),
    };
    if (state.lastHealth.ok && previousStatus !== "ready") {
        const endpoint = state.localRun.endpoint || activeEndpoint(state);
        state.localEndpoints[state.localRun.agentId || selectedAgent(state).id] = endpoint;
        addLocalEvent(state, "ok", `Local agent ready at ${endpoint}.`);
    }
}

async function isEndpointPortOpen(endpoint) {
    if (!isLoopbackEndpoint(endpoint)) return false;
    let parsed;
    try {
        parsed = new URL(endpoint);
    } catch {
        return false;
    }
    const port = Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
    if (!Number.isInteger(port) || port <= 0) return false;
    const host = parsed.hostname === "localhost" ? "127.0.0.1" : parsed.hostname;
    return new Promise((resolve) => {
        const socket = createConnection({ host, port });
        const finish = (open) => {
            socket.removeAllListeners();
            socket.destroy();
            resolve(open);
        };
        socket.setTimeout(300);
        socket.once("connect", () => finish(true));
        socket.once("timeout", () => finish(false));
        socket.once("error", () => finish(false));
    });
}

async function nextAvailableLocalEndpoint(endpoint) {
    const basePort = endpointPort(endpoint) || 8088;
    for (let port = basePort + 1; port < basePort + 100; port += 1) {
        const candidate = endpointWithPort(endpoint, port);
        if (!(await isEndpointPortOpen(candidate))) return candidate;
    }
    throw new CanvasError("local_port_unavailable", `No free local port was found after ${basePort}.`);
}

async function cleanupStaleWorkspaceLocalPorts(state, endpoint) {
    if (!isLoopbackEndpoint(endpoint)) return;
    const basePort = endpointPort(endpoint) || 8088;
    const cleaned = [];
    for (let port = basePort; port < basePort + 100; port += 1) {
        const candidate = endpointWithPort(endpoint, port);
        if (!(await isEndpointPortOpen(candidate))) continue;
        const killed = await killLoopbackPortOwner(candidate, { workspaceRoot: process.cwd() });
        if (!killed) continue;
        if (await waitForEndpointPortClosed(candidate, 2000)) {
            cleaned.push(candidate);
        }
    }
    if (cleaned.length) {
        addLocalEvent(state, "", `Cleaned up stale local agent listener${cleaned.length === 1 ? "" : "s"}: ${cleaned.join(", ")}.`);
    }
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForLocalReadiness(state, { agent, endpoint, runId }) {
    const timeoutMs = Number.isFinite(state.localRun?.readiness?.timeoutMs)
        ? state.localRun.readiness.timeoutMs
        : 15000;
    const deadline = Date.now() + timeoutMs;
    let latest = null;
    let pollingEventAdded = false;
    while (Date.now() < deadline) {
        if (!state.localRun?.running || state.localRun?.runId !== runId) {
            return {
                ok: false,
                status: state.localRun?.exitCode ?? 0,
                durationMs: 0,
                body: "Local agent exited before it became ready.",
            };
        }
        if (state.localRun.readiness?.phase !== "polling_readiness") {
            state.localRun.readiness = { ...state.localRun.readiness, phase: "polling_readiness" };
        }
        if (!pollingEventAdded) {
            addLocalEvent(state, "", "Polling /readiness until the local agent is usable.");
            pollingEventAdded = true;
        }
        latest = await checkReadiness(endpoint, {
            timeoutMs: 1000,
            expectedAgentNames: readinessAgentNames(agent),
            requiredProtocols: [],
        });
        if (latest.ok) return latest;
        await sleep(300);
    }
    return latest || {
        ok: false,
        status: 0,
        durationMs: timeoutMs,
        body: "Timed out waiting for local readiness.",
    };
}

async function stopLocalAgent(state) {
    const child = state.localRun?.process;
    const pid = child?.pid;
    const endpoint = state.localRun?.endpoint;
    if (child && !child.killed) {
        await killLocalProcessTree(pid);
        await waitForProcessClose(child, 3000);
    }
    const events = [...(state.localRun?.events || []), { kind: "", text: "Local agent stopped.", at: new Date().toISOString() }];
    let released = await waitForEndpointPortClosed(endpoint, child ? 5000 : 250);
    if (endpoint && !released) {
        const killedByPort = await killLoopbackPortOwner(endpoint);
        if (killedByPort) {
            events.push({ kind: "", text: `Cleaned up stale local process on ${endpoint}.`, at: new Date().toISOString() });
            released = await waitForEndpointPortClosed(endpoint, 5000);
        }
    }
    if (endpoint && !released) {
        events.push({ kind: "warn", text: `${endpoint} is still in use after stopping local agent.`, at: new Date().toISOString() });
    }
    state.localRun = {
        ...state.localRun,
        running: false,
        completedAt: new Date().toISOString(),
        exitCode: state.localRun?.exitCode ?? null,
        log: [...(state.localRun?.log || []), "$ stopped local agent\n"],
        events: events.slice(-5),
        process: null,
        runId: null,
        readiness: state.localRun?.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "stopped", completedAt: new Date().toISOString() }
            : state.localRun?.readiness || null,
    };
    clearTargetHealth(state, "local");
}

async function killLocalProcessTree(pid) {
    if (!pid) return;
    if (process.platform !== "win32") {
        try {
            process.kill(pid, "SIGTERM");
        } catch {
            // The local runner already exited.
        }
        return;
    }
    const pidValue = Number.isInteger(pid) ? pid : 0;
    const script = `
$ErrorActionPreference = 'SilentlyContinue'
$ids = New-Object System.Collections.Generic.List[int]
if (${pidValue} -gt 0) {
  $queue = New-Object System.Collections.Generic.Queue[int]
  $queue.Enqueue(${pidValue})
  while ($queue.Count -gt 0) {
    $current = $queue.Dequeue()
    if (-not $ids.Contains($current)) { $ids.Add($current) }
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $current" | ForEach-Object {
      $queue.Enqueue([int]$_.ProcessId)
    }
  }
}
$ids | Sort-Object -Descending -Unique | ForEach-Object {
  Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}
`;
    await runCommand("powershell.exe", [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]);
}

function killLocalProcessTreeSync(pid) {
    if (!pid) return;
    if (process.platform !== "win32") {
        try {
            process.kill(pid, "SIGTERM");
        } catch {
            // The local runner already exited.
        }
        return;
    }
    const pidValue = Number.isInteger(pid) ? pid : 0;
    const script = `
$ErrorActionPreference = 'SilentlyContinue'
$ids = New-Object System.Collections.Generic.List[int]
if (${pidValue} -gt 0) {
  $queue = New-Object System.Collections.Generic.Queue[int]
  $queue.Enqueue(${pidValue})
  while ($queue.Count -gt 0) {
    $current = $queue.Dequeue()
    if (-not $ids.Contains($current)) { $ids.Add($current) }
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $current" | ForEach-Object {
      $queue.Enqueue([int]$_.ProcessId)
    }
  }
}
$ids | Sort-Object -Descending -Unique | ForEach-Object {
  Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}
`;
    spawnSync("powershell.exe", [
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ], { stdio: "ignore" });
}

async function waitForProcessClose(child, timeoutMs) {
    if (!child || child.killed || child.exitCode !== null || child.signalCode) return;
    await new Promise((resolve) => {
        const timeout = setTimeout(resolve, timeoutMs);
        child.once("close", () => {
            clearTimeout(timeout);
            resolve();
        });
    });
}

async function waitForEndpointPortClosed(endpoint, timeoutMs) {
    if (!endpoint || !isLoopbackEndpoint(endpoint)) return true;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (!(await isEndpointPortOpen(endpoint))) return true;
        await sleep(100);
    }
    return !(await isEndpointPortOpen(endpoint));
}

function powershellString(value) {
    return `'${String(value || "").replace(/'/g, "''")}'`;
}

async function killLoopbackPortOwner(endpoint, { workspaceRoot = null } = {}) {
    if (!endpoint || !isLoopbackEndpoint(endpoint) || process.platform !== "win32") return false;
    const port = endpointPort(endpoint);
    if (!port) return false;
    const workspaceFilter = workspaceRoot
        ? `
$workspaceRoot = ${powershellString(resolve(workspaceRoot))}
$owners = $owners | Where-Object {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $_"
  $proc -and (($proc.ExecutablePath -like "*$workspaceRoot*") -or ($proc.CommandLine -like "*$workspaceRoot*"))
}
`
        : "";
    const script = `
$ErrorActionPreference = 'SilentlyContinue'
$owners = Get-NetTCPConnection -LocalPort ${port} -State Listen |
  Where-Object { @('127.0.0.1','0.0.0.0','::','::1') -contains $_.LocalAddress } |
  Select-Object -ExpandProperty OwningProcess -Unique
${workspaceFilter}
$owners | Where-Object { $_ -and $_ -ne $PID } | ForEach-Object {
  Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
  $_
}
`;
    const result = await runPowerShell(script);
    return result.code === 0 && result.output.trim().length > 0;
}

async function runPowerShell(script) {
    const cwd = await commandRuntimeCwd();
    return new Promise((resolve) => {
        const child = spawn("powershell.exe", [
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ], { cwd, shell: false });
        const output = [];
        child.stdout.on("data", (chunk) => output.push(chunk.toString()));
        child.stderr.on("data", (chunk) => output.push(chunk.toString()));
        child.on("error", (error) => resolve({ code: 1, output: `${error.name}: ${error.message}\n` }));
        child.on("close", (code) => resolve({ code: code ?? 0, output: output.join("") }));
    });
}

function readinessAgentNames(agent) {
    return [agent.serviceName, agent.displayName].filter(Boolean);
}

function normalizedAgentName(name) {
    return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function findAgentByReadinessName(state, name) {
    const normalized = normalizedAgentName(name);
    if (!normalized) return null;
    return state.agents.find((agent) =>
        readinessAgentNames(agent).some((candidate) => normalizedAgentName(candidate) === normalized)
    ) || null;
}

function reconcileSelectedAgentFromReadiness(state, health, { source = "manual" } = {}) {
    const actual = health?.identity?.actual || health?.readiness?.agent?.name || null;
    const selected = selectedAgent(state);
    state.localRun.identityMismatch = null;
    if (!actual) return;

    const matchingAgent = findAgentByReadinessName(state, actual);
    if (!matchingAgent) {
        state.localRun.identityMismatch = {
            expected: selected.displayName || selected.serviceName,
            actual,
            canSwitch: false,
            message: `Endpoint belongs to ${actual}, which was not discovered in azure.yaml.`,
        };
        return;
    }

    if (matchingAgent.id === selected.id) return;

    state.localRun.identityMismatch = {
        expected: selected.displayName || selected.serviceName,
        actual,
        agentId: matchingAgent.id,
        canSwitch: true,
        source,
        message: `Endpoint belongs to ${actual}; selected ${selected.displayName || selected.serviceName}.`,
    };
}

async function selectAgent(state, agentId) {
    const previousAgent = selectedAgent(state);
    const nextAgent = state.agents.find((candidate) => candidate.id === agentId) || state.agents[0];
    if (nextAgent?.id !== previousAgent.id && (state.localRun?.running || state.localRun?.process)) {
        await stopLocalAgent(state);
    }
    return switchSelectedAgent(state, agentId);
}

function setLocalEndpoint(state, endpoint) {
    const normalized = normalizeEndpoint(endpoint);
    const current = selectedLocalEndpoint(state);
    state.localEndpoints[state.selectedAgentId] = normalized;
    if (!state.localRun?.running && state.localRun?.agentId === selectedAgent(state).id) {
        state.localRun = emptyLocalRun();
    }
    setTarget(state, "local");
    clearProjectEndpointPrompt(state);
    if (normalized !== current) {
        clearTargetHealth(state, "local");
    }
}

async function refreshHostedContext(state) {
    const agent = selectedAgent(state);
    const result = await runCommand("azd", ["env", "get-values"], { cwd: agent.root });
    const values = parseAzdEnv(result.output);
    const now = new Date();
    const currentHosted = state.hostedByAgent[agent.id] || emptyHostedContext(agent);
    let hosted = hostedContextFromAzd({
        agent,
        currentHosted,
        foundryConnection: state.foundryConnection,
        values,
        exitCode: result.code,
        now,
    });
    try {
        const remote = await discoverHostedContextFromFoundry({
            agent,
            currentHosted: hosted,
            foundryConnection: state.foundryConnection,
            accessTokenProvider: azureAccessToken,
            now,
        });
        if (remote.attempted) hosted = remote.hosted;
    } catch (error) {
        hosted = {
            ...hosted,
            lastRefresh: now.toISOString(),
            lastRemoteDiscoveryStatus: "error",
            lastRemoteDiscoveryMessage: error instanceof Error ? error.message : String(error),
        };
    }
    state.hostedByAgent[agent.id] = hosted;
    state.hosted = state.hostedByAgent[agent.id];
    if (state.hosted.projectEndpoint && state.hosted.modelDeployment) {
        state.foundryConnection = {
            ...state.foundryConnection,
            projectEndpoint: state.hosted.projectEndpoint,
            modelDeployment: state.hosted.modelDeployment,
        };
    }
    return { result, values };
}

async function hydrateFoundryConnectionFromAzd(state) {
    const agent = selectedAgent(state);
    const result = await runCommand("azd", ["env", "get-values"], { cwd: agent.root });
    if (result.code !== 0) return;
    const values = parseAzdEnv(result.output);
    const projectEndpoint =
        values.FOUNDRY_PROJECT_ENDPOINT ||
        values.AZURE_AI_PROJECT_ENDPOINT ||
        values.AZURE_AIPROJECT_ENDPOINT ||
        null;
    const modelDeployment =
        values.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        values.AZURE_OPENAI_DEPLOYMENT_NAME ||
        null;
    if (!projectEndpoint && !modelDeployment) return;
    const prefix = agent.envPrefix;

    state.foundryConnection = {
        ...state.foundryConnection,
        projectEndpoint: projectEndpoint || state.foundryConnection.projectEndpoint,
        modelDeployment: modelDeployment || state.foundryConnection.modelDeployment,
        subscriptionId: values.AZURE_SUBSCRIPTION_ID || state.foundryConnection.subscriptionId,
        location: values.AZURE_LOCATION || state.foundryConnection.location,
        projectId: values.AZURE_AI_PROJECT_ID || state.foundryConnection.projectId,
        accountName: values.AZURE_AI_ACCOUNT_NAME || state.foundryConnection.accountName,
        projectName: values.AZURE_AI_PROJECT_NAME || state.foundryConnection.projectName,
        connectedAt: new Date().toISOString(),
        lastConnectExitCode: 0,
        lastDiscoveryMessage: "Loaded Foundry project from the selected azd environment.",
    };

    const hosted = state.hostedByAgent[agent.id] || emptyHostedContext(agent);
    state.hostedByAgent[agent.id] = {
        ...hosted,
        agentName: values[`${prefix}_NAME`] || hosted.agentName,
        agentId: values[`${prefix}_ID`] || hosted.agentId,
        version: values[`${prefix}_VERSION`] || hosted.version,
        responsesEndpoint: values[`${prefix}_RESPONSES_ENDPOINT`] || hosted.responsesEndpoint,
        activityEndpoint: values[`${prefix}_ACTIVITY_ENDPOINT`] || hosted.activityEndpoint,
        invocationsEndpoint: values[`${prefix}_INVOCATIONS_ENDPOINT`] || hosted.invocationsEndpoint,
        projectEndpoint: projectEndpoint || hosted.projectEndpoint,
        modelDeployment: modelDeployment || hosted.modelDeployment,
        lastRefresh: new Date().toISOString(),
        lastRefreshExitCode: 0,
    };
    state.hosted = state.hostedByAgent[agent.id];
}

async function hydrateFoundryConnectionFromDotEnv(state) {
    const agent = selectedAgent(state);
    const env = await refreshLocalEnvState(state);
    const projectEndpoint =
        env.values.FOUNDRY_PROJECT_ENDPOINT ||
        env.values.AZURE_AI_PROJECT_ENDPOINT ||
        env.values.AZURE_AIPROJECT_ENDPOINT ||
        null;
    const modelDeployment =
        env.values.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
        env.values.AZURE_OPENAI_DEPLOYMENT_NAME ||
        null;
    if (!projectEndpoint && !modelDeployment) return;
    state.foundryConnection = {
        ...state.foundryConnection,
        projectEndpoint: projectEndpoint || state.foundryConnection.projectEndpoint,
        modelDeployment: modelDeployment || state.foundryConnection.modelDeployment,
        connectedAt: new Date().toISOString(),
        lastConnectExitCode: 0,
        lastDiscoveryMessage: "Loaded Foundry project from .env.",
    };
    const hosted = state.hostedByAgent[agent.id] || emptyHostedContext(agent);
    state.hostedByAgent[agent.id] = {
        ...hosted,
        projectEndpoint: projectEndpoint || hosted.projectEndpoint,
        modelDeployment: modelDeployment || hosted.modelDeployment,
    };
    state.hosted = state.hostedByAgent[agent.id];
}

async function hydrateFoundryConnection(state) {
    await hydrateFoundryConnectionFromAzd(state);
    await hydrateFoundryConnectionFromDotEnv(state);
    if (state.foundryConnection.projectEndpoint) {
        await refreshHostedContext(state);
    }
}

async function connectFoundry(state, { projectEndpoint, modelDeployment }) {
    const agent = selectedAgent(state);
    const endpoint = normalizeFoundryProjectEndpoint(projectEndpoint);
    const deployment = String(modelDeployment || "").trim();
    if (!deployment) {
        throw new CanvasError("model_deployment_required", "Enter the model deployment name.");
    }
    const log = state.deployment.log;
    const discovery = await discoverManagementContext(agent, endpoint);
    const exitCode = await setAzdEnvValues(
        agent.root,
        {
            FOUNDRY_PROJECT_ENDPOINT: endpoint,
            AZURE_AI_MODEL_DEPLOYMENT_NAME: deployment,
            AZURE_SUBSCRIPTION_ID: discovery.subscriptionId,
            AZURE_LOCATION: discovery.location,
            AZURE_AI_PROJECT_ID: discovery.projectId,
            AZURE_RESOURCE_GROUP: discovery.resourceGroup,
            AZURE_TENANT_ID: await discoverTenantId(agent),
            AZURE_AI_ACCOUNT_NAME: discovery.accountName,
            AZURE_AI_PROJECT_NAME: discovery.projectName,
        },
        log,
    );
    state.foundryConnection = {
        projectEndpoint: endpoint,
        modelDeployment: deployment,
        subscriptionId: discovery.subscriptionId || null,
        location: discovery.location || null,
        projectId: discovery.projectId || null,
        accountName: discovery.accountName || null,
        projectName: discovery.projectName || null,
        connectedAt: new Date().toISOString(),
        lastConnectExitCode: exitCode,
        lastDiscoveryMessage: discovery.message || null,
    };
    state.hosted.projectEndpoint = endpoint;
    state.hosted.modelDeployment = deployment;
    state.deployment.needsProvision = false;
    if (discovery.message) {
        state.deployment.log.push(`${discovery.message}\n`);
    }
    return { discovery, exitCode };
}

async function ensureAzdDeploymentContext(state, log, { stampDeployment = false } = {}) {
    const agent = selectedAgent(state);
    const endpoint =
        state.foundryConnection.projectEndpoint ||
        state.hosted.projectEndpoint ||
        null;
    const deployment =
        state.foundryConnection.modelDeployment ||
        state.hosted.modelDeployment ||
        null;
    if (!endpoint || !deployment) return 0;

    const discovery = await discoverManagementContext(agent, endpoint);
    const values = {
        FOUNDRY_PROJECT_ENDPOINT: endpoint,
        AZURE_AI_PROJECT_ENDPOINT: endpoint,
        AZURE_AIPROJECT_ENDPOINT: endpoint,
        AZURE_AI_MODEL_DEPLOYMENT_NAME: deployment,
        AZURE_SUBSCRIPTION_ID: discovery.subscriptionId,
        AZURE_LOCATION: discovery.location,
        AZURE_AI_PROJECT_ID: discovery.projectId,
        AZURE_RESOURCE_GROUP: discovery.resourceGroup,
        AZURE_TENANT_ID: await discoverTenantId(agent),
        AZURE_AI_ACCOUNT_NAME: discovery.accountName,
        AZURE_AI_PROJECT_NAME: discovery.projectName,
    };
    if (stampDeployment) {
        values.CASTIA_DEPLOYMENT_STAMP = await deploymentStamp(agent.root);
    }
    const exitCode = await setAzdEnvValues(agent.root, values, log);
    state.foundryConnection = {
        ...state.foundryConnection,
        subscriptionId: discovery.subscriptionId || state.foundryConnection.subscriptionId,
        location: discovery.location || state.foundryConnection.location,
        projectId: discovery.projectId || state.foundryConnection.projectId,
        accountName: discovery.accountName || state.foundryConnection.accountName,
        projectName: discovery.projectName || state.foundryConnection.projectName,
        lastConnectExitCode: exitCode,
        lastDiscoveryMessage: discovery.message || state.foundryConnection.lastDiscoveryMessage,
    };
    if (discovery.message) {
        log?.push(`${discovery.message}\n`);
    }
    return exitCode;
}

function azdLifecyclePhase(text, commandName) {
    const value = String(text || "");
    if (/Uploading|Registering|Creating agent|Updating agent|Polling|Waiting for deployment|Remote/i.test(value)) {
        return { phase: "remote_registration", irreversible: true };
    }
    if (/Packaging|Building|Preparing|Resolving/i.test(value)) {
        return { phase: "prepare_artifacts", irreversible: false };
    }
    if (/Provisioning|Deploying/i.test(value)) {
        return { phase: commandName === "deploy" ? "deploying" : "provisioning", irreversible: false };
    }
    return null;
}

async function streamAzdLifecycle(res, state, { commandName, args }) {
    res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        Connection: "keep-alive",
    });
    const started = beginOperation(state, {
        kind: commandName,
        actor: "Canvas",
        phase: "starting",
        cancellable: true,
    });
    if (!started.started) {
        writeEvent(res, "snapshot", snapshotState(state));
        writeEvent(res, "operation", { duplicate: true, operation: started.operation });
        res.end();
        return;
    }
    recordOperation(state, "started", { commandName, args });
    const agent = selectedAgent(state);
    const abortController = new AbortController();
    state.deployment = {
        running: true,
        exitCode: null,
        startedAt: new Date().toISOString(),
        completedAt: null,
        command: `azd ${args.join(" ")}`,
        needsProvision: false,
        log: [`$ azd ${args.join(" ")}\n`],
        process: null,
        abortController,
    };
    if (commandName === "deploy") {
        clearMessagesForTarget(state, "hosted");
    }
    try {
        if (commandName === "deploy" || commandName === "provision") {
            updateOperation(state, { phase: "configure_environment" });
            await ensureAzdDeploymentContext(state, state.deployment.log, { stampDeployment: commandName === "deploy" });
        }
        writeEvent(res, "snapshot", snapshotState(state));
        const previousVersion = commandName === "deploy" ? state.hosted?.version : null;
        updateOperation(state, { phase: "run_azd" });
        const result = state.operations?.active?.status === "cancel_requested" && !state.operations.active.irreversible
            ? { code: 130, output: "Command cancelled before start.\n", cancelled: true }
            : await runCommand("azd", args, {
                cwd: agent.root,
                signal: abortController.signal,
                onChild: (child) => {
                    state.deployment.process = child;
                },
                onOutput: (text) => {
                    state.deployment.log.push(text);
                    const phase = azdLifecyclePhase(text, commandName);
                    if (phase?.irreversible) {
                        enterIrreversiblePhase(state, phase.phase);
                    } else if (phase?.phase) {
                        updateOperation(state, { phase: phase.phase });
                    }
                    if (state.operations?.active?.status === "cancel_requested" && !state.operations.active.irreversible) {
                        abortController.abort();
                    }
                    writeEvent(res, "snapshot", snapshotState(state));
                },
            });
        if (result.cancelled && !state.deployment.log.join("").includes("Command cancelled")) {
            state.deployment.log.push(result.output || "Command cancelled.\n");
        }
        const output = state.deployment.log.join("");
        state.deployment.exitCode = result.code;
        state.deployment.needsProvision =
            commandName === "deploy" &&
            result.code !== 0 &&
            /infrastructure has not been provisioned|Run 'azd provision'/i.test(output);
        if (state.deployment.needsProvision) {
            state.deployment.log.push("\nNext: prepare this repo for hosted deployment, then deploy again.\n");
        }
        if (!result.cancelled && (commandName === "provision" || result.code === 0)) {
            state.deployment.log.push(`\n$ azd env get-values\n`);
            const refresh = await refreshHostedContext(state);
            state.deployment.log.push(refresh.result.output || "(no output)\n");
            if (state.hosted.lastRemoteDiscoveryStatus || state.hosted.lastRemoteDiscoveryMessage) {
                state.deployment.log.push(
                    `Foundry discovery: ${state.hosted.lastRemoteDiscoveryStatus || "unknown"}${state.hosted.lastRemoteDiscoveryMessage ? ` — ${state.hosted.lastRemoteDiscoveryMessage}` : ""}\n`,
                );
            }
            if (commandName === "deploy" && previousVersion && state.hosted.version === previousVersion) {
                state.deployment.log.push(
                    `\nDeploy completed, but Foundry discovery still reports version ${previousVersion}. The hosted agent service may have reused the existing version; send a hosted prompt only if you expect that version to contain the latest package.\n`,
                );
            }
        }
        if (commandName === "provision" && result.code === 0) {
            state.deployment.log.push("\nDeploy prep complete. Deploy is ready.\n");
        }
        const cancellationMode = state.operations?.active?.cancellation?.mode || null;
        const terminal = completeOperation(state, {
            status: cancellationMode === "stop_requested" && result.cancelled ? "cancelled" : result.code === 0 ? "completed" : "failed",
            exitCode: result.code,
            summary: `${commandName} exited with code ${result.code}.`,
        });
        recordOperation(state, "completed", { commandName, exitCode: result.code, operation: terminal });
    } catch (error) {
        state.deployment.exitCode = state.deployment.exitCode ?? 1;
        state.deployment.log.push(`\nERROR: ${error instanceof Error ? error.message : String(error)}\n`);
        const cancellationMode = state.operations?.active?.cancellation?.mode || null;
        const terminal = completeOperation(state, {
            status: cancellationMode === "stop_requested" ? "cancelled" : "failed",
            exitCode: state.deployment.exitCode,
            summary: error instanceof Error ? error.message : String(error),
        });
        recordOperation(state, "failed", {
            commandName,
            error: error instanceof Error ? error.message : String(error),
            operation: terminal,
        });
    } finally {
        state.deployment.running = false;
        state.deployment.completedAt = new Date().toISOString();
        delete state.deployment.process;
        delete state.deployment.abortController;
        writeEvent(res, "snapshot", snapshotState(state));
        res.end();
    }
}

function snapshotState(state) {
    const snapshot = stateSnapshot(state);
    void persistStateSnapshot(state, snapshot).catch(() => {});
    return snapshot;
}

function recordOperation(state, event, details = {}) {
    const { operation, ...rest } = details;
    void appendOperationRecord(state, {
        event,
        operation: operation || state.operations?.active || null,
        details: rest,
    }).catch(() => {});
}

const {
    commandSelectAgent,
    commandStartLocal,
    commandCancelOperation,
    commandBootstrapLocalEnv,
} = createPlaygroundCommands({
    selectAgent,
    refreshHostedContext,
    snapshotState,
    syncLocalBootstrapForStart,
    startLocalAgent,
    bootstrapLocalEnv,
    recordOperation,
});

function openSnapshotStream(req, res, state) {
    res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        Connection: "keep-alive",
    });
    state.eventClients.add(res);
    writeEvent(res, "snapshot", snapshotState(state));
    req.on("close", () => {
        state.eventClients.delete(res);
    });
}

function broadcastSnapshot(state) {
    for (const client of state.eventClients) {
        try {
            writeEvent(client, "snapshot", snapshotState(state));
        } catch {
            state.eventClients.delete(client);
        }
    }
}

function pendingResponse() {
    return {
        ok: false,
        status: "waiting",
        durationMs: 0,
        body: { output_text: "" },
        streaming: true,
        delivery: {
            mode: "pending",
            upstreamStreaming: false,
            active: true,
        },
    };
}

function responseTurn(state, input, { stream = false } = {}) {
    return {
        input,
        createdAt: new Date().toISOString(),
        target: state.target,
        protocol: "responses",
        request: { endpoint: activeProtocolEndpoint(state, "responses") || activeEndpoint(state), path: "/responses", body: { input, ...(stream ? { stream: true } : {}) } },
        response: pendingResponse(),
    };
}

function invocationTurn(state, input) {
    return {
        input,
        createdAt: new Date().toISOString(),
        target: state.target,
        protocol: "invocations",
        request: { endpoint: activeProtocolEndpoint(state, "invocations"), path: "/invocations", body: { message: input } },
        response: pendingResponse(),
    };
}

function completedResponseResult(state, turn) {
    const pending = Boolean(responsePendingLabel(turn.response));
    return {
        turn,
        pending,
        final: !pending,
        displayText: responseDisplayText(turn.response),
        copyableText: copyableAnswerText(turn.response),
        transcript: transcriptState(state),
        state: snapshotState(state),
    };
}

async function completeResponseTurn(state, turn, activityId = null) {
    turn.response = await callAgent(turn.request.endpoint, "", { input: turn.input });
    const displayText = responseDisplayText(turn.response);
    updateActivity(state, activityId, {
        status: turn.response.ok ? "completed" : "failed",
        summary: turn.response.ok
            ? `Response completed (${turn.response.status}).`
            : `Response failed (${turn.response.status || "error"}).`,
        details: { displayText, status: turn.response.status, durationMs: turn.response.durationMs },
    });
    return completedResponseResult(state, turn);
}

async function completeActivityTurn(state, turn, activityId = null) {
    const result = await callAgent(turn.request.endpoint, "", turn.request.body);
    finishActivityTurn(turn, result);
    const displayText = responseDisplayText(turn.response);
    updateActivity(state, activityId, {
        status: turn.response.ok ? "completed" : "failed",
        summary: turn.response.ok
            ? `Activity turn acknowledged (${turn.response.status}).`
            : `Activity turn failed (${turn.response.status || "error"}).`,
        details: { displayText, status: turn.response.status, durationMs: turn.response.durationMs, events: turn.events },
    });
    return completedResponseResult(state, turn);
}

async function completeInvocationTurn(state, turn, activityId = null) {
    turn.response = await callAgent(turn.request.endpoint, "", turn.request.body);
    const displayText = responseDisplayText(turn.response);
    updateActivity(state, activityId, {
        status: turn.response.ok ? "completed" : "failed",
        summary: turn.response.ok
            ? `Invocation completed (${turn.response.status}).`
            : `Invocation failed (${turn.response.status || "error"}).`,
        details: { displayText, status: turn.response.status, durationMs: turn.response.durationMs },
    });
    return completedResponseResult(state, turn);
}

async function startServer(ctx, copilotSession) {
    const agents = await discoverAgents();
    const selectedAgentId = ctx.input?.agentId && agents.some((agent) => agent.id === ctx.input.agentId)
        ? ctx.input.agentId
        : agents[0].id;
    const selected = agents.find((agent) => agent.id === selectedAgentId) || agents[0];
    const hosted = emptyHostedContext(selected);
    const state = {
        target: "local",
        activeProtocol: "responses",
        agents,
        selectedAgentId,
        localEndpoints: { [selectedAgentId]: normalizeEndpoint(ctx.input?.endpoint) },
        hostedByAgent: { [selectedAgentId]: hosted },
        hosted,
        instanceId: ctx.instanceId,
        foundryConnection: emptyFoundryConnection(),
        envBootstrap: null,
        deployment: {
            running: false,
            exitCode: null,
            startedAt: null,
            completedAt: null,
            command: null,
            needsProvision: false,
            log: [],
        },
        localRun: emptyLocalRun(),
        teams: {},
        messages: [],
        lastHealth: null,
        lastHealthByTarget: {},
        projectEndpointPrompt: null,
        operations: emptyOperationState(),
        eventClients: new Set(),
    };
    configureRuntimeStore(state, {
        sessionId: copilotSession?.sessionId || ctx.sessionId || process.env.SESSION_ID || process.env.COPILOT_SESSION_ID,
        instanceId: ctx.instanceId,
    });
    const persisted = await readStateSnapshot(state);
    if (persisted.snapshot) {
        rehydratePlaygroundState(state, persisted.snapshot, { input: ctx.input || {} });
    } else if (persisted.error) {
        addActivity(state, {
            actor: "Canvas",
            kind: "restore_state",
            status: "warn",
            summary: "Could not restore previous Playground state; starting fresh.",
            details: { path: persisted.path, error: persisted.error.message },
        });
    }
    await hydrateFoundryConnection(state);
    snapshotState(state);
    const handleRequest = createRequestHandler({
        extensionRoot: EXTENSION_ROOT,
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
        setActiveProtocol,
        protocolState,
        commandStartLocal,
        stopLocalAgent,
        createActivityTurn,
        completeActivityTurn,
        invocationTurn,
        completeInvocationTurn,
        responseTurn,
        completeResponseTurn,
        commandCancelOperation,
        streamAzdLifecycle,
        broadcastSnapshot,
    });
    const server = createServer((req, res) => {
        void handleRequest(req, res, state);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    state.connectorBaseUrl = `http://127.0.0.1:${port}/connector`;
    state.activeProtocol = protocolState(state).activeProtocol;
    return { server, state, url: `http://127.0.0.1:${port}/` };
}

let copilotSession;
copilotSession = await joinSession({
    systemMessage: {
        mode: "append",
        content: [
            "When using the Foundry Agent Playground canvas and Foundry project values are missing, clicking Start local opens an in-canvas question box that asks the user for the Foundry project endpoint.",
            "Do not ask for the project endpoint in chat and do not tell the user to paste it manually; the Start local button is the only supported endpoint-entry path.",
            "After the user provides the endpoint in the canvas dialog, the canvas writes non-secret Foundry values into gitignored .env files.",
        ].join("\n"),
    },
    canvases: [
        createCanvas({
            id: "foundry-agent-playground",
            displayName: "Foundry Agent Playground",
            description: "Chat with an agent through the Responses protocol.",
            inputSchema: {
                type: "object",
                properties: {
                    agentId: {
                        type: "string",
                        description: "Optional discovered agent id to select when the canvas opens.",
                    },
                    endpoint: {
                        type: "string",
                        description: "Base URL of the agent, for example http://127.0.0.1:8088.",
                    },
                },
                additionalProperties: false,
            },
            actions: createCanvasActions({
                CanvasError,
                instanceState,
                commandSelectAgent,
                commandStartLocal,
                stopLocalAgent,
                commandCancelOperation,
                commandBootstrapLocalEnv,
                setLocalEndpoint,
                readinessAgentNames,
                reconcileSelectedAgentFromReadiness,
                reconcileLocalReadinessAfterHealth,
                localStartupStillPending,
                setActiveProtocol,
                protocolState,
                createActivityTurn,
                completeActivityTurn,
                invocationTurn,
                completeInvocationTurn,
                responseTurn,
                completeResponseTurn,
                completedResponseResult,
                snapshotState,
                broadcastSnapshot,
            }),
            open: async (ctx) => {
                let entry = servers.get(ctx.instanceId);
                if (!entry) {
                    entry = await startServer(ctx, copilotSession);
                    servers.set(ctx.instanceId, entry);
                } else if (ctx.input?.endpoint) {
                    entry.state.localEndpoints[entry.state.selectedAgentId] = normalizeEndpoint(ctx.input.endpoint);
                }
                return {
                    icon: ICON_PATH,
                    title: "Foundry Agent Playground",
                    status: activeEndpoint(entry.state),
                    url: entry.url,
                };
            },
            onClose: async (ctx) => {
                const entry = servers.get(ctx.instanceId);
                if (entry) {
                    if (entry.state.localRun?.process && !entry.state.localRun.process.killed) {
                        await stopLocalAgent(entry.state);
                    }
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});

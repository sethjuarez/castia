import { spawn } from "node:child_process";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createConnection } from "node:net";
import { basename, delimiter, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import {
    callAgent,
    callAgentStream,
    checkReadiness,
    configureAgentClient,
    azureAccessToken,
    isLoopbackEndpoint,
    responseText,
} from "./agent-client.mjs";
import { DEFAULT_ENDPOINT, DEFAULT_MODEL_DEPLOYMENT, DEFAULT_TOOLBOX_NAME } from "./constants.mjs";
import { renderHtml } from "./renderer.mjs";
import {
    activeEndpoint,
    addActivity,
    addLocalEvent,
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
    setSelectedAgent,
    switchSelectedAgent,
    responseDisplayText,
    responsePendingLabel,
    stateSnapshot,
    transcriptState,
    updateActivity,
} from "./state.mjs";
import { discoverAgents, serviceEnvPrefix } from "./agent-discovery.mjs";
import {
    discoverHostedContextFromFoundry,
    hostedContextFromAzd,
} from "./hosted-discovery.mjs";

const EXTENSION_ROOT = dirname(fileURLToPath(import.meta.url));
const ICON_PATH = join(EXTENSION_ROOT, "assets", "castia-mark.png");
const servers = new Map();

function cleanupManagedLocalRuns() {
    for (const entry of servers.values()) {
        const child = entry.state?.localRun?.process;
        if (child && !child.killed) {
            child.kill();
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

function runCommand(command, args, { cwd = process.cwd(), onOutput } = {}) {
    return new Promise((resolve) => {
        const output = [];
        const child = spawn(command, args, {
            cwd,
            shell: process.platform === "win32",
            env: { ...process.env, AZURE_DEV_USER_AGENT: "agent_playground" },
        });
        const append = (chunk) => {
            const text = chunk.toString();
            output.push(text);
            onOutput?.(text);
        };
        child.stdout.on("data", append);
        child.stderr.on("data", append);
        child.on("error", (error) => {
            const text = `${error.name}: ${error.message}`;
            output.push(text);
            onOutput?.(text);
            resolve({ code: 1, output: output.join("") });
        });
        child.on("close", (code) => {
            resolve({ code: code ?? 0, output: output.join("") });
        });
    });
}

configureAgentClient({ runCommand });

async function localStartCommand(agent) {
    if (await exists(join(agent.root, "main.py"))) {
        const localVenv = process.platform === "win32"
            ? join(agent.root, ".venv", "Scripts", "python.exe")
            : join(agent.root, ".venv", "bin", "python");
        const workspaceVenv = process.platform === "win32"
            ? join(process.cwd(), ".venv", "Scripts", "python.exe")
            : join(process.cwd(), ".venv", "bin", "python");
        const pythonPath = await discoverPythonPath(agent.root);
        if (!(await exists(localVenv)) && !(await exists(workspaceVenv)) && pythonPath) {
            const packageRoot = dirname(pythonPath);
            return {
                command: "uv",
                args: [
                    "run",
                    "--project",
                    packageRoot,
                    "--with-editable",
                    packageRoot,
                    "--extra",
                    "deploy",
                    "--extra",
                    "optimize",
                    "--extra",
                    "test",
                    "python",
                    "main.py",
                ],
                env: {},
            };
        }
        const command = await exists(localVenv) ? localVenv : await exists(workspaceVenv) ? workspaceVenv : "python";
        return {
            command,
            args: ["main.py"],
            env: pythonPath
                ? { PYTHONPATH: [pythonPath, process.env.PYTHONPATH].filter(Boolean).join(delimiter) }
                : {},
        };
    }
    return null;
}

async function discoverPythonPath(start) {
    let current = start;
    while (true) {
        const sourceRoot = join(current, "packages", "python", "src");
        if (await exists(join(sourceRoot, "castia", "__init__.py"))) return sourceRoot;
        const parent = dirname(current);
        if (parent === current) return null;
        current = parent;
    }
}

async function startLocalAgent(state) {
    if (state.localRun?.running) return;
    state.lastHealth = null;
    const agent = selectedAgent(state);
    const local = await localStartCommand(agent);
    if (!local) {
        throw new CanvasError("local_start_unsupported", "No local start command was discovered for this agent.");
    }
    let endpoint = state.localEndpoints[agent.id] || selectedLocalEndpoint(state);
    const requestedEndpoint = endpoint;
    let portWarning = null;
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
    const readinessDeadlineAt = new Date(Date.now() + 15000).toISOString();
    state.localRun = {
        running: true,
        command: `${local.command} ${local.args.join(" ")}`,
        startedAt: new Date().toISOString(),
        completedAt: null,
        exitCode: null,
        log: [`$ ${local.command} ${local.args.join(" ")}\n`],
        events: state.localRun?.events || [],
        process: null,
        agentId: agent.id,
        agentName: agent.serviceName,
        endpoint,
        runId,
        readiness: { status: "starting", deadlineAt: readinessDeadlineAt },
        requestedEndpoint,
        portWarning,
        identityMismatch: null,
    };
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
        cwd: agent.root,
        shell: false,
        env: childEnv,
    });
    state.localRun.process = child;
    const append = (chunk) => {
        if (state.localRun?.runId === runId) state.localRun.log.push(chunk.toString());
    };
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (error) => {
        if (state.localRun?.runId !== runId) return;
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = 1;
        state.localRun.readiness = state.localRun.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "failed", completedAt: new Date().toISOString() }
            : state.localRun.readiness;
        state.lastHealth = null;
        state.localRun.log.push(`${error.name}: ${error.message}\n`);
        addLocalEvent(state, "fail", error.message);
    });
    child.on("close", (code) => {
        if (state.localRun?.runId !== runId) return;
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = code ?? 0;
        state.localRun.readiness = state.localRun.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "stopped", completedAt: new Date().toISOString() }
            : state.localRun.readiness;
        state.lastHealth = null;
        addLocalEvent(state, code === 0 ? "" : "fail", code === 0 ? "Local agent stopped." : `Local agent exited with code ${code ?? 0}.`);
        delete state.localRun.process;
    });
    waitForLocalReadiness(state, { agent, endpoint, runId }).then((health) => {
        if (state.localRun?.runId !== runId || !state.localRun?.running) return;
        state.lastHealth = { ...health, source: "startup" };
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

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForLocalReadiness(state, { agent, endpoint, runId }) {
    const deadline = Date.now() + 15000;
    let latest = null;
    while (Date.now() < deadline) {
        if (!state.localRun?.running || state.localRun?.runId !== runId) {
            return {
                ok: false,
                status: state.localRun?.exitCode ?? 0,
                durationMs: 0,
                body: "Local agent exited before it became ready.",
            };
        }
        latest = await checkReadiness(endpoint, { timeoutMs: 1000, expectedAgentNames: readinessAgentNames(agent) });
        if (latest.ok) return latest;
        await sleep(300);
    }
    return latest || {
        ok: false,
        status: 0,
        durationMs: 15000,
        body: "Timed out waiting for local readiness.",
    };
}

async function stopLocalAgent(state) {
    const child = state.localRun?.process;
    const pid = child?.pid;
    if (child && !child.killed) {
        await killLocalProcessTree(pid);
        await waitForProcessClose(child, 3000);
    }
    state.localRun = {
        ...state.localRun,
        running: false,
        completedAt: new Date().toISOString(),
        exitCode: state.localRun?.exitCode ?? null,
        log: [...(state.localRun?.log || []), "$ stopped local agent\n"],
        events: [...(state.localRun?.events || []), { kind: "", text: "Local agent stopped.", at: new Date().toISOString() }].slice(-5),
        process: null,
        runId: null,
        readiness: state.localRun?.readiness?.status === "starting"
            ? { ...state.localRun.readiness, status: "stopped", completedAt: new Date().toISOString() }
            : state.localRun?.readiness || null,
    };
    state.lastHealth = null;
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

function readinessAgentNames(agent) {
    return [agent.serviceName, agent.displayName].filter(Boolean);
}

function setProjectEndpointPrompt(state, reason = "missing_configuration") {
    state.projectEndpointPrompt = {
        open: true,
        reason,
        selectedAgentId: state.selectedAgentId,
        requestedAt: new Date().toISOString(),
        message: "Foundry project endpoint is required before starting local.",
    };
    return state.projectEndpointPrompt;
}

function clearProjectEndpointPrompt(state) {
    state.projectEndpointPrompt = null;
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
    state.target = "local";
    clearProjectEndpointPrompt(state);
    if (normalized !== current) {
        state.lastHealth = null;
    }
}

async function commandSelectAgent(state, agentId, { actor = "Canvas" } = {}) {
    const agent = await selectAgent(state, agentId);
    await refreshHostedContext(state);
    addActivity(state, {
        actor,
        kind: "select_agent",
        status: "completed",
        summary: `Selected ${agent.displayName || agent.serviceName}.`,
        details: { selectedAgent: agent },
    });
    return stateSnapshot(state);
}

async function commandStartLocal(state, { actor = "Canvas" } = {}) {
    state.target = "local";
    const sync = await syncLocalBootstrapForStart(state);
    if (!sync.ok) {
        const prompt = setProjectEndpointPrompt(state);
        addActivity(state, {
            actor,
            kind: "start_local",
            status: "blocked",
            summary: prompt.message,
            details: { prompt, sync },
        });
        return {
            ok: false,
            needsProjectEndpoint: true,
            projectEndpointPrompt: prompt,
            state: stateSnapshot(state),
        };
    }
    clearProjectEndpointPrompt(state);
    await startLocalAgent(state);
    state.target = "local";
    addActivity(state, {
        actor,
        kind: "start_local",
        status: "running",
        summary: "Local agent start requested.",
        details: { endpoint: selectedLocalEndpoint(state), selectedAgent: selectedAgent(state) },
    });
    return { ok: true, state: stateSnapshot(state) };
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

async function streamAzdLifecycle(res, state, { commandName, args }) {
    res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        Connection: "keep-alive",
    });
    const agent = selectedAgent(state);
    state.deployment = {
        running: true,
        exitCode: null,
        startedAt: new Date().toISOString(),
        completedAt: null,
        command: `azd ${args.join(" ")}`,
        needsProvision: false,
        log: [`$ azd ${args.join(" ")}\n`],
    };
    if (commandName === "deploy") {
        clearMessagesForTarget(state, "hosted");
    }
    try {
        if (commandName === "deploy" || commandName === "provision") {
            await ensureAzdDeploymentContext(state, state.deployment.log, { stampDeployment: commandName === "deploy" });
        }
        writeEvent(res, "snapshot", stateSnapshot(state));
        const previousVersion = commandName === "deploy" ? state.hosted?.version : null;
        const result = await runCommand("azd", args, {
            cwd: agent.root,
            onOutput: (text) => {
                state.deployment.log.push(text);
                writeEvent(res, "snapshot", stateSnapshot(state));
            },
        });
        const output = state.deployment.log.join("");
        state.deployment.exitCode = result.code;
        state.deployment.needsProvision =
            commandName === "deploy" &&
            result.code !== 0 &&
            /infrastructure has not been provisioned|Run 'azd provision'/i.test(output);
        if (state.deployment.needsProvision) {
            state.deployment.log.push("\nNext: prepare this repo for hosted deployment, then deploy again.\n");
        }
        if (commandName === "provision" || result.code === 0) {
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
    } catch (error) {
        state.deployment.exitCode = state.deployment.exitCode ?? 1;
        state.deployment.log.push(`\nERROR: ${error instanceof Error ? error.message : String(error)}\n`);
    } finally {
        state.deployment.running = false;
        state.deployment.completedAt = new Date().toISOString();
        writeEvent(res, "snapshot", stateSnapshot(state));
        res.end();
    }
}

function writeEvent(res, name, payload) {
    res.write(`event: ${name}\n`);
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function openSnapshotStream(req, res, state) {
    res.writeHead(200, {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-store",
        Connection: "keep-alive",
    });
    state.eventClients.add(res);
    writeEvent(res, "snapshot", stateSnapshot(state));
    req.on("close", () => {
        state.eventClients.delete(res);
    });
}

function broadcastSnapshot(state) {
    for (const client of state.eventClients) {
        try {
            writeEvent(client, "snapshot", stateSnapshot(state));
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
        request: { endpoint: activeEndpoint(state), path: "/responses", body: { input, ...(stream ? { stream: true } : {}) } },
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
        state: stateSnapshot(state),
    };
}

async function completeResponseTurn(state, turn, activityId = null) {
    turn.response = await callAgent(activeEndpoint(state), "/responses", { input: turn.input });
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
            const svg = await readFile(join(EXTENSION_ROOT, "assets", "icon-service-AI-Foundry.svg"), "utf8");
            res.writeHead(200, {
                "Content-Type": "image/svg+xml; charset=utf-8",
                "Cache-Control": "no-store",
            });
            res.end(svg);
            return;
        }
        if (req.method === "GET" && url.pathname === "/assets/icon-teams.svg") {
            const svg = await readFile(join(EXTENSION_ROOT, "assets", "icon-teams.svg"), "utf8");
            res.writeHead(200, {
                "Content-Type": "image/svg+xml; charset=utf-8",
                "Cache-Control": "no-store",
            });
            res.end(svg);
            return;
        }
        if (req.method === "GET" && url.pathname === "/assets/icon-a365-agents.svg") {
            const svg = await readFile(join(EXTENSION_ROOT, "assets", "icon-a365-agents.svg"), "utf8");
            res.writeHead(200, {
                "Content-Type": "image/svg+xml; charset=utf-8",
                "Cache-Control": "no-store",
            });
            res.end(svg);
            return;
        }
        if (req.method === "GET" && url.pathname === "/api/state") {
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "GET" && url.pathname === "/api/events") {
            openSnapshotStream(req, res, state);
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/endpoint") {
            const body = await readBody(req);
            if (body.target === "hosted" || state.target === "hosted") {
                state.hosted.responsesEndpoint = String(body.endpoint || "").trim().replace(/\/+$/, "");
                state.hostedByAgent[state.selectedAgentId] = state.hosted;
                state.target = "hosted";
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
            sendJson(res, 200, stateSnapshot(state));
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
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/env/bootstrap") {
            const body = await readBody(req);
            const result = await bootstrapLocalEnv(state, body);
            clearProjectEndpointPrompt(state);
            if (state.foundryConnection.projectEndpoint) {
                await refreshHostedContext(state);
            }
            sendJson(res, 200, { result, state: stateSnapshot(state) });
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/project-endpoint-prompt/clear") {
            clearProjectEndpointPrompt(state);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/target") {
            const body = await readBody(req);
            if (!["local", "hosted"].includes(body.target)) {
                sendJson(res, 400, { error: "Target must be local or hosted." });
                return;
            }
            state.target = body.target;
            if (body.refresh && state.target === "hosted") {
                await refreshHostedContext(state);
            }
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/hosted/refresh") {
            await refreshHostedContext(state);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/config/refresh") {
            state.foundryConnection = emptyFoundryConnection();
            await hydrateFoundryConnection(state);
            sendJson(res, 200, stateSnapshot(state));
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
            state.lastHealth = {
                ...(await checkReadiness(activeEndpoint(state), {
                    expectedAgentNames: state.target === "local" ? readinessAgentNames(selectedAgent(state)) : null,
                })),
                source: "manual",
            };
            if (state.target === "local") {
                reconcileSelectedAgentFromReadiness(state, state.lastHealth, { source: "manual" });
                reconcileLocalReadinessAfterHealth(state);
            }
            updateActivity(state, activity.id, {
                status: state.lastHealth.ok ? "completed" : "failed",
                summary: `Readiness ${state.lastHealth.status} in ${state.lastHealth.durationMs}ms.`,
                details: { health: state.lastHealth },
            });
            sendJson(res, 200, stateSnapshot(state));
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
            state.target = "local";
            state.localEndpoints[state.selectedAgentId] = endpoint;
            state.localRun = {
                ...state.localRun,
                agentId: state.selectedAgentId,
                agentName: selectedAgent(state).serviceName,
                endpoint,
                identityMismatch: null,
            };
            state.lastHealth = null;
            state.messages.length = 0;
            addLocalEvent(state, "ok", `Selected ${selectedAgent(state).displayName || selectedAgent(state).serviceName} from readiness.`);
            sendJson(res, 200, stateSnapshot(state));
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
            writeEvent(res, "snapshot", stateSnapshot(state));
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
                writeEvent(res, "snapshot", stateSnapshot(state));
            });
            turn.response = result;
            updateActivity(state, activity.id, {
                status: result.ok ? "completed" : "failed",
                summary: result.ok ? `Response completed (${result.status}).` : `Response failed (${result.status || "error"}).`,
                details: { displayText: responseDisplayText(result), status: result.status, durationMs: result.durationMs },
            });
            writeEvent(res, "snapshot", stateSnapshot(state));
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
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/deploy/clear") {
            state.deployment.log.length = 0;
            state.deployment.exitCode = null;
            state.deployment.needsProvision = false;
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/teams/tested") {
            state.teams.testedAt = new Date().toISOString();
            state.teams.agentId = state.hosted.agentId || null;
            state.teams.version = state.hosted.version || null;
            sendJson(res, 200, stateSnapshot(state));
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

async function startServer(ctx) {
    const agents = await discoverAgents();
    const selectedAgentId = ctx.input?.agentId && agents.some((agent) => agent.id === ctx.input.agentId)
        ? ctx.input.agentId
        : agents[0].id;
    const selected = agents.find((agent) => agent.id === selectedAgentId) || agents[0];
    const hosted = emptyHostedContext(selected);
    const state = {
        target: "local",
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
        teams: {
            testedAt: null,
            agentId: null,
            version: null,
        },
        messages: [],
        lastHealth: null,
        projectEndpointPrompt: null,
        eventClients: new Set(),
    };
    await hydrateFoundryConnection(state);
    const server = createServer((req, res) => {
        void handleRequest(req, res, state);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    return { server, state, url: `http://127.0.0.1:${port}/` };
}

await joinSession({
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
                        return stateSnapshot(state);
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
                        state.target = ctx.input.target;
                        addActivity(state, {
                            actor: "Copilot",
                            kind: "set_target",
                            status: "completed",
                            summary: `Switched to ${state.target} target.`,
                            details: { target: state.target, endpoint: activeEndpoint(state) },
                        });
                        broadcastSnapshot(state);
                        return stateSnapshot(state);
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
                        state.lastHealth = {
                            ...(await checkReadiness(activeEndpoint(state), {
                                expectedAgentNames: state.target === "local" ? readinessAgentNames(selectedAgent(state)) : null,
                            })),
                            source: "copilot",
                        };
                        if (state.target === "local") {
                            reconcileSelectedAgentFromReadiness(state, state.lastHealth, { source: "copilot" });
                            reconcileLocalReadinessAfterHealth(state);
                        }
                        updateActivity(state, activity.id, {
                            status: state.lastHealth.ok ? "completed" : "failed",
                            summary: `Readiness ${state.lastHealth.status} in ${state.lastHealth.durationMs}ms.`,
                            details: { health: state.lastHealth },
                        });
                        broadcastSnapshot(state);
                        return {
                            readiness: state.lastHealth,
                            transcript: transcriptState(state),
                            state: stateSnapshot(state),
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

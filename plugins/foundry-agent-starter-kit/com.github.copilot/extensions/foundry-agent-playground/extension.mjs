import { spawn } from "node:child_process";
import { access, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createConnection } from "node:net";
import { basename, delimiter, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";
import { renderHtml } from "./renderer.mjs";
import { discoverAgents, serviceEnvPrefix } from "./agent-discovery.mjs";
import {
    discoverHostedContextFromFoundry,
    hostedContextFromAzd,
    normalizeHostedResponsesEndpoint,
} from "./hosted-discovery.mjs";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8088";
const DEFAULT_SERVICE_NAME = "minimal-agent";
const DEFAULT_AGENT_ROOT = join(process.cwd(), "examples", "python", "minimal-agent");
const DEFAULT_MODEL_DEPLOYMENT = "gpt-6-astra";
const DEFAULT_TOOLBOX_NAME = "contract-toolbox";
const EXTENSION_ROOT = dirname(fileURLToPath(import.meta.url));
const ICON_PATH = join(EXTENSION_ROOT, "assets", "castia-mark.png");
const servers = new Map();
const tokenCache = new Map();

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

function isLoopbackEndpoint(endpoint) {
    try {
        const { hostname } = new URL(endpoint);
        return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
    } catch {
        return false;
    }
}

function isFoundryResponsesEndpoint(endpoint) {
    try {
        const { pathname } = new URL(endpoint);
        return /\/endpoint\/protocols\/openai\/responses$/i.test(pathname);
    } catch {
        return false;
    }
}

function responsesUrl(endpoint) {
    if (isFoundryResponsesEndpoint(endpoint)) {
        return normalizeHostedResponsesEndpoint(endpoint);
    }
    return `${endpoint.replace(/\/+$/, "")}/responses`;
}

async function azureAccessToken(resource) {
    const cached = tokenCache.get(resource);
    if (cached && cached.expiresAt > Date.now() + 60000) {
        return cached.token;
    }
    const result = await runCommand("az", [
        "account",
        "get-access-token",
        "--resource",
        resource,
        "--query",
        "accessToken",
        "--output",
        "tsv",
    ]);
    if (result.code !== 0) {
        throw new Error(result.output || "Azure login is required to call the hosted agent.");
    }
    const token = result.output.trim();
    if (!token) {
        throw new Error("Azure CLI did not return an access token for the hosted agent.");
    }
    tokenCache.set(resource, { token, expiresAt: Date.now() + 50 * 60 * 1000 });
    return token;
}

async function requestHeadersForEndpoint(endpoint, accept) {
    const headers = {
        "Content-Type": "application/json",
        ...(accept ? { Accept: accept } : {}),
    };
    if (/^https:\/\//i.test(endpoint) && !isLoopbackEndpoint(endpoint)) {
        headers.Authorization = `Bearer ${await azureAccessToken("https://ai.azure.com")}`;
    }
    return headers;
}

function activeEndpoint(state) {
    return state.target === "hosted" ? state.hosted.responsesEndpoint || "" : selectedLocalEndpoint(state);
}

function selectedAgent(state) {
    return (
        state.agents.find((agent) => agent.id === state.selectedAgentId) ||
        state.agents[0] || {
            id: "minimal-agent",
            serviceName: DEFAULT_SERVICE_NAME,
            displayName: DEFAULT_SERVICE_NAME,
            root: DEFAULT_AGENT_ROOT,
            rootLabel: "examples\\python\\minimal-agent",
            envPrefix: serviceEnvPrefix(DEFAULT_SERVICE_NAME),
        }
    );
}

function selectedLocalEndpoint(state) {
    const agent = selectedAgent(state);
    return state.localEndpoints[agent.id] || DEFAULT_ENDPOINT;
}

function endpointPort(endpoint) {
    try {
        const parsed = new URL(endpoint);
        return Number(parsed.port || (parsed.protocol === "https:" ? 443 : 80));
    } catch {
        return null;
    }
}

function endpointWithPort(endpoint, port) {
    const parsed = new URL(endpoint);
    parsed.hostname = "127.0.0.1";
    parsed.port = String(port);
    return parsed.toString().replace(/\/+$/, "");
}

function addLocalEvent(state, kind, text) {
    state.localRun ||= {};
    state.localRun.events = [
        ...(state.localRun.events || []),
        { kind, text, at: new Date().toISOString() },
    ].slice(-5);
}

function hasFoundryProjectValues(state) {
    return Boolean(state.foundryConnection?.projectEndpoint && state.foundryConnection?.modelDeployment);
}

function emptyHostedContext(agent) {
    return {
        agentName: agent.displayName || agent.serviceName,
        agentId: null,
        version: null,
        responsesEndpoint: null,
        activityEndpoint: null,
        invocationsEndpoint: null,
        projectEndpoint: null,
        modelDeployment: null,
        status: "not_deployed",
        deployedAt: null,
        lastRefresh: null,
        lastRefreshExitCode: null,
        lastRefreshSource: null,
        lastRemoteDiscoveryStatus: null,
        lastRemoteDiscoveryMessage: null,
    };
}

function emptyFoundryConnection() {
    return {
        projectEndpoint: null,
        modelDeployment: null,
        subscriptionId: null,
        location: null,
        projectId: null,
        accountName: null,
        projectName: null,
        connectedAt: null,
        lastConnectExitCode: null,
        lastDiscoveryMessage: null,
    };
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
        log?.push(`$ azd env set ${key} ${key.includes("ENDPOINT") ? value : String(value)}\n`);
        if (result.code !== 0) {
            log?.push(result.output || `Failed to set ${key}.\n`);
        }
    }
    return exitCode;
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

function setSelectedAgent(state, agentId) {
    const agent = state.agents.find((candidate) => candidate.id === agentId) || state.agents[0];
    state.selectedAgentId = agent.id;
    state.hostedByAgent[agent.id] ||= emptyHostedContext(agent);
    state.localEndpoints[agent.id] ||= DEFAULT_ENDPOINT;
    state.hosted = state.hostedByAgent[agent.id];
    return agent;
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
    const initialEndpoint = selectedLocalEndpoint(state);
    if (await isEndpointPortOpen(initialEndpoint)) {
        addLocalEvent(state, "warn", `${initialEndpoint} is already in use.`);
        const correctedEndpoint = await nextAvailableLocalEndpoint(initialEndpoint);
        state.localEndpoints[agent.id] = correctedEndpoint;
        addLocalEvent(state, "ok", `Using ${correctedEndpoint} instead.`);
    }
    const endpoint = selectedLocalEndpoint(state);
    const port = endpointPort(endpoint);
    state.localRun = {
        running: true,
        command: `${local.command} ${local.args.join(" ")}`,
        startedAt: new Date().toISOString(),
        completedAt: null,
        exitCode: null,
        log: [`$ ${local.command} ${local.args.join(" ")}\n`],
        events: state.localRun?.events || [],
        process: null,
    };
    addLocalEvent(state, "", `Starting local agent on ${endpoint}.`);
    const child = spawn(local.command, local.args, {
        cwd: agent.root,
        shell: false,
        env: {
            ...process.env,
            ...(local.env || {}),
            ...(port ? { PORT: String(port) } : {}),
            FOUNDRY_PROJECT_ENDPOINT: state.foundryConnection.projectEndpoint || process.env.FOUNDRY_PROJECT_ENDPOINT || "",
            AZURE_AI_MODEL_DEPLOYMENT_NAME: state.foundryConnection.modelDeployment || process.env.AZURE_AI_MODEL_DEPLOYMENT_NAME || "",
        },
    });
    state.localRun.process = child;
    const append = (chunk) => state.localRun.log.push(chunk.toString());
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (error) => {
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = 1;
        state.localRun.log.push(`${error.name}: ${error.message}\n`);
        addLocalEvent(state, "fail", error.message);
    });
    child.on("close", (code) => {
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = code ?? 0;
        addLocalEvent(state, code === 0 ? "" : "fail", code === 0 ? "Local agent stopped." : `Local agent exited with code ${code ?? 0}.`);
        delete state.localRun.process;
    });
    waitForLocalReadiness(state).then((health) => {
        state.lastHealth = health;
        addLocalEvent(state, health.ok ? "ok" : "fail", health.ok ? `Local agent ready at ${endpoint}.` : `Local readiness failed: ${health.body || health.status}`);
    }).catch((error) => {
        addLocalEvent(state, "fail", error instanceof Error ? error.message : String(error));
    });
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

async function waitForLocalReadiness(state) {
    const deadline = Date.now() + 15000;
    let latest = null;
    while (Date.now() < deadline) {
        if (!state.localRun?.running) {
            return {
                ok: false,
                status: state.localRun?.exitCode ?? 0,
                durationMs: 0,
                body: "Local agent exited before it became ready.",
            };
        }
        latest = await checkReadiness(selectedLocalEndpoint(state), { timeoutMs: 1000 });
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

function stopLocalAgent(state) {
    const child = state.localRun?.process;
    if (child && !child.killed) {
        child.kill();
    }
    state.localRun = {
        ...state.localRun,
        running: false,
        completedAt: new Date().toISOString(),
        exitCode: state.localRun?.exitCode ?? null,
        log: [...(state.localRun?.log || []), "$ stopped local agent\n"],
        events: [...(state.localRun?.events || []), { kind: "", text: "Local agent stopped.", at: new Date().toISOString() }].slice(-5),
        process: null,
    };
    state.lastHealth = null;
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
    const env = await readAgentEnv(agent);
    state.localEnv = {
        path: env.envPath,
        exists: env.exists,
        loadedAt: new Date().toISOString(),
    };
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

async function ensureAzdDeploymentContext(state, log) {
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
    if (commandName === "deploy" || commandName === "provision") {
        await ensureAzdDeploymentContext(state, state.deployment.log);
    }
    writeEvent(res, "snapshot", stateSnapshot(state));
    const result = await runCommand("azd", args, {
        cwd: agent.root,
        onOutput: (text) => {
            state.deployment.log.push(text);
            writeEvent(res, "snapshot", stateSnapshot(state));
        },
    });
    const output = state.deployment.log.join("");
    state.deployment.running = false;
    state.deployment.exitCode = result.code;
    state.deployment.completedAt = new Date().toISOString();
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
    }
    if (commandName === "provision" && result.code === 0) {
        state.deployment.log.push("\nDeploy prep complete. Deploy is ready.\n");
    }
    writeEvent(res, "snapshot", stateSnapshot(state));
    res.end();
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

function messagesForTarget(state) {
    return state.messages.filter((message) =>
        state.target === "hosted" ? message.target === "hosted" : message.target !== "hosted",
    );
}

async function callAgentStream(endpoint, payload, onDelta) {
    const started = Date.now();
    try {
        const response = await fetch(responsesUrl(endpoint), {
            method: "POST",
            headers: await requestHeadersForEndpoint(endpoint, "text/event-stream"),
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
            if (data.trim() === "[DONE]") return;
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
        const url = path === "/responses" ? responsesUrl(endpoint) : `${endpoint}${path}`;
        const response = await fetch(url, {
            method: "POST",
            headers: await requestHeadersForEndpoint(endpoint),
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

async function checkReadiness(endpoint, { timeoutMs = 10000 } = {}) {
    const started = Date.now();
    if (isFoundryResponsesEndpoint(endpoint)) {
        return {
            ok: true,
            status: "hosted",
            durationMs: 0,
            body: "Hosted Responses endpoint discovered. Send a prompt to test it.",
        };
    }
    try {
        const response = await fetch(`${endpoint}/readiness`, {
            signal: AbortSignal.timeout(timeoutMs),
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
    const agent = selectedAgent(state);
    const visibleMessages = messagesForTarget(state);
    return {
        endpoint: activeEndpoint(state),
        localEndpoint: selectedLocalEndpoint(state),
        target: state.target,
        agents: state.agents,
        selectedAgentId: state.selectedAgentId,
        selectedAgent: agent,
        foundryConnection: state.foundryConnection,
        envBootstrap: state.envBootstrap,
        hosted: state.hosted,
        deployment: state.deployment,
        localEnv: state.localEnv,
        localRun: {
            ...state.localRun,
            process: undefined,
        },
        teams: state.teams,
        messages: state.messages,
        visibleMessages,
        lastHealth: state.lastHealth,
        stats: transcriptStats(visibleMessages),
    };
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
        if (req.method === "POST" && url.pathname === "/api/endpoint") {
            const body = await readBody(req);
            if (body.target === "hosted" || state.target === "hosted") {
                state.hosted.responsesEndpoint = String(body.endpoint || "").trim().replace(/\/+$/, "");
                state.hostedByAgent[state.selectedAgentId] = state.hosted;
                state.target = "hosted";
            } else {
                state.localEndpoints[state.selectedAgentId] = normalizeEndpoint(body.endpoint);
                state.target = "local";
            }
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/agent") {
            const body = await readBody(req);
            setSelectedAgent(state, body.agentId);
            state.lastHealth = null;
            await refreshHostedContext(state);
            sendJson(res, 200, stateSnapshot(state));
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
            if (state.foundryConnection.projectEndpoint) {
                await refreshHostedContext(state);
            }
            sendJson(res, 200, { result, state: stateSnapshot(state) });
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
            state.lastHealth = await checkReadiness(activeEndpoint(state));
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/local/start") {
            if (!hasFoundryProjectValues(state)) {
                sendJson(res, 409, {
                    error: "Foundry project endpoint is required before starting local.",
                    needsProjectEndpoint: true,
                    state: stateSnapshot(state),
                });
                return;
            }
            await startLocalAgent(state);
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/local/stop") {
            stopLocalAgent(state);
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
            const turn = {
                input,
                createdAt: new Date().toISOString(),
                target: state.target,
                request: { endpoint: activeEndpoint(state), path: "/responses", body: { input } },
                response: await callAgent(activeEndpoint(state), "/responses", { input }),
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
            if (state.target !== "hosted" && !state.lastHealth?.ok) {
                sendJson(res, 409, { error: "Start the local agent and wait for readiness before sending a prompt." });
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
                target: state.target,
                request: { endpoint: activeEndpoint(state), path: "/responses", body: { input, stream: true } },
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
            writeEvent(res, "snapshot", stateSnapshot(state));
            res.end();
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/clear") {
            const keep = state.messages.filter((message) =>
                state.target === "hosted" ? message.target !== "hosted" : message.target === "hosted",
            );
            state.messages.length = 0;
            state.messages.push(...keep);
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
        localRun: {
            running: false,
            command: null,
            startedAt: null,
            completedAt: null,
            exitCode: null,
            log: [],
            events: [],
        },
        teams: {
            testedAt: null,
            agentId: null,
            version: null,
        },
        messages: [],
        lastHealth: null,
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
            "After the user provides the endpoint, the canvas writes non-secret Foundry values into gitignored .env files with the same bootstrap logic exposed through the bootstrap_env action.",
            "Do not tell the user to paste the project endpoint into the canvas; the canvas intentionally keeps project bootstrap out of the visible UI.",
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
                            state.localEndpoints[state.selectedAgentId] = normalizeEndpoint(ctx.input?.endpoint);
                        }
                        return stateSnapshot(state);
                    },
                },
                {
                    name: "health_check",
                    description: "Call GET /readiness on the configured agent endpoint.",
                    handler: async (ctx) => {
                        const state = instanceState(ctx);
                        state.lastHealth = await checkReadiness(activeEndpoint(state));
                        return state.lastHealth;
                    },
                },
                {
                    name: "bootstrap_env",
                    description: "Create or update gitignored local .env files with non-secret Foundry project values derived from a project URL.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            projectEndpoint: {
                                type: "string",
                                description: "Foundry project URL ending in /api/projects/<project>.",
                            },
                            modelDeployment: {
                                type: "string",
                                description: "Azure AI model deployment name. Defaults to gpt-6-astra.",
                            },
                            toolboxName: {
                                type: "string",
                                description: "Optional toolbox name. When provided, writes TOOLBOX_NAME and its MCP endpoint. Defaults to contract-toolbox.",
                            },
                            overwrite: {
                                type: "boolean",
                                description: "Overwrite existing values in .env files. Defaults to false, preserving existing values.",
                            },
                            dryRun: {
                                type: "boolean",
                                description: "Preview discovered targets and derived values without writing files.",
                            },
                            targetPaths: {
                                type: "array",
                                description: "Optional workspace-relative .env files to write. Each must be gitignored.",
                                items: { type: "string" },
                            },
                        },
                        required: ["projectEndpoint"],
                        additionalProperties: false,
                    },
                    handler: async (ctx) => {
                        const state = instanceState(ctx);
                        const result = await bootstrapLocalEnv(state, ctx.input || {});
                        return { result, state: stateSnapshot(state) };
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
                            target: state.target,
                            request: { endpoint: activeEndpoint(state), path: "/responses", body: { input } },
                            response: await callAgent(activeEndpoint(state), "/responses", { input }),
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
                        entry.state.localRun.process.kill();
                    }
                    servers.delete(ctx.instanceId);
                    await new Promise((resolve) => entry.server.close(() => resolve()));
                }
            },
        }),
    ],
});

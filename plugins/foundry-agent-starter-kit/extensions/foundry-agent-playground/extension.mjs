import { spawn } from "node:child_process";
import { access, readdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { delimiter, dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { CanvasError, createCanvas, joinSession } from "@github/copilot-sdk/extension";

const DEFAULT_ENDPOINT = "http://127.0.0.1:8088";
const DEFAULT_SERVICE_NAME = "minimal-agent";
const DEFAULT_AGENT_ROOT = join(process.cwd(), "examples", "python", "minimal-agent");
const EXTENSION_ROOT = dirname(fileURLToPath(import.meta.url));
const ICON_PATH = join(EXTENSION_ROOT, "assets", "castia-mark.png");
const servers = new Map();
const tokenCache = new Map();

async function exists(path) {
    try {
        await access(path);
        return true;
    } catch {
        return false;
    }
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
    if (isFoundryResponsesEndpoint(endpoint)) return endpoint;
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
        lastRefresh: null,
        lastRefreshExitCode: null,
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

function serviceEnvPrefix(serviceName) {
    return `AGENT_${String(serviceName || "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase()}`;
}

function normalizeRootLabel(root) {
    const label = relative(process.cwd(), root) || ".";
    return label.split(/[\\/]+/).join("\\");
}

function parseHostedServices(yaml, filePath) {
    const root = dirname(filePath);
    const lines = yaml.split(/\r?\n/);
    const servicesLine = lines.findIndex((line) => /^services:\s*$/.test(line));
    if (servicesLine === -1) return [];
    const services = [];
    let current = null;
    for (const line of lines.slice(servicesLine + 1)) {
        const serviceMatch = line.match(/^ {2}([A-Za-z0-9_-]+):\s*$/);
        if (serviceMatch) {
            if (current?.isHosted) services.push(current);
            current = {
                serviceName: serviceMatch[1],
                displayName: serviceMatch[1],
                project: ".",
                isHosted: false,
            };
            continue;
        }
        if (!current) continue;
        if (/^\S/.test(line)) break;
        const propertyMatch = line.match(/^ {4}([A-Za-z0-9_]+):\s*(.*)$/);
        if (!propertyMatch) continue;
        const [, key, rawValue] = propertyMatch;
        const value = rawValue.trim().replace(/^['"]|['"]$/g, "");
        if (key === "host" && value === "azure.ai.agent") current.isHosted = true;
        if (key === "kind" && value === "hosted") current.isHosted = true;
        if (key === "project") current.project = value || ".";
        if (key === "name" && value) current.displayName = value;
    }
    if (current?.isHosted) services.push(current);
    return services.map((service) => {
        const agentRoot = join(root, service.project || ".");
        const rootLabel = normalizeRootLabel(agentRoot);
        return {
            id: `${rootLabel}:${service.serviceName}`,
            serviceName: service.serviceName,
            displayName: service.displayName,
            root: agentRoot,
            rootLabel,
            envPrefix: serviceEnvPrefix(service.serviceName),
        };
    });
}

async function findAzureYamlFiles(dir, depth = 0) {
    if (depth > 4) return [];
    const entries = await readdir(dir, { withFileTypes: true }).catch(() => []);
    const files = [];
    for (const entry of entries) {
        if (entry.name === "node_modules" || entry.name === ".git" || entry.name === ".venv") continue;
        const path = join(dir, entry.name);
        if (entry.isFile() && entry.name === "azure.yaml") {
            files.push(path);
        } else if (entry.isDirectory()) {
            files.push(...(await findAzureYamlFiles(path, depth + 1)));
        }
    }
    return files;
}

async function discoverAgents() {
    const files = await findAzureYamlFiles(process.cwd());
    const agents = [];
    for (const file of files) {
        const yaml = await readFile(file, "utf8").catch(() => "");
        agents.push(...parseHostedServices(yaml, file));
    }
    if (agents.length) {
        return agents.sort((a, b) => a.rootLabel.localeCompare(b.rootLabel) || a.serviceName.localeCompare(b.serviceName));
    }
    const rootLabel = normalizeRootLabel(DEFAULT_AGENT_ROOT);
    return [
        {
            id: `${rootLabel}:${DEFAULT_SERVICE_NAME}`,
            serviceName: DEFAULT_SERVICE_NAME,
            displayName: DEFAULT_SERVICE_NAME,
            root: DEFAULT_AGENT_ROOT,
            rootLabel,
            envPrefix: serviceEnvPrefix(DEFAULT_SERVICE_NAME),
        },
    ];
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
    const agent = selectedAgent(state);
    const local = await localStartCommand(agent);
    if (!local) {
        throw new CanvasError("local_start_unsupported", "No local start command was discovered for this agent.");
    }
    state.localRun = {
        running: true,
        command: `${local.command} ${local.args.join(" ")}`,
        startedAt: new Date().toISOString(),
        completedAt: null,
        exitCode: null,
        log: [`$ ${local.command} ${local.args.join(" ")}\n`],
        process: null,
    };
    const child = spawn(local.command, local.args, {
        cwd: agent.root,
        shell: false,
        env: {
            ...process.env,
            ...(local.env || {}),
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
    });
    child.on("close", (code) => {
        state.localRun.running = false;
        state.localRun.completedAt = new Date().toISOString();
        state.localRun.exitCode = code ?? 0;
        delete state.localRun.process;
    });
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
        process: null,
    };
}

async function refreshHostedContext(state) {
    const agent = selectedAgent(state);
    const result = await runCommand("azd", ["env", "get-values"], { cwd: agent.root });
    const values = parseAzdEnv(result.output);
    const prefix = agent.envPrefix;
    state.hostedByAgent[agent.id] = {
        ...state.hostedByAgent[agent.id],
        agentName: values[`${prefix}_NAME`] || state.hostedByAgent[agent.id].agentName,
        agentId: values[`${prefix}_ID`] || state.hostedByAgent[agent.id].agentId,
        version: values[`${prefix}_VERSION`] || state.hostedByAgent[agent.id].version,
        responsesEndpoint:
            values[`${prefix}_RESPONSES_ENDPOINT`] || state.hostedByAgent[agent.id].responsesEndpoint,
        activityEndpoint:
            values[`${prefix}_ACTIVITY_ENDPOINT`] || state.hostedByAgent[agent.id].activityEndpoint,
        invocationsEndpoint:
            values[`${prefix}_INVOCATIONS_ENDPOINT`] || state.hostedByAgent[agent.id].invocationsEndpoint,
        projectEndpoint:
            values.AZURE_AI_PROJECT_ENDPOINT ||
            values.AZURE_AIPROJECT_ENDPOINT ||
            values.FOUNDRY_PROJECT_ENDPOINT ||
            state.foundryConnection.projectEndpoint ||
            state.hostedByAgent[agent.id].projectEndpoint,
        modelDeployment:
            values.AZURE_AI_MODEL_DEPLOYMENT_NAME ||
            values.AZURE_OPENAI_DEPLOYMENT_NAME ||
            state.foundryConnection.modelDeployment ||
            state.hostedByAgent[agent.id].modelDeployment,
        lastRefresh: new Date().toISOString(),
        lastRefreshExitCode: result.code,
    };
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
    await hydrateFoundryConnectionFromDotEnv(state);
}

async function connectFoundry(state, { projectEndpoint, modelDeployment }) {
    const agent = selectedAgent(state);
    const endpoint = String(projectEndpoint || "").trim().replace(/\/+$/, "");
    const deployment = String(modelDeployment || "").trim();
    if (!/^https:\/\/[^/\s]+\/api\/projects\/[^/\s]+$/i.test(endpoint)) {
        throw new CanvasError("invalid_foundry_endpoint", "Enter a Foundry project endpoint ending in /api/projects/<project>.");
    }
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

async function checkReadiness(endpoint) {
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
    const agent = selectedAgent(state);
    return {
        endpoint: activeEndpoint(state),
        localEndpoint: selectedLocalEndpoint(state),
        target: state.target,
        agents: state.agents,
        selectedAgentId: state.selectedAgentId,
        selectedAgent: agent,
        foundryConnection: state.foundryConnection,
        hosted: state.hosted,
        deployment: state.deployment,
        localEnv: state.localEnv,
        localRun: {
            ...state.localRun,
            process: undefined,
        },
        teams: state.teams,
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
  <title>Foundry Agent Playground</title>
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
      display: grid;
      gap: 10px;
      padding: 10px 12px;
      background: var(--cp-bg-elevated);
    }
    .journey {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .step {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      min-width: 0;
      min-height: 48px;
      padding: 8px;
      border: 1px solid transparent;
      border-radius: 14px;
      background: var(--cp-surface-soft);
      color: var(--cp-text);
      text-align: left;
      box-shadow: none;
    }
    .step:hover {
      border-color: var(--cp-border);
    }
    .step.active {
      border-color: var(--cp-border);
      background: var(--cp-surface);
      box-shadow: var(--cp-shadow);
    }
    .step.done {
      border-color: color-mix(in srgb, var(--cp-success) 24%, transparent);
    }
    .step-index {
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--cp-panel-strong);
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 800;
    }
    .step.active .step-index {
      background: var(--cp-accent);
      color: var(--cp-accent-fg);
    }
    .step.done .step-index {
      background: var(--cp-success);
      color: var(--cp-accent-fg);
    }
    .step-title {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .step-subtitle {
      display: block;
      margin-top: 1px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
    }
    .step-state {
      display: none;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 700;
    }
    @media (max-width: 900px) {
      .journey {
        gap: 6px;
      }
      .step {
        grid-template-columns: auto minmax(0, 1fr);
        gap: 8px;
        padding: 8px;
      }
      .step-index {
        width: 24px;
        height: 24px;
      }
    }
    @media (max-width: 520px) {
      .journey {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .step {
        grid-template-columns: auto minmax(0, 1fr);
        min-height: 44px;
        padding: 8px 6px;
        border-radius: 14px;
      }
      .step-index {
        width: 22px;
        height: 22px;
      }
      .step-title {
        font-size: 13px;
      }
      .step-subtitle {
        display: none;
      }
    }
    .action-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 12px;
      border-radius: 16px;
      background: var(--cp-panel-strong);
      box-shadow: var(--cp-shadow);
    }
    .action-title {
      font-weight: 800;
    }
    .action-copy {
      color: var(--cp-text-muted);
      font-size: 13px;
      margin-top: 2px;
    }
    .action-buttons {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .hero-picker {
      width: min(240px, 34vw);
    }
    .secondary {
      color: var(--cp-text-muted);
    }
    .advanced-row {
      display: grid;
      grid-template-columns: minmax(220px, 1.2fr) minmax(180px, 1fr) minmax(110px, 0.5fr) auto auto;
      gap: 8px;
      align-items: center;
    }
    .advanced-row[hidden] { display: none; }
    .project-hint {
      grid-column: 1 / -1;
      color: var(--cp-text-muted);
      font-size: 12px;
    }
    .agent-picker {
      position: relative;
      min-width: 0;
    }
    .agent-picker-button {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 10px;
      width: 100%;
      border-color: var(--cp-border);
      background: var(--cp-surface);
      color: var(--cp-text);
      font-weight: 500;
      text-align: left;
    }
    .agent-picker-button:hover {
      border-color: var(--cp-border-strong);
      background: var(--cp-panel-strong);
    }
    .agent-picker-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .agent-picker-chevron {
      width: 8px;
      height: 8px;
      border-right: 1.5px solid var(--cp-text-muted);
      border-bottom: 1.5px solid var(--cp-text-muted);
      transform: translateY(-2px) rotate(45deg);
    }
    .agent-menu {
      position: absolute;
      z-index: 20;
      top: calc(100% + 6px);
      left: 0;
      right: 0;
      max-height: 260px;
      overflow: auto;
      padding: 4px;
      border: 1px solid var(--cp-border);
      border-radius: 0.75rem;
      background: var(--cp-panel-strong);
      color: var(--cp-text);
      box-shadow: var(--cp-shadow);
    }
    .agent-menu[hidden] { display: none; }
    .agent-option {
      display: grid;
      gap: 2px;
      width: 100%;
      padding: 8px 10px;
      border: 0;
      border-radius: 0.5rem;
      background: transparent;
      color: var(--cp-text);
      text-align: left;
      font-weight: 500;
    }
    .agent-option:hover,
    .agent-option.active {
      background: var(--cp-accent-soft);
      color: var(--cp-text);
    }
    .agent-option-folder {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 500;
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
    .content.deploy-mode {
      grid-template-rows: minmax(0, 1fr);
    }
    .view { min-height: 0; }
    .view[hidden] { display: none; }
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
      white-space: normal;
    }
    .bubble-body .md {
      display: grid;
      gap: 6px;
    }
    .bubble-body p,
    .bubble-body h1,
    .bubble-body h2,
    .bubble-body h3,
    .bubble-body ul,
    .bubble-body ol,
    .bubble-body pre,
    .bubble-body .table-scroll {
      margin: 0;
    }
    .bubble-body h1,
    .bubble-body h2,
    .bubble-body h3 {
      color: var(--cp-text);
      font-weight: 700;
      line-height: 1.2;
    }
    .bubble-body h1 {
      font-size: 16px;
    }
    .bubble-body h2 {
      font-size: 14px;
    }
    .bubble-body h3 {
      font-size: 13px;
    }
    .bubble-body ul,
    .bubble-body ol {
      padding-left: 18px;
    }
    .bubble-body li {
      margin: 2px 0;
    }
    .bubble-body code {
      padding: 1px 4px;
      border-radius: 6px;
      background: color-mix(in srgb, var(--cp-text) 8%, transparent);
      font-size: 12px;
    }
    .bubble-body pre {
      overflow: auto;
      padding: 8px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--cp-text) 8%, transparent);
    }
    .bubble-body pre code {
      padding: 0;
      background: transparent;
    }
    .bubble-body pre.json-pre {
      border: 1px solid var(--cp-border);
      background: var(--cp-surface);
    }
    .json-key {
      color: var(--cp-accent);
      font-weight: 600;
    }
    .json-string {
      color: var(--cp-success);
    }
    .json-number {
      color: var(--cp-warning);
    }
    .json-literal {
      color: var(--cp-danger);
      font-weight: 600;
    }
    .bubble-body .table-scroll {
      overflow-x: auto;
      border: 1px solid var(--cp-border);
      border-radius: 10px;
      background: var(--cp-surface);
    }
    .bubble-body table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .bubble-body th,
    .bubble-body td {
      padding: 6px 8px;
      border-bottom: 1px solid var(--cp-border);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    .bubble-body th {
      color: var(--cp-text);
      font-weight: 700;
      background: var(--cp-surface-soft);
    }
    .bubble-body tr:last-child td {
      border-bottom: 0;
    }
    .bubble-body a {
      color: var(--cp-accent);
      text-decoration: none;
    }
    .bubble-body a:hover {
      text-decoration: underline;
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
    .deploy-view,
    .teams-view {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
      min-height: 0;
      padding: 4px 2px 16px;
    }
    .teams-view {
      grid-template-rows: auto auto;
      align-content: start;
      overflow: auto;
    }
    .deploy-summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .deploy-card {
      padding: 12px;
      border-radius: 16px;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .deploy-label {
      color: var(--cp-text-muted);
      font-size: 12px;
      font-weight: 600;
    }
    .deploy-value {
      margin-top: 4px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .terminal {
      min-height: 0;
      overflow: auto;
      margin: 0;
      max-height: none;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .guide-card {
      display: grid;
      gap: 12px;
      padding: 16px;
      border-radius: 16px;
      background: var(--cp-surface-soft);
      box-shadow: var(--cp-shadow);
    }
    .guide-title {
      font-size: 16px;
      font-weight: 700;
    }
    .guide-copy {
      color: var(--cp-text-muted);
    }
    .guide-steps {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .guide-steps li {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 10px;
      align-items: start;
      padding: 10px;
      border-radius: 12px;
      background: var(--cp-surface);
    }
    .guide-number {
      display: inline-grid;
      place-items: center;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      background: var(--cp-accent-soft);
      color: var(--cp-accent);
      font-size: 12px;
      font-weight: 700;
    }
    .teams-gates {
      display: grid;
      gap: 10px;
      position: relative;
      margin-top: 4px;
    }
    .teams-gates::before {
      content: "";
      position: absolute;
      top: 44px;
      bottom: 44px;
      left: 23px;
      width: 2px;
      border-radius: 999px;
      background: linear-gradient(180deg, #7c3aed, #242424, #6264a7);
      opacity: 0.28;
    }
    .teams-gate {
      position: relative;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 14px;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-surface);
      box-shadow: var(--cp-shadow);
    }
    .gate-icon {
      display: inline-grid;
      place-items: center;
      width: 46px;
      height: 46px;
      border: 1px solid var(--cp-border);
      border-radius: 16px;
      background: var(--cp-panel-strong);
      color: var(--cp-accent);
      font-size: 18px;
      font-weight: 800;
      box-shadow: var(--cp-shadow);
    }
    .gate-logo {
      width: 28px;
      height: 28px;
      object-fit: contain;
    }
    .gate-title {
      font-weight: 800;
    }
    .gate-meta {
      margin-top: 2px;
      color: var(--cp-text-muted);
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
      .advanced-row,
      .action-card,
      .deploy-summary,
      .content {
        grid-template-columns: 1fr;
      }
      .action-buttons {
        flex-wrap: wrap;
        justify-content: flex-start;
      }
      .hero-picker {
        width: 100%;
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
        <button id="teamsStep" class="step" type="button">
          <span class="step-index">3</span>
          <span><span class="step-title">Teams</span><span id="teamsStepText" class="step-subtitle">Hire it</span></span>
          <span id="teamsStepState" class="step-state">Later</span>
        </button>
      </div>
      <div class="action-card">
        <div>
          <div id="guideTitle" class="action-title">Make it work locally</div>
          <div id="guideCopy" class="action-copy">Check the local agent, then send a prompt.</div>
        </div>
        <div class="action-buttons">
          <div class="agent-picker hero-picker">
            <button id="agentPickerButton" class="agent-picker-button" type="button" aria-haspopup="listbox" aria-expanded="false">
              <span id="agentPickerLabel" class="agent-picker-label">Agent</span>
              <span class="agent-picker-chevron" aria-hidden="true"></span>
            </button>
            <div id="agentMenu" class="agent-menu" role="listbox" hidden></div>
          </div>
          <button id="primaryGuideAction" class="primary" type="button">Choose project</button>
          <button id="stopLocalAction" class="secondary danger" type="button" hidden>Stop local</button>
          <button id="testHostedAction" class="secondary" type="button" hidden>Test hosted</button>
          <button id="advancedToggle" class="secondary" type="button" hidden>Refresh .env</button>
        </div>
      </div>
      <div id="advancedRow" class="advanced-row" hidden>
        <input id="endpoint" aria-label="Agent endpoint" spellcheck="false" placeholder="http://127.0.0.1:8088" />
        <input id="foundryEndpoint" aria-label="Foundry project endpoint" spellcheck="false" placeholder="https://.../api/projects/..." />
        <input id="modelDeployment" aria-label="Model deployment" spellcheck="false" placeholder="gpt-5.5" />
        <button id="connectFoundry" type="button" hidden>Use project</button>
        <button id="checkHealth" type="button">Check readiness</button>
        <button id="startLocal" type="button">Start local</button>
        <div class="project-hint">Missing config? Copy .env.example to .env in the agent folder, fill FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME, then refresh.</div>
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
        <div id="transcript" class="transcript">
          <div class="empty">Send a prompt to test <code>POST /responses</code>.</div>
        </div>
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
        <div class="deploy-summary">
          <div class="deploy-card">
            <div class="deploy-label">Teams status</div>
            <div id="teamsStatus" class="deploy-value">Not tested</div>
          </div>
          <div class="deploy-card">
            <div class="deploy-label">Hosted agent</div>
            <div id="teamsAgent" class="deploy-value">Not resolved</div>
          </div>
          <div class="deploy-card">
            <div class="deploy-label">Foundry version</div>
            <div id="teamsVersion" class="deploy-value">Not deployed</div>
          </div>
        </div>
        <div class="guide-card">
          <div>
            <div class="guide-title">Publish and hire the hosted agent</div>
            <div class="guide-copy">After Foundry hosted chat works, finish the user-controlled Microsoft 365 handoff in three steps.</div>
          </div>
          <div class="teams-gates">
            <div class="teams-gate">
              <span class="gate-icon" aria-hidden="true"><img class="gate-logo" src="/assets/icon-service-AI-Foundry.svg" alt="" /></span>
              <div><div class="gate-title">Publish in Foundry</div><div class="gate-meta">Confirm the active hosted version, then use Publish → Teams and Microsoft 365 Copilot. Review name, version, descriptions, developer, and scope.</div></div>
            </div>
            <div class="teams-gate">
              <span class="gate-icon" aria-hidden="true"><img class="gate-logo" src="/assets/icon-a365-agents.svg" alt="" /></span>
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
          <button class="primary" id="teamsTestedButton" type="button" hidden>Mark Teams tested</button>
        </div>
      </div>
    </section>
  </div>
  <script>
    const endpointInput = document.getElementById("endpoint");
    const foundryEndpointInput = document.getElementById("foundryEndpoint");
    const modelDeploymentInput = document.getElementById("modelDeployment");
    const connectFoundryButton = document.getElementById("connectFoundry");
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
    const primaryGuideAction = document.getElementById("primaryGuideAction");
    const testHostedAction = document.getElementById("testHostedAction");
    const advancedToggle = document.getElementById("advancedToggle");
    const advancedRow = document.getElementById("advancedRow");
    const chatView = document.getElementById("chatView");
    const deployView = document.getElementById("deployView");
    const teamsView = document.getElementById("teamsView");
    const foundryTarget = document.getElementById("foundryTarget");
    const hostedAgent = document.getElementById("hostedAgent");
    const hostedVersion = document.getElementById("hostedVersion");
    const deployLog = document.getElementById("deployLog");
    const teamsStatus = document.getElementById("teamsStatus");
    const teamsAgent = document.getElementById("teamsAgent");
    const teamsVersion = document.getElementById("teamsVersion");
    const checkHealthButton = document.getElementById("checkHealth");
    const startLocalButton = document.getElementById("startLocal");
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
    let inFlight = false;
    let lastSend = { input: "", at: 0 };
    let activeView = "chat";
    let latestState = null;
    let agentMenuOpen = false;
    let settingsOpen = false;

    function setStatus(kind, text) {
      statusDot.className = "dot " + (kind || "");
      statusText.textContent = text;
    }

    function renderSnapshot(state) {
      latestState = state;
      renderAgentPicker(state);
      endpointInput.value = state.target === "hosted" ? (state.hosted.responsesEndpoint || "") : state.localEndpoint;
      endpointInput.placeholder = state.target === "hosted" ? "Foundry Responses endpoint not discovered yet" : "http://127.0.0.1:8088";
      foundryEndpointInput.value = state.foundryConnection?.projectEndpoint || state.hosted?.projectEndpoint || "";
      modelDeploymentInput.value = state.foundryConnection?.modelDeployment || state.hosted?.modelDeployment || "";
      turnCount.textContent = state.stats.total + " turns";
      passCount.textContent = state.stats.completed + " pass";
      failCount.textContent = state.stats.failed + " fail";
      avgLatency.textContent = state.stats.averageMs + "ms avg";
      renderMessages(state.messages || []);
      if (state.lastHealth) {
        setStatus(state.lastHealth.ok ? "ok" : "fail", "Readiness " + state.lastHealth.status + " in " + state.lastHealth.durationMs + "ms");
      }
      renderDeploy(state);
      renderTeams(state);
      renderJourney(state);
      renderView();
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
      localStep.classList.toggle("done", localOk);
      foundryStep.classList.toggle("done", foundryOk);
      teamsStep.classList.toggle("done", teamsOk);
      localStepText.textContent = !connected ? "Project" : localRunning && !localOk ? "Starting" : localOk ? "Answered" : "Run it";
      foundryStepText.textContent = foundryOk ? "Hosted" : "Deploy it";
      teamsStepText.textContent = teamsOk ? "Tested" : "Hire it";
      localStepState.textContent = !connected ? "First" : localOk ? "Done" : "Start";
      foundryStepState.textContent = versionLabel || (foundryOk ? "Ready" : localOk ? "Next" : "Later");
      teamsStepState.textContent = teamsOk ? "Done" : foundryOk ? "Next" : "Later";
      if (activeView === "deploy") {
        const needsProvision = Boolean(state.deployment?.needsProvision);
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = !(connected && foundryOk);
        guideTitle.textContent = !connected ? "Fill .env first" : "Make it work in Foundry";
        guideCopy.textContent = !connected
          ? "Copy .env.example to .env, add FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME, then refresh."
          : needsProvision
          ? "Prepare this repo for hosted deployment into the connected Foundry project."
          : foundryOk
          ? "Current version: " + (state.hosted.version || "ready") + ". Deploy changes when local updates are ready."
          : "Deploy the selected agent, then use the same transcript against the hosted target.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : needsProvision ? "Prepare deploy" : "Deploy";
        setActiveStep("foundry");
      } else if (activeView === "teams") {
        primaryGuideAction.hidden = false;
        testHostedAction.hidden = true;
        guideTitle.textContent = "Publish and hire it in Teams";
        guideCopy.textContent = "Confirm the active Foundry version, publish it to Microsoft 365, approve it if needed, hire it in Teams, then run the same smoke prompt.";
        primaryGuideAction.textContent = teamsOk ? "Teams tested" : "Mark Teams tested";
        setActiveStep("teams");
      } else {
        primaryGuideAction.hidden = connected && state.target !== "hosted" && localOk;
        testHostedAction.hidden = true;
        guideTitle.textContent = !connected ? "Fill .env first" : state.target === "hosted" ? "Test it in Foundry" : "Make it work locally";
        guideCopy.textContent = !connected
          ? "Copy .env.example to .env in the selected agent folder, add the Foundry endpoint and model deployment, then refresh."
          : state.target === "hosted"
          ? "Current version: " + (state.hosted?.version || "ready") + ". Send a prompt here, or switch to deploy."
          : localRunning
          ? "Local agent is starting. Check readiness, then send a prompt."
          : "Start the local agent, check readiness, then send a prompt.";
        primaryGuideAction.textContent = !connected ? "Refresh .env" : state.target === "hosted" ? "Deploy" : localOk ? "Send prompt" : localRunning ? "Check local" : "Start local";
        setActiveStep(state.target === "hosted" ? "foundry" : "local");
      }
      settingsOpen = false;
      advancedRow.hidden = true;
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
      const localBlocked = activeView === "chat" && latestState?.target !== "hosted" && !connected;
      chatView.hidden = activeView !== "chat";
      deployView.hidden = activeView !== "deploy";
      teamsView.hidden = activeView !== "teams";
      sendButton.hidden = activeView !== "chat";
      sendButton.disabled = inFlight || localBlocked;
      provisionButton.hidden = true;
      deployButton.hidden = true;
      teamsTestedButton.hidden = true;
      promptInput.hidden = activeView !== "chat";
      promptInput.disabled = localBlocked;
      checkHealthButton.disabled = localBlocked;
      startLocalButton.hidden = activeView !== "chat" || latestState?.target === "hosted";
      startLocalButton.disabled = localBlocked;
      startLocalButton.textContent = latestState?.localRun?.running ? "Stop local" : "Start local";
      stopLocalAction.hidden = !(activeView === "chat" && latestState?.target !== "hosted" && latestState?.localRun?.running);
      stopLocalAction.disabled = false;
      clearButton.hidden = activeView === "teams";
      clearButton.textContent = activeView === "deploy" ? "Clear deploy log" : "Clear transcript";
    }

    function renderDeploy(state) {
      const hosted = state.hosted || {};
      foundryTarget.textContent = hosted.projectEndpoint || "Not discovered";
      hostedAgent.textContent = (hosted.agentName || state.selectedAgent?.displayName || "minimal-agent") + " · " + (state.selectedAgent?.rootLabel || "");
      hostedVersion.textContent = hosted.version ? "Version " + hosted.version : "Not deployed";
      const lines = state.deployment?.log || [];
      deployLog.textContent = lines.length ? lines.join("") : [
        "$ azd env get-values\\n",
        "Discover the deployed Foundry agent version and protocol endpoints.\\n\\n",
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
      if (!(latestState?.foundryConnection?.projectEndpoint && latestState?.foundryConnection?.modelDeployment) && latestState?.target !== "hosted") {
        setStatus("fail", "Fill .env from .env.example with FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME, then refresh.");
        return;
      }
      const input = promptInput.value.trim();
      if (!input) return;
      const now = Date.now();
      if (lastSend.input === input && now - lastSend.at < 1500) return;
      lastSend = { input, at: now };
      inFlight = true;
      sendButton.disabled = true;
      setStatus("", "Sending prompt...");
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
      return request("/api/endpoint", {
        method: "POST",
        body: JSON.stringify({ endpoint: endpointInput.value, target: latestState?.target || "local" }),
      });
    }

    async function connectFoundryFromInputs() {
      connectFoundryButton.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", "Using Foundry project...");
      try {
        const state = await request("/api/foundry/connect", {
          method: "POST",
          body: JSON.stringify({
            projectEndpoint: foundryEndpointInput.value,
            modelDeployment: modelDeploymentInput.value,
          }),
        });
        settingsOpen = false;
        renderSnapshot(state);
        setStatus("ok", "Foundry project ready.");
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        connectFoundryButton.disabled = false;
        primaryGuideAction.disabled = false;
      }
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
      startLocalButton.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", "Starting local agent...");
      try {
        const state = await request("/api/local/start", { method: "POST" });
        renderSnapshot(state);
        setStatus("", "Local agent starting: " + (state.localRun?.command || "agent"));
        window.setTimeout(() => void load(), 1200);
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        primaryGuideAction.disabled = false;
        renderView();
      }
    }

    async function stopLocalFromCanvas() {
      startLocalButton.disabled = true;
      stopLocalAction.disabled = true;
      primaryGuideAction.disabled = true;
      setStatus("", "Stopping local agent...");
      try {
        const state = await request("/api/local/stop", { method: "POST" });
        renderSnapshot(state);
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

    endpointInput.addEventListener("change", async () => {
      try {
        renderSnapshot(await saveEndpointFromInput());
      } catch (error) {
        setStatus("fail", error.message);
      }
    });

    connectFoundryButton.addEventListener("click", async () => {
      await connectFoundryFromInputs();
    });

    startLocalButton.addEventListener("click", async () => {
      if (latestState?.localRun?.running) {
        await stopLocalFromCanvas();
      } else {
        await startLocalFromCanvas();
      }
    });

    stopLocalAction.addEventListener("click", async () => {
      await stopLocalFromCanvas();
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
      const state = await request("/api/target", {
        method: "POST",
        body: JSON.stringify({ target: "local" }),
      });
      renderSnapshot(state);
      setStatus("", "Local target selected.");
    }

    async function showFoundryChat() {
      setStatus("", "Discovering Foundry target...");
      activeView = "chat";
      settingsOpen = false;
      const state = await request("/api/target", {
        method: "POST",
        body: JSON.stringify({ target: "hosted", refresh: true }),
      });
      renderSnapshot(state);
      setStatus(state.hosted?.responsesEndpoint ? "ok" : "fail", state.hosted?.responsesEndpoint ? "Foundry target selected." : "Foundry endpoint not discovered.");
    }

    async function showDeploy() {
      activeView = "deploy";
      settingsOpen = false;
      const state = await request("/api/hosted/refresh", { method: "POST" });
      renderSnapshot(state);
      setStatus("", "Foundry step.");
    }

    async function showFoundry() {
      setStatus("", "Refreshing Foundry...");
      settingsOpen = false;
      const refreshed = await request("/api/hosted/refresh", { method: "POST" });
      if (refreshed.hosted?.version || refreshed.hosted?.responsesEndpoint) {
        activeView = "chat";
        const state = await request("/api/target", {
          method: "POST",
          body: JSON.stringify({ target: "hosted" }),
        });
        renderSnapshot(state);
        setStatus(state.hosted?.responsesEndpoint ? "ok" : "fail", state.hosted?.responsesEndpoint ? "Foundry target selected." : "Foundry endpoint not discovered.");
        return;
      }
      activeView = "deploy";
      renderSnapshot(refreshed);
      setStatus("", "Foundry step.");
    }

    async function showTeams() {
      activeView = "teams";
      const state = await request("/api/hosted/refresh", { method: "POST" });
      renderSnapshot(state);
      setStatus("", "Teams step.");
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
        void refreshConfigFromDisk();
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
      } else if (latestState?.target === "hosted") {
        void showDeploy();
      } else if (latestState?.lastHealth?.ok || latestState?.messages?.some((message) => message.target !== "hosted" && message.response?.ok)) {
        promptInput.focus();
      } else if (!latestState?.localRun?.running) {
        void startLocalFromCanvas();
      } else {
        checkHealthButton.click();
      }
    });

    testHostedAction.addEventListener("click", () => {
      void showFoundryChat();
    });

    checkHealthButton.addEventListener("click", async () => {
      if (!(latestState?.foundryConnection?.projectEndpoint && latestState?.foundryConnection?.modelDeployment) && latestState?.target !== "hosted") {
        setStatus("fail", "Fill .env from .env.example with FOUNDRY_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME, then refresh.");
        return;
      }
      checkHealthButton.disabled = true;
      setStatus("", "Checking readiness...");
      try {
        await saveEndpointFromInput();
        renderSnapshot(await request("/api/health", { method: "POST" }));
      } catch (error) {
        setStatus("fail", error.message);
      } finally {
        renderView();
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
      renderSnapshot(await request(activeView === "deploy" ? "/api/deploy/clear" : "/api/clear", { method: "POST" }));
      setStatus("", activeView === "deploy" ? "Deploy log cleared." : "Transcript cleared.");
    });

    async function streamCommand({ path, button, confirmText, runningText, successText, failureText }) {
      if (!confirm(confirmText)) return;
      button.disabled = true;
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
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/foundry/connect") {
            const body = await readBody(req);
            await connectFoundry(state, {
                projectEndpoint: body.projectEndpoint,
                modelDeployment: body.modelDeployment,
            });
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
            state.lastHealth = await checkReadiness(activeEndpoint(state));
            sendJson(res, 200, stateSnapshot(state));
            return;
        }
        if (req.method === "POST" && url.pathname === "/api/local/start") {
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
            state.messages.length = 0;
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
        foundryConnection: emptyFoundryConnection(),
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

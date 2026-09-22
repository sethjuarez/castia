import { readdir, readFile } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";

export function serviceEnvPrefix(serviceName) {
    return `AGENT_${String(serviceName || "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase()}`;
}

export function normalizeRootLabel(root, workspace = process.cwd()) {
    const label = relative(workspace, root) || ".";
    return label.split(/[\\/]+/).join("\\");
}

export function parseHostedServices(yaml, filePath, workspace = process.cwd()) {
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
        const rootLabel = normalizeRootLabel(agentRoot, workspace);
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

export async function findAzureYamlFiles(dir, depth = 0) {
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

export function uniqueAgents(agents) {
    const seen = new Set();
    return agents.filter((agent) => {
        const key = agent.id;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function azureYamlSortKey(workspace, file) {
    const label = normalizeRootLabel(dirname(file), workspace);
    const depth = label === "." ? 0 : label.split("\\").length;
    return { depth, label };
}

function compareAzureYamlFiles(workspace) {
    return (left, right) => {
        const a = azureYamlSortKey(workspace, left);
        const b = azureYamlSortKey(workspace, right);
        return a.depth - b.depth || a.label.localeCompare(b.label);
    };
}

export async function discoverAgents({
    workspace = process.cwd(),
    defaultAgentRoot,
    defaultServiceName = "minimal-agent",
} = {}) {
    const root = resolve(workspace);
    const fallbackAgentRoot = defaultAgentRoot || join(root, "examples", "python", "minimal-agent");
    const defaultRoot = isAbsolute(String(fallbackAgentRoot || ""))
        ? resolve(fallbackAgentRoot)
        : resolve(root, String(fallbackAgentRoot || ""));
    const files = (await findAzureYamlFiles(root)).sort(compareAzureYamlFiles(root));
    const agents = [];
    for (const file of files) {
        const yaml = await readFile(file, "utf8").catch(() => "");
        agents.push(...parseHostedServices(yaml, file, root));
    }
    const dedupedAgents = uniqueAgents(agents);
    if (dedupedAgents.length) {
        return dedupedAgents.sort((a, b) => a.rootLabel.localeCompare(b.rootLabel) || a.serviceName.localeCompare(b.serviceName));
    }
    const rootLabel = normalizeRootLabel(defaultRoot, root);
    return [
        {
            id: `${rootLabel}:${defaultServiceName}`,
            serviceName: defaultServiceName,
            displayName: defaultServiceName,
            root: defaultRoot,
            rootLabel,
            envPrefix: serviceEnvPrefix(defaultServiceName),
        },
    ];
}

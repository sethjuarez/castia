import { access, readFile } from "node:fs/promises";
import { delimiter, dirname, join } from "node:path";

async function exists(path) {
    try {
        await access(path);
        return true;
    } catch {
        return false;
    }
}

export async function isManagedPythonAppRoot(root) {
    if (!(await exists(join(root, "main.py")))) return false;
    const pyproject = join(root, "pyproject.toml");
    const hasPyproject = await exists(pyproject);
    if (!hasPyproject) return false;
    if (await exists(join(root, "uv.lock"))) return true;
    const text = await readFile(pyproject, "utf8");
    if (/(^|\n)\s*\[tool\.uv\]/.test(text) ||
        /(^|\n)\s*package\s*=\s*false\b/.test(text) ||
        /\bcastia(?:\[|\b)/i.test(text)) {
        return true;
    }
    const main = await readFile(join(root, "main.py"), "utf8");
    return /\bfrom\s+castia\b|\bimport\s+castia\b/.test(main);
}

export async function localStartCommand(agent, { workspaceRoot = process.cwd() } = {}) {
    if (!(await exists(join(agent.root, "main.py")))) return null;
    const managed = await isManagedPythonAppRoot(agent.root);
    if (managed) {
        return {
            command: "uv",
            args: ["run", "--directory", agent.root, "python", "main.py"],
            cwd: agent.root,
            env: {},
            managed: true,
            manager: "uv",
            startupTimeoutMs: 120000,
            startupPhase: "dependency_sync",
        };
    }

    const localVenv = process.platform === "win32"
        ? join(agent.root, ".venv", "Scripts", "python.exe")
        : join(agent.root, ".venv", "bin", "python");
    const workspaceVenv = process.platform === "win32"
        ? join(workspaceRoot, ".venv", "Scripts", "python.exe")
        : join(workspaceRoot, ".venv", "bin", "python");
    const pythonPath = await discoverPythonPath(agent.root);
    if (!(await exists(localVenv)) && !(await exists(workspaceVenv)) && pythonPath) {
        const packageRoot = dirname(pythonPath);
        const extras = ["deploy", "optimize", "test", ...(await localSdkExtrasForAgent(agent))];
        return {
            command: "uv",
            args: [
                "run",
                "--project",
                packageRoot,
                "--with-editable",
                packageRoot,
                ...extras.flatMap((extra) => ["--extra", extra]),
                "python",
                "main.py",
            ],
            cwd: agent.root,
            env: {},
            managed: false,
            manager: "uv-sdk-editable",
            startupTimeoutMs: 15000,
            startupPhase: "starting",
        };
    }
    const command = await exists(localVenv) ? localVenv : await exists(workspaceVenv) ? workspaceVenv : "python";
    return {
        command,
        args: ["main.py"],
        cwd: agent.root,
        env: pythonPath
            ? { PYTHONPATH: [pythonPath, process.env.PYTHONPATH].filter(Boolean).join(delimiter) }
            : {},
        managed: false,
        manager: command === "python" ? "python" : "venv",
        startupTimeoutMs: 15000,
        startupPhase: "starting",
    };
}

export async function localSdkExtrasForAgent(agent) {
    const extras = new Set();
    const pyproject = join(agent.root, "pyproject.toml");
    if (await exists(pyproject)) {
        const text = await readFile(pyproject, "utf8");
        const dependencyExtras = text.matchAll(/castia\[([^\]]+)\]/g);
        for (const match of dependencyExtras) {
            for (const extra of match[1].split(",")) {
                const normalized = extra.trim();
                if (normalized) extras.add(normalized);
            }
        }
    }
    return [...extras].filter((extra) => !["deploy", "optimize", "test"].includes(extra)).sort();
}

export async function discoverPythonPath(start) {
    let current = start;
    while (true) {
        const sourceRoot = join(current, "packages", "python", "src");
        if (await exists(join(sourceRoot, "castia", "__init__.py"))) return sourceRoot;
        const parent = dirname(current);
        if (parent === current) return null;
        current = parent;
    }
}

export function stderrTail(chunks = [], maxChars = 4000) {
    return chunks.join("").slice(-maxChars).trim();
}

export function localStartupFailure({ localRun, stderr = "", error = null } = {}) {
    const exitCode = localRun?.exitCode ?? 1;
    const stderrText = String(stderr || "").trim();
    const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error || "");
    const rootCause = stderrText || message || `Local agent exited with code ${exitCode}.`;
    const moduleNotFound = /ModuleNotFoundError:\s+No module named ['"]castia['"]/i.test(rootCause);
    const managed = Boolean(localRun?.launcher?.managed);
    const uvCommand = localRun?.launcher?.managedCommand || localRun?.command || "";
    const suggestion = moduleNotFound && managed
        ? `This looks like a managed Castia app. Start it with ${uvCommand} so uv can create/sync the environment before running main.py.`
        : null;
    return {
        command: localRun?.command || "",
        cwd: localRun?.cwd || "",
        exitCode,
        stderrTail: stderrText,
        rootCause,
        suggestion,
    };
}

import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
    isManagedPythonAppRoot,
    localStartCommand,
    localStartupFailure,
} from "../domain/local-launcher.mjs";

async function tempAgent(files) {
    const root = await mkdtemp(join(tmpdir(), "castia-local-launcher-"));
    for (const [name, content] of Object.entries(files)) {
        await writeFile(join(root, name), content, "utf8");
    }
    return {
        id: "agent-1",
        root,
        serviceName: "contract-expert",
        displayName: "contract-expert",
    };
}

test("localStartCommand prefers uv run --directory for managed Castia app roots", async () => {
    const agent = await tempAgent({
        "main.py": "from castia import Agent\n",
        "pyproject.toml": `[project]
name = "contract-expert"
dependencies = ["castia[optimize]==0.7.6"]

[tool.uv]
package = false
`,
        "uv.lock": "",
    });

    assert.equal(await isManagedPythonAppRoot(agent.root), true);

    const command = await localStartCommand(agent, { workspaceRoot: agent.root });

    assert.equal(command.command, "uv");
    assert.deepEqual(command.args, ["run", "--directory", agent.root, "python", "main.py"]);
    assert.equal(command.cwd, agent.root);
    assert.equal(command.managed, true);
    assert.equal(command.startupTimeoutMs, 120000);
    assert.equal(command.startupPhase, "dependency_sync");
});

test("localStartCommand preserves bare Python fallback for unmanaged app roots", async () => {
    const agent = await tempAgent({
        "main.py": "print('hello')\n",
    });

    assert.equal(await isManagedPythonAppRoot(agent.root), false);

    const command = await localStartCommand(agent, { workspaceRoot: agent.root });

    assert.equal(command.command, "python");
    assert.deepEqual(command.args, ["main.py"]);
    assert.equal(command.managed, false);
    assert.equal(command.startupTimeoutMs, 15000);
});

test("managed root detection accepts Castia imports when pyproject has no uv metadata", async () => {
    const agent = await tempAgent({
        "main.py": "import castia\n",
        "pyproject.toml": `[project]
name = "custom-agent"
dependencies = []
`,
    });

    assert.equal(await isManagedPythonAppRoot(agent.root), true);
});

test("localStartupFailure includes command, cwd, exit code, stderr tail, and uv remediation", () => {
    const failure = localStartupFailure({
        localRun: {
            command: "uv run --directory C:\\agent python main.py",
            cwd: "C:\\agent",
            exitCode: 1,
            launcher: {
                managed: true,
                managedCommand: "uv run --directory C:\\agent python main.py",
            },
        },
        stderr: "Traceback...\nModuleNotFoundError: No module named 'castia'\n",
    });

    assert.equal(failure.command, "uv run --directory C:\\agent python main.py");
    assert.equal(failure.cwd, "C:\\agent");
    assert.equal(failure.exitCode, 1);
    assert.match(failure.stderrTail, /ModuleNotFoundError/);
    assert.match(failure.rootCause, /No module named 'castia'/);
    assert.match(failure.suggestion, /uv run --directory/);
});

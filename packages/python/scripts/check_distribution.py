"""Check an installed wheel, without importing the editable source checkout."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    import castia

    installed = Path(castia.__file__).resolve()
    if not installed.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"Expected an installed wheel inside {sys.prefix}, got {installed}")
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected_version = tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
    version = importlib.metadata.version("castia")
    if version != expected_version:
        raise RuntimeError(f"Installed version {version} does not match {expected_version}")

    root_files = {path.name for path in installed.parent.iterdir() if path.is_file()}
    if root_files != {"__init__.py", "__main__.py", "py.typed"}:
        raise RuntimeError(f"Unexpected installed package root files: {sorted(root_files)}")
    heavy = ("azure", "openai", "httpx", "fastapi", "uvicorn", "opentelemetry")
    loaded = [name for name in sys.modules if name.split(".")[0] in heavy]
    if loaded:
        raise RuntimeError(f"Root import eagerly loaded host dependencies: {loaded}")
    for name in castia.__all__:
        getattr(castia, name)

    families = ("", "build", "observe", "lifecycle", "deploy", "eval", "optimize", "finetune")
    for family in families:
        command = [sys.executable, "-I", "-W", "error", "-m", "castia"]
        if family:
            command.append(family)
        result = subprocess.run(
            [*command, "--help"], check=False, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0 or "usage:" not in result.stdout:
            raise RuntimeError(f"{family or 'root'} help failed:\n{result.stdout}\n{result.stderr}")

    from castia.building import AgentTestHarness

    agent = castia.Agent(name="installed-wheel-check")

    @agent.responses()
    async def echo(text: str) -> str:
        return text

    async def check_responses() -> None:
        async with AgentTestHarness(agent) as harness:
            response = await harness.client.post("/responses", json={"input": "installed wheel"})
            if response.status_code != 200 or response.json().get("output_text") != "installed wheel":
                raise RuntimeError(f"Installed-wheel Responses check failed: {response.text}")

    asyncio.run(check_responses())
    print(json.dumps({
        "version": version,
        "python": sys.version.split()[0],
        "installed_package": str(installed),
        "public_exports": len(castia.__all__),
        "cli_help_surfaces": len(families),
        "offline_responses": "pass",
    }))


if __name__ == "__main__":
    main()

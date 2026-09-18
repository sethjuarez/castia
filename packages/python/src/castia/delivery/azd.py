"""Deploy only a named service in an explicitly selected existing project."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


@dataclass(frozen=True)
class DeploymentReceipt:
    project_endpoint: str
    project_resource_id: str
    agent_name: str
    agent_version: str
    model: str | None
    candidate_id: str | None
    content_hash: str | None


class AzdCommandError(RuntimeError):
    """Failed handoff with diagnostic output kept out of the printable message.

    ``stdout`` and ``stderr`` are for explicit local debugging. They can contain
    private hook output and must not be copied into public lifecycle records.
    """

    def __init__(self, command: list[str], returncode: int, stdout: str, stderr: str) -> None:
        super().__init__(
            f"azd {' '.join(command[:3])} exited {returncode}; "
            "inspect deployment logs and verify remote state before retrying"
        )
        self.command = tuple(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AzdDeployment:
    """Deploy from a validated, explicitly prepared azd environment.

    The authored manifest remains authoritative. Model/candidate expectations
    verify the resulting hosted environment; they do not silently rewrite it.
    Review azd hooks and endpoint configuration before authorizing deployment.
    """

    def __init__(
        self, root: str | os.PathLike[str], *, service: str, project_endpoint: str,
        project_resource_id: str,
        environment: str | None = None, timeout: float = 900,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", service):
            raise ValueError("service must be a plain azure.yaml service name")
        if environment is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", environment):
            raise ValueError("environment must be a plain azd environment name")
        url = urlsplit(project_endpoint)
        if (
            url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment
            or not re.fullmatch(r"/api/projects/[^/]+/?", url.path)
        ):
            raise ValueError("project_endpoint must be an explicit HTTPS Foundry project URL")
        resource = re.fullmatch(
            r"/subscriptions/([^/]+)/resourceGroups/[^/?#\\]+"
            r"/providers/Microsoft\.CognitiveServices/accounts/[^/?#\\]+/projects/([^/?#\\]+)",
            project_resource_id.rstrip("/"), re.IGNORECASE,
        )
        if resource is None:
            raise ValueError("project_resource_id must identify a Foundry project ARM resource")
        try:
            subscription = str(UUID(resource[1]))
        except ValueError as exc:
            raise ValueError("project_resource_id must contain a subscription UUID") from exc
        if url.path.rstrip("/").rsplit("/", 1)[-1].casefold() != resource[2].casefold():
            raise ValueError("project endpoint and ARM resource must name the same project")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.root = Path(root).resolve(strict=True)
        self.service = service
        self.project_endpoint = project_endpoint.rstrip("/")
        self.project_resource_id = project_resource_id.rstrip("/")
        self.subscription_id = subscription
        self.environment = environment
        self.timeout = timeout
        self._validate_manifest()

    def _validate_manifest(self) -> None:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load((self.root / "azure.yaml").read_text(encoding="utf-8"))
        services = data.get("services") if isinstance(data, dict) else None
        service = services.get(self.service) if isinstance(services, dict) else None
        if not isinstance(service, dict) or service.get("host") != "azure.ai.agent":
            raise ValueError("named service must exist and use host: azure.ai.agent")
        from castia.delivery.manifest import validate_hosted_service_env

        validate_hosted_service_env(self.service, service)
        if service.get("name", self.service) != self.service:
            raise ValueError("service key and deployed agent name must agree")
        project = (self.root / str(service.get("project", "."))).resolve(strict=True)
        if not project.is_relative_to(self.root) or not project.is_dir():
            raise ValueError("service project directory must stay inside the deployment root")

    def _run(self, arguments: list[str]) -> str:
        executable = shutil.which("azd")
        if not executable:
            raise FileNotFoundError("azd is required for deployment handoff")
        env = dict(os.environ)
        # These cover host-side consumers only. The extension prioritizes stored
        # azd values, which must also pass _validate_environment.
        for key in ("FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT", "AZURE_AIPROJECT_ENDPOINT"):
            env[key] = self.project_endpoint
        for key in ("AZURE_AI_PROJECT_ID", "FOUNDRY_PROJECT_RESOURCE_ID", "AZURE_AI_PROJECT_RESOURCE_ID"):
            env[key] = self.project_resource_id
        env["AZURE_SUBSCRIPTION_ID"] = self.subscription_id
        argv = [executable, *arguments]
        if self.environment:
            argv += ["--environment", self.environment]
        try:
            completed = subprocess.run(
                argv, cwd=self.root, env=env, capture_output=True, text=True,
                timeout=self.timeout, check=False, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                "azd timed out; an Azure-side deployment may still be running. "
                "Verify remote state before retrying or rolling back."
            ) from exc
        if completed.returncode:
            # Keep raw azd output out of persisted reports: hooks may print secrets.
            raise AzdCommandError(
                arguments, completed.returncode, completed.stdout, completed.stderr,
            )
        return completed.stdout

    def _validate_environment(self) -> None:
        values = json.loads(self._run(["env", "get-values", "--output", "json", "--no-prompt"]))
        if not isinstance(values, dict):
            raise TypeError("azd environment inspection returned non-object JSON")
        name = values.get("AZURE_ENV_NAME")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ValueError("azd environment must have a valid AZURE_ENV_NAME")
        if self.environment is not None and name != self.environment:
            raise ValueError("azd resolved a different environment from the requested environment")
        for key in ("FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT", "AZURE_AIPROJECT_ENDPOINT"):
            value = values.get(key)
            if key != "FOUNDRY_PROJECT_ENDPOINT" and value is None:
                continue
            if not isinstance(value, str) or value.rstrip("/") != self.project_endpoint:
                raise ValueError(f"stored azd {key} must match the approved project endpoint")
        for key in ("AZURE_AI_PROJECT_ID", "FOUNDRY_PROJECT_RESOURCE_ID", "AZURE_AI_PROJECT_RESOURCE_ID"):
            value = values.get(key)
            if key != "AZURE_AI_PROJECT_ID" and value is None:
                continue
            if (
                not isinstance(value, str)
                or value.rstrip("/").casefold() != self.project_resource_id.casefold()
            ):
                raise ValueError(f"stored azd {key} must match the approved project ARM resource")
        subscription = values.get("AZURE_SUBSCRIPTION_ID")
        if not isinstance(subscription, str) or subscription.casefold() != self.subscription_id:
            raise ValueError("stored azd AZURE_SUBSCRIPTION_ID must match the approved subscription")
        # Freeze default-environment selection before any resource operation.
        self.environment = name

    def show(self) -> dict:
        self._validate_environment()
        payload = json.loads(self._run(["ai", "agent", "show", self.service, "--output", "json"]))
        self._validate_environment()
        if not isinstance(payload, dict):
            raise TypeError("azd agent show returned non-object JSON")
        if payload.get("name") != self.service:
            raise ValueError("azd resolved a different agent from the requested service")
        if not isinstance(payload.get("version"), str) or not payload["version"]:
            raise ValueError("deployed agent has no version")
        return payload

    def verify(
        self, *, expected_model: str | None = None, expected_candidate: str | None = None,
        expected_version: str | None = None,
    ) -> DeploymentReceipt:
        """Verify deployment identity/configuration; callers also run functional probes."""
        payload = self.show()
        if payload.get("status") != "active":
            raise ValueError(f"agent version is not active: {payload.get('status')!r}")
        if expected_version is not None and payload["version"] != expected_version:
            raise ValueError("deployed agent version differs from the expected version")
        definition = payload.get("definition")
        if not isinstance(definition, dict):
            raise TypeError("deployed agent is missing its definition")
        if definition.get("kind") != "hosted":
            raise ValueError("deployment verification requires a hosted agent")
        env = definition.get("environment_variables", {})
        if not isinstance(env, dict):
            raise TypeError("deployed environment_variables must be an object")
        model = env.get("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        candidate = env.get("OPTIMIZATION_CANDIDATE_ID")
        if expected_model is not None and model != expected_model:
            raise ValueError("deployed model differs from the expected model")
        if expected_candidate is not None and candidate != expected_candidate:
            raise ValueError("deployed candidate differs from the expected candidate")
        code = definition.get("code_configuration", {})
        content_hash = code.get("content_hash") if isinstance(code, dict) else None
        for name, value in (("model", model), ("candidate", candidate), ("content hash", content_hash)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"deployed {name} must be a string")
        return DeploymentReceipt(
            self.project_endpoint, self.project_resource_id, self.service,
            payload["version"], model, candidate, content_hash,
        )

    def deploy(
        self, *, expected_model: str | None = None, expected_candidate: str | None = None,
    ) -> DeploymentReceipt:
        """Explicitly deploy one service, then verify its returned configuration."""
        self._validate_manifest()
        self._validate_environment()
        self._run(["deploy", self.service, "--no-prompt"])
        return self.verify(expected_model=expected_model, expected_candidate=expected_candidate)

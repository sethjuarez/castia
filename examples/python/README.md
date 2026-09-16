# Python examples

These examples are checked-in proving grounds for the Castia agent lifecycle.
Use them when validating that a new Copilot session, plugin, or canvas can stand
up an agent before moving to live Foundry resources.

## Minimal agent

[`minimal-agent`](minimal-agent) is a small multi-protocol agent for the
end-to-end story:

1. make an agent;
2. test it locally through the Responses protocol;
3. prepare the same app for Foundry hosted deployment and Teams Activity turns.

The app is generated from Castia's scaffold and serves Activity, Responses, and
Invocations with one handler. Its test suite uses `AgentTestHarness`, so local
validation does not require Azure credentials, model access, or network calls.

From the repository root:

```powershell
Set-Location packages\python
uv venv --python 3.13
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
uv pip install --python $python --prerelease=allow -e ".[deploy,optimize,test]"
Set-Location ..\..\examples\python\minimal-agent
```

Run the offline gates:

```powershell
& $python -m castia deploy --app main:app
& $python -m castia optimize --app main:app
& $python -m castia build check .
& $python -m castia build test . --timeout 60
& $python -m castia eval check --config eval.yaml
```

`build check` requires `FOUNDRY_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME` to pass completely. Set real values before a
live model call or deployment; dummy values are only useful for exercising the
offline shape. The example starts with `gpt-5.5`; make sure that value matches
an existing deployment name in the selected Foundry project.

To test the Responses protocol manually after configuring a reachable model:

```powershell
& $python main.py
Invoke-RestMethod http://127.0.0.1:8088/readiness
Invoke-RestMethod http://127.0.0.1:8088/responses `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"input":"Say hello from the minimal agent."}'
```

For a real streaming smoke test, request the Responses stream:

```powershell
curl.exe -N http://127.0.0.1:8088/responses `
  -H "Content-Type: application/json" `
  -H "Accept: text/event-stream" `
  -d '{"input":"Say hello from the minimal agent.","stream":true}'
```

For hosted deployment, follow
[`minimal-agent\DEPLOYMENT.md`](minimal-agent/DEPLOYMENT.md). Deployment
uses an existing Foundry project and model deployment; the example does not
provision cloud resources.

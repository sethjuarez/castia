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

`app.run()` uses `PORT` when set and otherwise falls back to `8088`. When
running more than one local Castia agent, assign each shell or playground launch
a distinct port, for example `$env:PORT = "8089"`, and point readiness and
Responses requests at that port.

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

## Prompty agent

[`prompty-agent`](prompty-agent) is a Responses-only agent that exercises the
optional Prompty runtime harness added by `castia[prompty]`. It keeps
`.agent_configs` as the optimizer contract, projects that config into a Prompty
runner, and includes optional host-side toolbox MCP wiring for Prompty function
tools.

From the repository root:

```powershell
Set-Location packages\python
uv venv --python 3.13
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
uv pip install --python $python --prerelease=allow -e ".[deploy,optimize,prompty,test]"
Set-Location ..\..\examples\python\prompty-agent
```

Run the offline tests:

```powershell
& $python -m pytest -q
```

The tests monkeypatch the Prompty runner and toolbox client, so they validate
the Castia protocol/wiring shape without Azure credentials, model access, or
network calls. For a live run, copy `.env.example` to `.env`, fill the Foundry
endpoint and deployment name, and start `main.py` from the example directory.

# Hosted invoke trace lookup

Use this workflow for any Castia-built Microsoft Foundry Hosted Agent when
`azd ai agent invoke` returns a trace/session/conversation ID but the terminal
output is missing, incomplete, or ambiguous. The goal is to identify which layer
failed without rediscovering App Insights table mappings.

## Inputs

Resolve as many as possible from `azd ai agent invoke`, `azd env get-values`,
`azure.yaml`, or project context:

| Input | Meaning |
| --- | --- |
| `traceId` | App Insights `operation_Id`; azd may print this as the trace ID. |
| `conversationId` | Hosted conversation ID, commonly `conv_...`. |
| `sessionId` | Hosted Agent session ID for `azd ai agent monitor --session-id`. |
| `responseId` | Hosted response ID, commonly `caresp_...`. |
| `agentName` | Foundry Hosted Agent name or azd service name. |
| App Insights resource ID | Resource to query with Azure Monitor / App Insights KQL. |

If only a trace ID is available, start with the `operation_Id` queries below. If
only an agent name or session ID is available, start with hosted logs and request
search.

## Hosted logs first

Use azd logs to confirm the hosted process received the request and returned a
status:

```powershell
azd ai agent monitor --tail 120
```

For a specific session:

```powershell
azd ai agent sessions list --output table
azd ai agent monitor --session-id <session-id> --tail 120
```

Look for readiness checks, `/responses` requests, exceptions, and framework or
server errors.

## App Insights trace ID lookup

When running from PowerShell, prefer one-line KQL strings. Multiline `let`
blocks are easy to corrupt through shell quoting and can produce misleading KQL
parse errors.

### Requests

Confirms whether the hosted HTTP endpoint and Foundry AgentServer invocation
succeeded:

```kql
requests | where timestamp > ago(2h) | where operation_Id == "<trace-id>" | project timestamp, name, resultCode, success, duration, operation_Id, id, operation_ParentId, customDimensions | order by timestamp asc
```

Interpretation:

| Signal | Meaning |
| --- | --- |
| `POST /responses`, `resultCode=200`, `success=true` | Hosted app accepted and completed the Responses request. |
| `invoke_agent`, `success=true` | Foundry AgentServer invocation succeeded. |
| Missing request rows | Wrong App Insights resource, wrong time range, telemetry delay, or wrong trace ID. |
| 4xx/5xx request rows | Diagnose the hosted app or platform failure before checking downstream spans. |

### Dependencies

Checks LLM calls, tool calls, and agent spans under the same trace:

```kql
dependencies | where timestamp > ago(2h) | where operation_Id == "<trace-id>" | project timestamp, name, type, target, resultCode, success, duration, operation_ParentId, customDimensions | order by timestamp asc
```

Interpretation:

| Signal | Meaning |
| --- | --- |
| `chat <model>`, `success=true`, completed finish reason | Model call completed; inspect token counts and output-message metadata. |
| `execute_tool`, `success=false` | Tool execution failed; inspect tool attributes and hosted logs. |
| `invoke_agent`, `success=false` | Agent orchestration failed; inspect error attributes and exceptions. |
| No `chat` span | Request failed before model invocation, short-circuited, or telemetry is incomplete. |

### Traces, exceptions, and custom events

Use traces for app logs and GenAI events:

```kql
traces | where timestamp > ago(2h) | where operation_Id == "<trace-id>" | project timestamp, severityLevel, message, operation_ParentId, customDimensions | order by timestamp asc
```

Use exceptions for stack traces:

```kql
exceptions | where timestamp > ago(2h) | where operation_Id == "<trace-id>" | project timestamp, type, message, outerMessage, operation_ParentId, details | order by timestamp asc
```

Use custom events for evaluation or other emitted result records:

```kql
customEvents | where timestamp > ago(2h) | where operation_Id == "<trace-id>" | project timestamp, name, customDimensions | order by timestamp asc
```

## When azd does not print answer text

App Insights often stores message shape, response IDs, token counts, and GenAI
attributes rather than raw answer text. Missing literal answer text in telemetry
does not prove the hosted agent returned no answer.

If all of these are true, the hosted code path likely succeeded and the issue is
probably CLI/platform response rendering or stream display:

1. `requests` has `POST /responses` with `resultCode=200` and `success=true`.
2. `requests` has `invoke_agent` with `success=true`.
3. `dependencies` has `chat <model>` with `success=true`, a completed finish
   reason, and output token count.
4. `gen_ai.output.messages` or equivalent metadata indicates an assistant text
   part.
5. Server traces show `http.response.start` and `http.response.body`, when that
   framework telemetry is present.

If any of those are missing or failed, keep diagnosing the missing layer instead
of blaming CLI rendering.

## Search without a trace ID

When only the agent name is known, start from `requests` because hosted-agent
Foundry names are request-scoped:

```kql
requests | where timestamp > ago(24h) | extend foundryAgentName = coalesce(tostring(customDimensions["gen_ai.agent.name"]), tostring(customDimensions["azure.ai.agentserver.agent_name"])), agentId = tostring(customDimensions["gen_ai.agent.id"]), agentNameFromId = tostring(split(agentId, ":")[0]), agentVersion = iff(agentId contains ":", tostring(split(agentId, ":")[1]), ""), conversationId = coalesce(tostring(customDimensions["gen_ai.conversation.id"]), tostring(customDimensions["azure.ai.agentserver.conversation_id"]), operation_Id), responseId = tostring(customDimensions["azure.ai.agentserver.response_id"]) | where foundryAgentName == "<agent-name>" or agentNameFromId == "<agent-name>" | project timestamp, name, resultCode, success, duration, conversationId, responseId, agentVersion, operation_Id, customDimensions | order by timestamp desc | take 50
```

When a conversation ID or response ID is known, search request dimensions first:

```kql
requests | where timestamp > ago(24h) | where tostring(customDimensions["gen_ai.conversation.id"]) == "<conversation-id>" or tostring(customDimensions["azure.ai.agentserver.conversation_id"]) == "<conversation-id>" or tostring(customDimensions["azure.ai.agentserver.response_id"]) == "<response-id>" | project timestamp, name, resultCode, success, duration, operation_Id, customDimensions | order by timestamp asc
```

Then use the returned `operation_Id` with the trace ID lookup queries.

## Report format

Summarize findings by layer:

| Layer | Evidence | Status |
| --- | --- | --- |
| Hosted HTTP | `POST /responses` 200 | Success |
| AgentServer | `invoke_agent` success | Success |
| Model | `chat <model>` success, completed, tokens present | Success |
| Tools | no failed `execute_tool` spans | Success |
| Response body | server emitted response body | Success |
| Terminal rendering | azd did not print answer text | Suspect |

End with the most likely failing layer and the next concrete command or query,
not a generic recommendation.

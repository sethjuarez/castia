# Trace labels in the Foundry Traces UI

For application setup and content-recording controls, start with the
[consumer guide](AGENTS.md) and [observability configuration](README.md#observability--evaluation).
For bounded App Insights queries and verification, use
[the lifecycle guide](LIFECYCLE.md#inspect-traces-and-run-a-drift-suite).
Azure Monitor export is enabled when either `AZURE_MONITOR_CONNECTION_STRING` or
the App Insights-compatible `APPLICATIONINSIGHTS_CONNECTION_STRING` is present;
if both are set, `AZURE_MONITOR_CONNECTION_STRING` wins.

A working reference for the span badges in the trace tree — **In Process**,
**HTTP**, **Invoke Agent**, **Chat** — and a warning about one badge that lies.
Written after a bug bash where we watched the framework/HTTP badges flip on their
own and spent a long time blaming our own code before finding the real cause: a
non-deterministic query in the Foundry Traces portal.

## Streaming transport spans

Castia optimizes hosted traces for semantic readability. The Microsoft
OpenTelemetry distro instruments FastAPI through ASGI middleware; by default
that middleware can create a child span for every ASGI `send` and `receive`
event. A streaming `POST /responses` call may therefore produce many
zero-duration `POST /responses http send` children that describe chunks, plus
`POST /responses http receive` children that describe framework event handling
rather than bounded agent work.

Castia passes `exclude_spans=["send", "receive"]` to the FastAPI instrumentation by
default. The normal trace shape remains:

- one server/request span for `POST /responses`;
- semantic Castia spans such as `invoke_agent`;
- Foundry GenAI spans such as `chat {model}`;
- tool and retrieval spans.

Prompt and response text is intentionally absent unless content recording is
enabled. With content recording on, the standard Foundry instrumentor records
message payloads on the `chat {model}` span as `gen_ai.input.messages` and
`gen_ai.output.messages`; without it, the trace still carries timing, identity,
token, and operation metadata.

Castia sends user turns to the Responses API using explicit
`{"type": "input_text", "text": ...}` content parts. This is deliberately more
verbose than a bare string because the Foundry GenAI instrumentor can then record
the real prompt in `gen_ai.input.messages` when
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`. The same payload still
exports only role/type structure when content recording is off.

Set `CASTIA_OTEL_TRACE_ASGI_INTERNAL=true` only when debugging ASGI transport
behavior and you deliberately want per-event spans back. The older
`CASTIA_OTEL_TRACE_ASGI_SEND=true` flag is still accepted as a compatibility
alias. Do not record streamed chunk text by default. Castia records aggregate
transport attributes on the request span when available instead:
`stream.chunk_count`, `stream.first_chunk_ms`, `stream.last_chunk_ms`, and
`stream.bytes_sent`.

## Where the labels come from

Three different things feed the badges, and only the Castia-owned spans are fully
under our control.

**1. Our own operation spans — stable, ours to set.** The spans we create in
`src/castia/observe/tracing.py` (`invoke_agent`, `execute_tool`) carry `gen_ai.operation.name`, a
fixed vocabulary the portal recognises: `invoke_agent` → Invoke Agent, `chat` →
Chat, `execute_tool` → Execute Tool. We set these explicitly, at span creation,
on spans we own. They render correctly and do not flicker.

**2. Framework and client span "kind" — computed at export.** For every other
span (the SDK's `agents.adapter.process`, the auth/MSI HTTP calls, …) the badge
derives from the Azure-Monitor *dependency type* the exporter writes into App
Insights. That logic lives in
`azure/monitor/opentelemetry/exporter/export/trace/_exporter.py`:

| Span shape | Exported type | Badge |
| --- | --- | --- |
| `SpanKind.INTERNAL`, no mapping attributes | `InProc` | **In Process** |
| `SpanKind.INTERNAL` **with** `gen_ai.system` | `gen_ai.*` | (GenAI type) |
| `SpanKind.CLIENT` + `http.method` / `http.request.method` | `HTTP` | **HTTP** (renders the verb) |
| `SpanKind.CLIENT` + `db.system` / messaging / rpc | that system | (its own type) |
| `SpanKind.SERVER` / `CONSUMER` | — | a Request, not a dependency |
| `SpanKind.CLIENT` with none of the recognised attributes | *(blank)* | **Other** |

**3. Hosted Responses platform spans — server-side, platform-owned.** Foundry
hosted agents also receive server-side trace spans from the Responses service
(`cloud_RoleName == "responsesapi"`), including `invoke_agent {agent}:{version}`
and `chat {model-version}`. These spans are emitted outside the Castia process.
Castia cannot filter, re-parent, or change their content policy. Castia therefore
emits its own app-level telemetry by default and stamps SDK-owned spans with
`castia.telemetry.source = "castia"`. Use that attribute, or
`cloud_RoleName != "responsesapi"`, when you want the Castia-authoritative view
of model/tool behavior. The portal may still count both client and platform chat
spans if it aggregates every GenAI row in the operation.

These two chat spans are not semantically identical even when they represent the
same logical model turn. Castia's client-side `chat {model}` span measures the
SDK call roundtrip from the agent container to the Foundry Responses endpoint,
including client/network/platform overhead. Castia marks those spans with
`castia.telemetry.scope = "client_roundtrip"`. The platform
`responsesapi` `chat {model-version}` span is emitted server-side and measures
Foundry's internal model step. Compare them as correlated client/server evidence,
not as duplicate spans with equal duration semantics.

The platform spans are also not reliably parented under the Castia client span.
In live hosted traces, the `responsesapi` `invoke_agent {agent}:{version}` span
can have an `operation_ParentId` that is absent from the operation's exported
requests, dependencies, traces, exceptions, and customEvents. In that shape, the
platform span is correlated into the same `operation_Id`, but the exported parent
chain is incomplete:

```text
castia.prompty-agent prompty turn_async
  ├─ castia.prompty-agent chat gpt-5.5
  ├─ castia.prompty-agent execute_tool local_agent_fact
  └─ castia.prompty-agent chat gpt-5.5

responsesapi invoke_agent prompty-agent:<version>   # parent id missing
  └─ responsesapi chat gpt-5.5-<version>
```

That missing parent is platform telemetry Castia cannot repair after export.
Use `castia.telemetry.source == "castia"` for the SDK-owned hierarchy, and treat
`responsesapi` rows as correlated infrastructure/server-side evidence rather
than strict children of Castia's client spans.

Castia propagates the current W3C trace context (`traceparent` / `tracestate`)
on Responses API calls so downstream services can join the active operation when
they honor those headers. Live hosted validation with explicit headers confirmed
operation-level correlation, but the platform `responsesapi` `invoke_agent`
parents were still internal ids that were not exported in any App Insights table.
In other words, header propagation is standards-compliant and useful at the
client boundary, but it does not currently make platform-owned server spans strict
children of Castia client spans.

To verify whether a suspected parent is actually exported, search all App
Insights tables for both the missing id and rows parented to it:

```kusto
let trace_id = "<operation_Id>";
let missing_parents = dynamic(["<parent-id-1>", "<parent-id-2>"]);
union isfuzzy=true requests, dependencies, traces, exceptions, customEvents
| where operation_Id == trace_id
   or id in (missing_parents)
   or operation_ParentId in (missing_parents)
| project timestamp, itemType, operation_Id, id, parent = operation_ParentId,
          cloud_RoleName, name, type, success, duration
| order by timestamp asc
```

If this returns only the `responsesapi` children and no row whose `id` equals the
missing parent, the exported distributed trace is incomplete across the
container-to-platform boundary even though the rows share one `operation_Id`.

Do not treat Castia's `enable_content_recording=False` as a hosted-platform
content switch. It disables content on Castia-owned client telemetry, but
server-side `responsesapi` spans are emitted by Foundry and can still carry
`gen_ai.input.messages` / `gen_ai.output.messages` according to the platform's
own policy.

This exported `type` is written once, is single-valued, and is immutable in App
Insights. `az monitor app-insights query` returns the correct `HTTP` / `InProc`
for these spans every time, hours or days later.

## The one rule: stamp identity, restyle nothing

`_AgentIdentitySpanProcessor.on_start` (in `src/castia/observe/configuration.py`) runs on **every**
span on the provider. Its only job is to stamp the agent-identity attributes the
Foundry portal needs to associate the run with the agent:

- `gen_ai.agent.name`, `gen_ai.agent.version`, `gen_ai.agent.id`
- `microsoft.foundry.project.id`, `gen_ai.azure_ai_project.id`

Those keys are neutral — none is a mapping attribute the exporter keys off, so
they do not change any span's exported `type`. Keep it that way. **Do not** stamp
classification-affecting attributes (`gen_ai.operation.name`, `gen_ai.system`,
`http.*`) onto framework or client spans from a blanket processor; set operation
names only on spans we own, at creation, in `src/castia/observe/tracing.py`.

This rule is about keeping the underlying App Insights `type` clean. It is good
hygiene, but note it is **not** what makes the framework badge flicker — see next.

## Tool-loop shape

`Model.respond_with_tools(...)` has two layers of trace ownership:

- The official Foundry Responses/OpenAI instrumentor owns the `chat {model}` span
  and the `gen_ai.input.messages` / `gen_ai.output.messages` attributes. Server-side
  tools such as Foundry Toolbox MCP are executed inside that model request, so
  their HTTP/dependency shape is platform-owned.
- Castia owns local tool execution. Local function tools run inside an
  `execute_tool {name}` span. Castia also adds lightweight, non-payload events to
  the current turn span so human review can follow the logical phases even when
  the platform keeps the model call as one long span:
  `castia.model.request.started`, `castia.model.tool_calls.requested`,
  `castia.tool.call.started`, `castia.tool.call.completed`, and
  `castia.model.final_response.completed`.

These events carry counts, phase names, iteration numbers, tool names, and status
only. They do not duplicate prompt text, tool arguments, or tool output. Payload
content remains governed by the existing GenAI content-recording opt-in.

For server-side MCP/toolbox calls passed as raw Responses `mcp` specs, Castia
cannot safely reparent the platform's dependency spans under a separate local
`execute_tool` span: the tool execution happens inside the Responses service call
and is reported by the upstream instrumentors. Use this path when you want the
platform to own MCP execution.

When you need deterministic tool-call telemetry from Castia, build local tools
from the MCP schema with `toolbox_tools_from_mcp(...)` and pass those tools to
`Model.respond_with_tools(...)`. Castia then owns the `tools/call` request and
wraps each remote MCP invocation in the same `execute_tool {name}` span used for
local functions, with `gen_ai.tool.type = mcp` and the Responses call id when
available. That is the Castia-owned path for Monitor-countable toolbox calls.

For local MCP toolbox calls, Castia also creates an explicit outbound HTTP client
span around the JSON-RPC POST. The span is named from the request path, for
example `POST /api/projects/<project>/toolboxes/<toolbox>/mcp`, and is a child of
the active `execute_tool <name>` span. Castia injects that POST span's W3C
`traceparent` plus `leaf_customer_span_id` before sending the request, then
suppresses automatic HTTPX instrumentation inside the manual span so downstream
Foundry toolbox telemetry parents to the visible POST dependency instead of to a
hidden auto-instrumented HTTP span. The span carries marker attributes such as
`castia.toolbox.mcp` and `castia.telemetry.scope = "toolbox_mcp_http"` for
filtering; it deliberately avoids blanket `gen_ai.system` /
`gen_ai.provider.name` attributes so Azure Monitor keeps classifying it as an
HTTP dependency.

If an agent needs to bypass this Castia-owned POST span, construct local toolbox
tools with `toolbox_tools_from_mcp(..., trace_requests=False)` or pass
`trace_requests=False` to `ToolboxMcpClient`. The raw Responses `mcp` spec path
is unaffected: those toolbox calls are executed by the model service, not by the
Castia process, so Castia cannot wrap or reparent their outbound POSTs.

## Azure SDK metadata dependency noise

Azure Identity and Azure Monitor can emit standalone dependency spans such as
`GET /msi/token`, `GET /metadata/instance/compute`, and
`GET /AzMonSDKDynamicConfiguration`. They are platform authentication/exporter
probes, not Castia agent executions, and the Foundry portal can promote them into
one-line "traces" when they carry the same agent/project identity attributes as a
real run.

Castia keeps Azure SDK/HTTP instrumentation enabled so real authentication,
exporter, and abnormal-latency failures remain visible. It also installs an
identity processor that stamps Foundry agent/project identity only while Castia
is handling an actual agent invocation. Process-level probes emitted outside a
turn are left unstamped so they do not become standalone Foundry traces. As a
backstop, Castia also suppresses ordinary fast successful metadata probe spans
before export. Spans with explicit HTTP 4xx/5xx status, or unusually slow
metadata probes, remain visible for diagnostics.

## The badge that lies: portal query is non-deterministic

**Symptom.** In the Traces UI, the **kind** badge on framework and HTTP spans
(e.g. `agents.adapter.process`, `GET /msi/token`) randomly flips to
**default / Other** on runs that are already complete and stored. No redeploy, no
new run, no data change — just refreshing or reopening the same trace is enough to
change it. It flips back on a later read. Seen across agent versions (confirmed on
v13 op `6efde7c97049e6214d5aee83d1fc4305` and a v18 run `ad28e31a…`).

**Root cause — portal-side, not our telemetry.** The Traces detail query
`spanAppInsightsLLMCallBaseQueryOpt` (fired when a run is opened) builds two
projections of the *same* spans and unions them:

- `normal_spans`: `span_type = type` — the real exported type (`HTTP` / `InProc`)
- `gen_ai_spans`: `span_type = "default"` — a hardcoded literal, applied to
  **every** span with **no filter**

Then it collapses per span id with a non-deterministic aggregate:

```kusto
gen_ai_spans
| ...
| union normal_spans
| summarize spanType = any(span_type), ...
  by id, operationId = operation_Id, operationParentId = operation_ParentId
```

Each span id now has two rows with conflicting `span_type` — its real type and
`"default"` — and `any()` in KQL is explicitly non-deterministic. Every execution
arbitrarily returns one or the other, so the badge is a coin-flip on each read.

**Why it looked like "decay" or a per-version difference.** Nothing decayed and
nothing was version-specific. A span that "stayed correct for hours" was just
winning the `any()` toss on the reads we happened to look at; a span that "flipped
on its own" lost one. The captured detail query is byte-for-byte identical across
v13 and v18 except the `operation_Id` and version filter.

**None of our code is involved.** Our agent emits correct spans; App Insights
stores the correct `type` immutably. The flip is entirely in the portal's derived
query. Reported to the Foundry team; suggested fix is to filter `gen_ai_spans` to
spans with `gen_ai.operation.name` set (so a span contributes one `span_type`), or
to replace `any(span_type)` with a deterministic pick (`arg_max` / `coalesce` /
`max`) that prefers the real type.

## How to verify (ground truth)

Do **not** trust the UI badge for framework/HTTP spans — query App Insights
directly, where the type is immutable and correct:

```kusto
dependencies
| where operation_Id == "<your-operation-id>"
| project id, name, type, timestamp
```

- A framework span (e.g. `agents.adapter.process`) shows `type == "InProc"`.
- If `CASTIA_OTEL_TRACE_MSI_TOKEN=true` is set, an auth/MSI call (e.g.
  `GET /msi/token`) shows `type == "HTTP"`.

If those rows are correct but the UI badge reads **Other**, it is the portal
`any()` query, not the runtime and not our processor.

## Azure SDK metadata probe spans

Hosted and local agents can emit metadata dependency spans beneath model or tool
calls, or as standalone operations (`GET /msi/token`,
`GET /metadata/identity/oauth2/token`, `GET /metadata/instance/compute`, and
`GET /AzMonSDKDynamicConfiguration`). These spans come from platform
managed-identity, IMDS, and Azure Monitor SDK plumbing, not from Castia's agent
loop. They make the default trace tree noisy, so Castia suppresses ordinary fast
metadata probe spans by default before export. That includes fast local IMDS
probe statuses such as HTTP 504 when managed identity is unavailable; those are
credential-chain probing noise, not agent/model/tool work. Spans with explicit
exception evidence and unusually slow metadata calls remain visible because they
are useful auth/exporter and latency diagnostics.

Set `CASTIA_OTEL_TRACE_MSI_TOKEN=true` when debugging managed-identity
authentication, token-acquisition latency, IMDS probing, or Azure Monitor
exporter configuration. That opt-in restores these Azure SDK dependency spans for
the process.

## Prompty inner spans

Castia's Prompty integration records an aggregate `prompty turn_async` span by
default, plus the Castia `execute_tool ...` spans for local/toolbox functions.
The lower-level Prompty lifecycle spans (`prepare_async`, `render_async`,
`parse_async`, repeated model-wrapper `execute_async` / `responses.create`,
and `process_async`) are useful when debugging Prompty itself, but they add deep
nesting to ordinary agent trajectories.

The aggregate `prompty turn_async` span also carries a Castia-owned observed
timeline:

- `castia.turn.summary` gives a compact ordered path such as
  `turn_start -> tool:local_agent_fact -> tool:local_trace_marker -> turn_end`.
- `castia.turn.timeline` is JSON with the observed turn/tool events in order.
- `castia.turn.tool_call_count` counts local/toolbox tool executions.

Each `execute_tool ...` span carries `castia.step.index`,
`castia.step.kind=tool`, `castia.tool.status`, and `gen_ai.tool.name`. When
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true`, tool arguments and
outputs are also attached as `gen_ai.tool.arguments` and `gen_ai.tool.output`.

Set `CASTIA_PROMPTY_TRACE_INTERNAL=true` to restore those lower-level Prompty
pipeline spans for a process. Content on Prompty spans still follows the normal
`AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED=true` privacy gate.

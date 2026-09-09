# Trace labels in the Foundry Traces UI

A working reference for the span badges in the trace tree — **In Process**,
**HTTP**, **Invoke Agent**, **Chat** — and a warning about one badge that lies.
Written after a bug bash where we watched the framework/HTTP badges flip on their
own and spent a long time blaming our own code before finding the real cause: a
non-deterministic query in the Foundry Traces portal.

## Where the labels come from

Two different things feed the badges, and only one of them is under our control.

**1. Our own operation spans — stable, ours to set.** The spans we create in
`tracing.py` (`invoke_agent`, `execute_tool`) carry `gen_ai.operation.name`, a
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

This exported `type` is written once, is single-valued, and is immutable in App
Insights. `az monitor app-insights query` returns the correct `HTTP` / `InProc`
for these spans every time, hours or days later.

## The one rule: stamp identity, restyle nothing

`_AgentIdentitySpanProcessor.on_start` (in `observability.py`) runs on **every**
span on the provider. Its only job is to stamp the agent-identity attributes the
Foundry portal needs to associate the run with the agent:

- `gen_ai.agent.name`, `gen_ai.agent.version`, `gen_ai.agent.id`
- `microsoft.foundry.project.id`, `gen_ai.azure_ai_project.id`

Those keys are neutral — none is a mapping attribute the exporter keys off, so
they do not change any span's exported `type`. Keep it that way. **Do not** stamp
classification-affecting attributes (`gen_ai.operation.name`, `gen_ai.system`,
`http.*`) onto framework or client spans from a blanket processor; set operation
names only on spans we own, at creation, in `tracing.py`.

This rule is about keeping the underlying App Insights `type` clean. It is good
hygiene, but note it is **not** what makes the framework badge flicker — see next.

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
- An auth/MSI call (e.g. `GET /msi/token`) shows `type == "HTTP"`.

If those rows are correct but the UI badge reads **Other**, it is the portal
`any()` query, not the runtime and not our processor.

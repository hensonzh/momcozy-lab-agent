# Observability

API, Agent worker, and replay/eval processes write one-line JSON logs to stderr
using `LOG_LEVEL`.

## Operational events

| `metric_name` | One event represents | Allowed aggregation dimensions |
| --- | --- | --- |
| `agent_runtime_http_request` | one HTTP request | `method`, `route`, `status_code`, `outcome` |
| `agent_runtime_run` | one run processing attempt | `outcome`, `error_code` |
| `agent_runtime_tool` | one tool execution attempt | `tool_name`, `outcome`, `error_code` |
| `agent_runtime_model` | one model operation | `provider`, `model`, `agent_name`, `outcome`, `error_code` |
| `agent_runtime_context_preflight` | one Run history measurement | `provider`, `model`, `outcome` |
| `agent_runtime_model_request_budget` | one complete pre-model request measurement | `provider`, `model`, `outcome`, `error_code` |
| `agent_runtime_context_compaction` | one durable compaction attempt | `provider`, `model`, `outcome`, `error_code` |

Each event contains `duration_ms`; the log collector derives an operation
counter and latency histogram from these events. `request_id`, `trace_id`,
`run_id`, `thread_id`, `tool_call_id`, and `action_id` are search/correlation
fields only and must never become metric labels.

Agents SDK tracing is disabled in the execution adapter. Runtime lifecycle
hooks emit the existing privacy-safe `agent_runtime_model` operation records,
while the durable execution manifest and replay remain the diagnostic source
of truth. This avoids exporting Prompt, user content, or Tool payloads through
an independent tracing path.

Every provider model turn is preceded by
`agent_runtime_model_request_budget`. It counts the final materialized input and
the exact provider Tool schemas, then reserves the configured response budget.
An `outcome="exceeded"` record precedes durable compaction recovery or an
explicit `model_context_budget_exceeded` failure.

Deterministic safety escalations are persisted as versioned `safety.decision`
Run events containing only the bounded category, severity, rule ID, and policy
version. They do not copy the triggering user text. A matching Run completes
without a provider model or business Tool operation, so these durable events
are the operational source for safety-escalation monitoring.

`AGENT_MODEL_TIMEOUT_SECONDS` bounds every complete CozyMate model call,
including the entire streamed response and provider SDK retries. A deadline
breach emits `agent_runtime_model` with `outcome="timeout"` and
`error_code="model_provider_timeout"`; external cancellation remains an
interrupted execution and is not reclassified as a provider timeout. The
durable execution manifest records the timeout value and
`timeout_scope="per_model_call_wall_clock"`.

There is intentionally no public or process-local metrics endpoint. API and
workers run in separate processes, so an in-memory endpoint would be incomplete
and easy to expose accidentally. Production log collection is the shared,
service-controlled operations boundary.

The Agent worker refreshes a fixed role heartbeat key in Redis every
`WORKER_HEARTBEAT_INTERVAL_SECONDS`; the key expires after
`WORKER_HEARTBEAT_TTL_SECONDS`. With `WORKER_HEARTBEATS_REQUIRED=true`,
`GET /v1/health/ready` checks that role and returns 503 when it is missing or
reports a version different from the API's
`APP_VERSION`. Heartbeat keys contain only the Runtime version and no user,
request, or run data.

## Privacy

Runtime instrumentation uses fixed log messages, and the JSON formatter accepts
only an explicit operational-field allowlist. Instrumentation never passes
prompts, messages, tool arguments/results, URLs, files, business records, or
actor identifiers to that logger. Exception output contains only the exception
type and stack frame locations; exception messages and source lines are
omitted. HTTP logs use the declared route template, never the raw path or query
string.

Incoming `X-Request-ID` and `X-Trace-ID` values are accepted only when they use
the bounded correlation-ID format; otherwise Runtime creates a new request ID.
Both IDs are returned as response headers, and new runs persist them for worker
correlation.

## Minimum alerts

- HTTP 5xx rate and p95/p99 `agent_runtime_http_request` latency.
- `agent_runtime_run{outcome="failed"}` and interrupted-run events.
- Tool timeout/error rate by the bounded tool catalog.
- Model timeout/error rate by configured provider/model.
- Context-compaction retry/dead-letter rate, queued-job age, lease-renewal
  failures, and Thread Context Heads stuck in `compacting`. A `blocked` head
  rejects new Runs until an audited supersede recovery succeeds.
- Complete-request budget exceedances and durable `safety.decision`
  escalations by their bounded category/severity.
- `http.request.unhandled` events, grouped by route and exception type.
- Missing worker heartbeat or repeated `worker.heartbeat.failed` events.

Start incident investigation with `request_id` or `run_id`, then use the
persisted run replay and audit records. Do not copy message or tool payloads into
logs while investigating.

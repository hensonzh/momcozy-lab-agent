# Runtime v1 contract

Runtime v1 (`momcozy-agent-v1`) makes authorization, side effects, provider
capabilities, and streamed-text recovery explicit runtime contracts. These are
release invariants, not prompt conventions.

## Authorization boundary

- Product Backend remains the token issuer. New and refreshed user access
  tokens include `agent:run` plus the bounded Product capability permissions.
  Its OpenAPI root publishes the sorted
  `x-momcozy-runtime-token-permissions` contract; Runtime's Product Backend contract
  gate rejects a release missing any permission required by the Tool catalog.
- Every public Runtime endpoint requires `agent:run` in addition to a valid
  Runtime-audience JWT.
- Run creation stores the canonical
  `agent.authorization_context.v1` snapshot (owner, session/token identity,
  roles, permissions, and token timestamps). Workers reconstruct that exact
  principal; they never invent a privileged worker user.
- The model receives only Tool contracts whose required permissions are a
  subset of the Run snapshot. The Tool executor repeats the permission and
  owner check before validation or handler invocation and persists a
  `tool.blocked` event on denial.
- Action proposal checks the frozen Run snapshot. Confirmation and rejection
  additionally check the newly authenticated principal, so a permission
  removed after proposal cannot authorize the write.

The snapshot intentionally freezes admission authority for deterministic Run
replay. It is not a credential and is never forwarded to Product Backend.
Interactive confirmation is a new request and therefore uses current token
authority.

## ToolContract v1 and ActionPolicy v1

完整的当前方案、Tool/Action 清单、限制和变更维护协议统一记录在
[tools.md](tools.md)；本节只定义 Runtime v1 不可绕过的发布不变量。

Every Tool declares its domain, operation class, required permissions, actor
owner scope, privacy-safe argument/output fields, retry policy, schemas,
timeout, and any Action types it can propose. Every Tool record carries
`schema_version=agent.tool_contract.v1`; every Action policy record carries
`schema_version=agent.action_policy.v1`. Both fail closed on an unsupported
version.

The allowed combinations are fixed:

| Tool operation | Action binding | Retry policy |
| --- | --- | --- |
| `read` | none | `safe_read` |
| `action_proposal` | one or more | `idempotent_write` |
| `runtime_internal` | none | `none` |

Every Action policy declares required permissions, side-effect level,
confirmation/blocking behavior, idempotency, audit, and retryable failure
codes. A medium/high effect without confirmation needs a documented exemption.
Composition fails when an Action has no Tool or policy, is bound more than
once, lacks an applicator, or needs permissions not covered by its Tool.

All cross-cutting Runtime contract version constants have one code source:
`app/agent_runtime/runtime_metadata.py`. Its deterministic metadata snapshot is
copied into each model execution manifest and the generated
`docs/runtime-contract-catalog.generated.json` catalog. The catalog contains
the sorted Tool contracts and Action policies, independent section hashes, and
a top-level content hash.

This release is the v1 baseline for every cross-cutting contract enumerated in
that module. Backward-compatible implementation changes keep the affected
contract at v1.
An incompatible change increments only that contract, adds an explicit data,
replay, and rollout policy, updates the consistency test, and regenerates the
catalog. New literals for those contracts must not be introduced outside
`runtime_metadata.py`, except in negative tests and generated or external
contract artifacts. Capability-local payload and reference schemas remain
owned by their capability modules.

Regenerate or validate the catalog with:

```bash
python scripts/export_runtime_contract_catalog.py
python scripts/export_runtime_contract_catalog.py --check
```

`agent_model_execution.v1` records the Tool contract version, Action policy
version, catalog schema versions, and the exact Tool, Action, and aggregate
catalog hashes used by the worker. Worker composition rejects a catalog whose
Tool set differs from the actual registry.

Operational logs and ledger summaries use explicit allowlists. Undeclared Tool
arguments are stored as `<redacted>`; raw arguments, Tool outputs, tokens, and
user content are not log dimensions.

## Double-cursor streaming

The stream has two independent positions:

- `after_sequence` is the PostgreSQL durable-event sequence.
- `after_transient_cursor` is the Redis Stream cursor for low-latency text
  deltas.

Each `append-only.v1` delta includes `message_stream_id`, zero-based
`segment_index`, cumulative UTF-8 byte count, and cumulative SHA-256. The
durable `message.completed` event includes the same stream identity, total
segment count, final UTF-8 byte count, final SHA-256, and canonical full text.
Flutter persists both cursors, orders or temporarily buffers indexed segments,
deduplicates replays, checks every prefix, and treats the durable final message
as canonical reconciliation after Redis loss or expiry.

Redis remains disposable: transient entries expire after ten minutes, while a
completed response and all terminal state remain recoverable from PostgreSQL.

## Model provider boundary

Runtime orchestration consumes the non-secret `agent.model_provider.v1`
`ModelProviderProfile`; the worker supports OpenAI Responses and Azure OpenAI
Responses v1 through one `ProviderRuntimeBundle`. It records provider/API,
capabilities, request policy, model/deployment metadata, SDK versions, and
request/context hashes in `agent_model_execution.v1` (aggregated by
`agent_run_execution_manifest.v1`). Credentials and tokens are never recorded.
A provider is rejected unless it supports function tools, streaming,
structured outputs, and tool search.

The same canonical provider identity is pinned to every durable Context job and
copied into its checkpoint. Context idempotency and worker compatibility also
bind the token-counter identity, preventing queued work from being processed
after an undeclared provider, deployment, or counter change. Provider failures
share one normalized error contract across the main Agent call and Context
provider calls; bounded retry hints survive in failed Runs and `run.failed`
events.

`AGENT_MODEL_PROVIDER` is either `openai_responses` or
`azure_openai_responses`. OpenAI may use an explicitly attested compatible
gateway. Azure uses a `/openai/v1` endpoint, the deployment name as the request
model, and either Entra or API-key authentication; its profile also pins the
model family/version, region, and deployment type. OpenAI uses the exact
Responses input-token counter. Azure currently uses a named, conservative local
estimate plus multimodal reserves because its v1 endpoint lacks that operation.
The full contract and rollout gate are in
[model-providers.md](model-providers.md).

## Deterministic release harness

Run the Runtime v1 contract harness locally or in CI:

```bash
python scripts/run_runtime_v1_harness.py \
  --junit reports/runtime-v1-harness.xml
```

The pinned suite exercises authorization snapshot parsing, Tool catalog
filtering and executor denial, Action confirmation re-authorization,
privacy-safe persistence, double-cursor/indexed streaming, provider contracts,
SDK integration, durable loop recovery, and replay. The existing behavior
catalog remains a separate release gate for end-to-end response and safety
quality.

## Migration and rollout

The current Alembic revision is the fresh v1 baseline, not an in-place upgrade
from any pre-v1 experimental schema. It adds a non-null Run authorization
snapshot and does not translate pre-v1 Runs, Tool calls, Actions, or stream
state.

1. Back up and identify the Product and Runtime databases independently.
2. Deploy Product Backend first so newly issued and refreshed access tokens
   contain Runtime permissions.
3. Quiesce Runtime admission and drain or explicitly abandon existing Runs.
4. Drop and recreate **only the resettable Agent Runtime database**, then run
   `alembic upgrade head`. Never reset or point at the Product Backend database.
5. Deploy the Runtime API and worker from the same image digest and verify
   migration head, readiness, Product Backend OpenAPI compatibility, the Runtime v1
   harness, and database-backed behavior evals.
6. Publish the Flutter App only after both backend lanes pass. Existing App
   builds remain compatible because the transient cursor query is optional;
   new builds gain lossless reconnect behavior.

Access tokens issued before the Product deployment may lack `agent:run` and
receive `403 permission_denied` until refresh or sign-in. Do not weaken Runtime
authorization as a compatibility workaround.

Rollback is image-only when the candidate supports the v1 baseline. Pre-v1
images must not be pointed at the v1 database; roll forward or recreate the
resettable Runtime database instead.

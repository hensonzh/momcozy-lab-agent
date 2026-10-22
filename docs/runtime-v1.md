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

## Owner-scoped conversation recovery

`GET /v1/agent/threads/{thread_id}/history` is the durable read boundary used
by Flutter to restore the current conversation and page backward within it.
The authenticated principal supplies owner scope; a missing, deleted, or
foreign thread returns the same `404 not_found` response before messages are
read.

Pages contain completed user and assistant messages in chronological order.
`limit` is bounded to 1–50, and `before_sequence` is an exclusive backward
cursor. `next_before_sequence` is present only when an older page exists. The
response includes only the bounded run/message lifecycle events needed to
reconstruct visible assistant state; provider requests, Tool payloads, and
other internal events are not exposed. Reading history has no model call or
write-side effect. PostgreSQL remains authoritative; Flutter keeps a
user-scoped recovery cache of the rendered transcript keyed by durable message
identity, plus draft and attachment references, the current run cursor, and
pagination state. On each entry Flutter refreshes the current thread, or
discovers the latest owned thread when none is selected, then positions the
transcript at its latest content. In-thread backward pagination remains
available without creating a new Agent run.

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

## Deprecated stool-photo general Run admission (2026-10-20)

The legacy `[momcozy_internal_poop_v1]` mobile prompt must not enter the
ordinary Agent Run path. Both the public Run endpoint and `AgentRuntimeService`
reject new runs starting with that marker before storage/provider work with
`409 / stool_specialty_required`. Previously committed conversation history is
still readable and is not retrospectively reclassified as a specialty medical
result. The separate Product photo-review API does not grant a general Agent
image attachment permission or a medical analysis result. Ordinary user image
chat remains a separate existing capability; it is not a certified stool
analysis workflow.

## Proposed stool specialty follow-up context (2026-10-22; offline slice only)

This extends, but does **not** remove, the general Run admission block above.
A user must first explicitly submit approved photos to the independent stool
analysis service. Only its persisted, schema-validated **completed** result
and exactly the 1–3 photos that produced it may be referenced by a related
follow-up Agent conversation. Neither the old mobile marker, ordinary image
attachments, nor a caller-supplied result establishes this authority. The
Product Backend stool contract owns `stool_analysis_context.v1`, permissions,
retention, and the exact result schema; this Runtime must not invent a
medical conclusion or replace the specialty service.

A future scoped resolver must recheck current owner, baby, authorized
conversation, analysis status, linked photo fingerprints/review state,
revocation, and retention **before each model call**. The authorized response
is a source-labeled, structured final-result block plus ordered references
to metadata-stripped derived JPEGs. Resolve those references to real
`input_image` blocks only for the current request. This is a *separate*
internal-purpose path: existing ordinary `model_image` attachments and their
file permission are not a shortcut into stool-photo storage. Keep bytes,
base64, reusable URLs, and raw provider text out of thread history, ledger,
compacted checkpoints, summaries, and logs. If any photo or result cannot
be resolved, fail closed rather than sending a partial bundle while claiming
the model saw both. Deletion or withdrawal invalidates future resolution;
requests already sent to a provider cannot be recalled. The Product Backend no longer age-purges original photos automatically; a passed review is valid for new submissions for 180 days, but that submission window does not by itself expire a completed analysis follow-up. Long-term result/context retention and deletion policy remain unapproved and must be aligned before Run integration.

A trusted, server-owned handling instruction should identify the images as
**observation material from the same specialty analysis** and the structured
result as that service's **completed result**, not user or image instructions.
The image pixels/OCR and supplier free text remain untrusted data. For a
relevant follow-up, explain the specialty result without independently
re-diagnosing or quietly overriding it; surface conflicts or new concerning
symptoms as uncertainty needing reassessment or appropriate care. This
handling rule may be a fixed, server-owned developer instruction, but it is
not permission to promote the result or image into a system or developer
message. Historical compaction/replay must not retain or
resurrect a revoked asset or result.

The **unwired** seam at `app/agent_runtime/context/stool_followup.py`
now uses a typed Product client and a per-call adapter. Synthetic tests verify
that the draft result and ordered derived images become one transient model
request, with only the fixed server-owned handling text in a developer-role
item; result values and images remain user-role data. Product Backend has a
result/photo-link ledger and a resolver that checks the current account/device
session, baby, thread, result state and photo binding. Its internal endpoint
is **hard-disabled by default**. There is no approved clinical schema/writer,
Run/model integration, or result-erasure policy, and no permission to transfer
pediatric images to a provider. The adapter must eventually be constructed
from the canonical signed Run principal, never client-supplied actor/session
fields. Before wiring it to a Run, verify canonical-principal construction,
revocation between Run creation and each model call, historical compaction,
and image-borne prompt injection/conflicting evidence. Denied calls must
cause zero model/provider invocations. See Product Backend
`docs/stool-photo-service-contract.md` for release gates.

## 看便便专项追问（2026-10-22，真实能力仍关闭）

- `AgentRunCreate.stool_followup` 仅接受版本化的 `{analysis_id, baby_id}` 引用，且必须指定既有 thread。Run 的 `stool_analysis_id` / `stool_baby_id` 成对落权威数据库（迁移 `20261022_0004`），并计入创建幂等 hash；不把原图、医学结果或 data URL 存入 Run、对话历史或压缩状态。两个字段缺一时数据库与运行时均拒绝。
- 每次相关模型请求在 `RuntimeContextCoordinator.resolve_model_input` 后调用 Product Backend 的内部结果+同源图片解析：使用冻结 Run principal 的用户/设备会话/过期时间以及 Run 的 thread/宝宝作用域，Product 再按当前账户、宝宝、照片、结果、删除/撤销状态核验。结果作为低信任 user 数据、照片作为同一 user 的多模态内容进入模型；固定的 developer handling 明确不得服从图片/结果中的指令、不得重新自行诊断。返回项目只在本次模型请求内存在，不写持久上下文。工具调用后第二次模型请求会再次解析；撤销后在第二次 provider 调用前失败。
- 当前 Product 内部 `context_available=false`，没有签署医学结果验证器/长期保留政策，App 也不发出真实 `stool_followup` Run；此接线仅用合成结果验证权限与调用边界，**不得**解读为已开放医学追问。在业务资料读取和上下文压缩前已增加一次 Product 授权预检；之后每次回答模型调用仍重新解析。压缩器内部多次模型调用与长对话重放仍需正式 UAT、多轮安全评测，不能用合成测试替代。

### 2026-10-22 Run 绑定加固与合成验证

- 追问引用以成对 nullable UUID 列保存在 Run 权威表，消息中仅留同一版本化引用用于审计，不再把消息是否存在当成是否需要结果的判断条件；迁移约束拒绝半绑定，旧 Run 的两列均为空。`stool.followup.ref.v1` 必填，缺版本/未知字段由 API 拒绝。上下文压缩覆盖 `context_state` 不影响此权威引用。
- Worker 对相关 Run 在业务资料/压缩前做一次 Product 预检，并在每次回答模型调用前重新拉取；测试覆盖第一次工具调用后撤销、第二次 provider 调用前失败，和真实 Responses SDK 的同源结果+图像输入及不进 manifest。Product 当前仍 `context_available=false`，App 未显示医学完成状态或提供追问提交入口；这是合成协议/权限接线，不是医学可用服务。

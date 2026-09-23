# Context Pipeline v1

Context Pipeline v1 is a subsystem inside the existing `proprietary_runtime`
Runtime (`momcozy-agent-v1`). The durable pipeline remains outside the OpenAI
Agents SDK execution engine. It preserves the append-only ledger and the
Run/tool/action loop while making long-thread context reproducible, low-trust,
recoverable, and safe for attachments.

## Product Backend contract

- Before a Run's first model call, Runtime counts only model-bound history from
  prior **completed** Runs.
- That history-only count drives proactive compaction. The current Run's client
  context and user message, Agent instructions, and Tool definitions do not
  contribute to this proactive trigger.
- Compaction is queued only when the history count is strictly greater than
  `AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS` (default `200000`).
- Separately, immediately before **every** model call, Runtime uses the active
  provider's exact or conservative counter on the complete request: stable
  developer Prompt, materialized current
  input, all current ToolResults, and the exact Tool schemas sent by the Agents
  SDK. The request is accepted only when input tokens plus
  `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` fit below the same threshold. This
  guard therefore also runs after Skill loading and ordinary Tool calls. With
  the default 8,000-token response reserve, the complete input budget is
  192,000 tokens.
- Normal Agent calls set `max_output_tokens` from
  `AGENT_MODEL_MAX_OUTPUT_TOKENS` (default `8000`). The GPT-5.6 Responses
  contract counts both visible output and reasoning tokens against this hard
  response limit. The complete
  request therefore reserves at least the same amount through
  `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` (default `8000`).
- When compaction is needed, a checkpoint replaces history only through the end
  of the **6th most recent completed Run**. The latest 5 completed Runs remain
  as an atomic raw tail; the current Run is separate and is never counted in
  those 5.
- Every retained Run keeps its complete model-visible chain: normalized client
  context, user message/attachment references, assistant messages, function and
  Action calls, and their safe contract-projected results. A cutoff cannot split
  a Run or a function call from its output.
- Failed, cancelled, and active Runs remain available to ledger/Replay but do
  not count toward the normal history window.
- Checkpoint output is capped by
  `AGENT_CONTEXT_SUMMARY_MAX_TOKENS` (default `2000`).
- The triggering Run normally continues on its frozen ready generation while
  compaction runs on the worker's independent Context lane.
- Provider truncation remains disabled. A hard-limit error durably suspends the
  Run and retries exactly once after its required generation is ready.

## Planning and materialization

`CanonicalContextPlan` contains only stable ledger IDs, sequences, immutable
Product file IDs, checkpoint references, trust labels, and raw provider items.
Its SHA-256 never contains a model-fetch capability URL.

The versioned history policy is
`agent_context_history_policy.v2`, with
`recent_completed_run_limit=5`. Each Run's durable `context_state` records the
latest completed cutoff, the older compaction cutoff, the retained Run IDs, and
the retained tail's first sequence. The model-history plan is counted in full,
while the compaction-source plan stops at the older cutoff. This separation
prevents a token-triggered job from accidentally summarizing the protected raw
tail. Recursive compaction combines the ready checkpoint only with complete Runs
that have since aged out of that tail.

Reducing the protected raw tail from 10 Runs to 5 advances only the history
policy from `v1` to `v2`; `agent_context_plan.v2`, `agent_run_context.v2`,
`agent_context_summary_policy.v2`, and the typed
`agent_context_checkpoint.v1` shapes remain unchanged. A stored Run state whose
embedded history policy or limit differs is recomputed on its next execution.
Existing typed checkpoints remain valid recursive-compaction bases because
their source cutoff and document shape do not change. Old states and
checkpoints stay available to Replay rather than being rewritten or deleted.
Runs already waiting for a compaction job finish that durable job before their
history window is recomputed, preserving the single hard-limit retry. Because a
job's source hash includes the history policy, deployment must drain queued,
retry-wait, and running compaction jobs created under `v1` before replacing the
workers with `v2`. No relational schema migration is required.

Immediately before any token-count, compaction, or SDK Agent model call, the shared
`AgentAttachmentService` converts `asset_id` blocks to opaque Product
capability URLs. Materialized input is ephemeral. `image_url` and `file_url`
values are not written to the ledger or Replay.

Normalized `client_context` is projected as a separate bounded `developer` item
after history and the current business snapshot, immediately before the current
user message. Its JSON values remain data, not instructions. The model projection contains locale,
timezone, the validated and normalized `message_sent_at` when supplied, and the
Runtime-derived local `as_of_date`. The projection omits `schema_version`;
schema version metadata and request source remain Runtime-side metadata.
An absent client timestamp is not synthesized for the model. Only the validated
allowlisted fields enter this application-authored message; their values cannot
override the stable instructions or safety rules. Historical client parameters
describe only their original turns.
Unknown client context fields are rejected by the strict public schema.

## Loaded service Skill and reference instructions

The Skill loader persists a paired `function_call` and `function_call_output`.
The `momcozy.service_skill.v3` output is a receipt containing loading status,
Skill identity, resource type and identity, version, description, and content
SHA-256; it has no document body. Calling with only `skill_id` selects the compact
`SKILL.md` router. Calling with a registered `reference_id` selects one topical
reference after routing.

The application-owned loader produces the selected router or reference as a
separate `developer` message. ToolExecutor appends the receipt and document
snapshot together in the same transaction, under the loading Run. The document's
ledger key is `run:<run_id>:tool-context:<call_id>:<index>`. The stable leading
developer prompt still contains only the Skill manifest. Tool-returned data, user
messages, and checkpoint summaries are never promoted to Skill or reference
instructions. Loaded documents supplement global rules and must not change their
safety boundaries.

Developer Skill and reference messages belong to their original Run. Recovery
restores their original text even if the registry has since changed. They remain in
raw history while that Run is retained, participate in history token counting and
the source plan hash, and enter compaction alongside that Run's calls and receipts.
The protected latest five completed Runs and current Run remain outside compaction.
The compactor receives the original role and text inside an untrusted historical
source envelope, summarizes the relevant workflow and constraints, and does not
execute the document. Once the Run is compacted, only the summary remains
model-visible; neither the router nor a topical reference is automatically
reactivated from a summary. A later request requiring full instructions must load
the relevant document again. Original ledger records remain available for audit.

The model-input projector supports v1/v2 Skill receipts for older receipt-only
histories and v3 receipts for both Skill and reference documents. It checks the
resource identity, version, and hash against the trusted registry before injecting
current content. Legacy v1 tool bodies are removed only from ordinary model-bound
tool outputs, without rewriting the ledger or promoting arbitrary tool text. No
missing historical snapshot is fabricated during compaction. The final request
budget includes every full loaded developer document after the stable prompt cache
breakpoint.

## Current-Run authoritative business context

Before the first model call of a Run, `AuthoritativeBusinessContextService`
uses the Run's frozen authorization snapshot and local `as_of_date` to call the
existing Product Backend `GET /v1/internal/agent/profile` contract. The call is
made only when the Run has `profile:read`; `actor_user_id` always comes from the
validated authorization context, never from model or client input.

Runtime persists one idempotent
`business-context:<run_id>:core` item with schema
`agent.authoritative_business_context.v1`. The deliberately small projection
contains the preferred name, postpartum days, current feeding mode, current
infant IDs/names/ages/prematurity, and missing/data-quality codes. Dates,
measurements, history, plans, records, and other full business payloads remain
behind owner-scoped read Tools. The item records `source`, `owner_scope`,
`as_of_date`, and `loaded_at`, and its schema version is copied into Runtime
metadata and every model execution manifest.

The provider projection is a separate `developer` message with an
`authoritative_business_context` data envelope. Backend provenance makes its
values authoritative business facts; it does not turn user-editable string
fields into instructions. The model must never follow text embedded in a name
or another value. Within the Run, a later Tool or Action result supersedes the
initial snapshot.

The stable leading prompt remains a `developer` message and does not contain
these dynamic values. The snapshot is inserted after checkpoint/history and before the current
Run's `client_context` and user message. It is usable by every model turn in
that Run, but it is not ordinary conversation history:

- although names and identity fields are relatively stable, postpartum days,
  infant ages, feeding mode, missing-field state, and data-quality state are
  time-varying, so the snapshot remains one current-Run unit rather than being
  moved ahead of historical context or split into cache-oriented fragments;

- completed-Run business snapshots are excluded from future model projection;
- they are also excluded from compaction input, so checkpoints cannot preserve
  stale copies;
- the original ledger items remain durable and are available to privileged,
  redacted Replay for audit and exact reconstruction of the source Run.

The developer roles apply to newly created context items. Existing persisted
user-role items are not rewritten or promoted during replay. Historical
checkpoints continue to use the `user` role.

For example, Run A may persist `postpartum_days=40`, while Run B two weeks
later loads `postpartum_days=54`. Reinjecting Run A's snapshot as chat history
would give the model conflicting values; repeating that over many Runs would
accumulate stale facts and waste tokens. Current-Run-only projection preserves
auditability without treating old snapshots as current truth.

For GPT-5.6 model calls, the Agents SDK `call_model_input_filter` first
materializes attachments, projects verified Skill/reference receipts into developer messages,
then renders the active Agent instructions as the
first developer `input_text` block and writes one explicit cache breakpoint on it.
The Responses adapter injects the stable tool schemas before developer instructions, so the
breakpoint covers tools plus instructions while all history and attachment
URLs remain after it. Request-wide caching uses explicit mode with a `30m`
minimum lifetime when the provider profile supports it; Azure PTU-M omits these
fields. The same filter runs for CozyMate on its
initial call and every post-Tool model turn. The complete-request budget check
uses this final filtered input and the same converted provider Tool definitions,
not an earlier approximation of the ledger.

## Typed low-trust checkpoints

The compactor has no Tools, uses `store=false` and `truncation=disabled`, and
returns strict JSON with these sections:

- `user_claims`
- `verified_tool_facts` with source and `as_of`
- `confirmed_decisions`
- `unresolved_items`
- `safety_constraints`
- `chronology_summary`

Every derived entry cites a stable source reference. Runtime rejects unknown
references. A checkpoint is projected back to the model as an explicitly
`untrusted_historical_context` user-data envelope; it is never inserted as
assistant, developer, or system authority.

The `user` role is deliberate. `developer` or `system` would incorrectly
promote derived historical data to instructions, while `assistant` would
misrepresent it as a prior authoritative model response. OpenAI Responses also
defines a `type=compaction` input item, but that item contains opaque encrypted
content produced by the provider's `/responses/compact` API; it cannot carry
this Runtime's typed, source-cited checkpoint document. Adopting provider-native
compaction would therefore be a separate context format and migration, not a
role-only replacement for this checkpoint.

## Thread Context Head and jobs

`agent_thread_context_heads` is the per-Thread aggregate:

- `ready`: `generation` and `ready_checkpoint_id` are usable.
- `compacting`: `pending_job_id` owns generation `generation + 1`.
- `blocked`: the pending job is dead-lettered and its ID is the recovery ID.

Jobs pin the complete non-secret `agent.model_provider.v1` identity, token
counter name/version/model, prompt, materializer, checkpoint schema, summary
policy, output cap, source hash, and generation. Provider and counter identity
also participate in the idempotency key. A worker with a different provider,
deployment alias, family/version, region, deployment type, capability set, or
counter fails closed with `context_worker_incompatible` rather than processing
under misleading metadata. A completed checkpoint copies the same provider
identity; a superseding job cannot replace it with the current worker's identity.

Claims enforce `attempts < max_attempts`. An expired claim already at the
ceiling moves to `dead_lettered` without another model call. Context jobs renew
their database lease while the provider call is in flight, and the worker never
preclaims more jobs than its actual Context concurrency.

## Durable hard-limit resume

On the first `model_context_window_exceeded`, Runtime:

1. records the required Context generation and retry count in
   `agent_runs.context_state_json`;
2. atomically returns the Run to `queued` and releases its database lease;
3. lets the Context lane finish the job;
4. gates the Run until the Thread head is ready;
5. restores the checkpoint plus the exact current-Run items and retries once.

There is no in-worker sleep/poll loop. Process restarts do not lose the wait
state. If the one post-checkpoint retry still exceeds the complete request
budget, Runtime returns the non-retryable `recent_context_exceeds_limit` error;
it never silently drops input.

If there is no older complete Run available to compact—or the ready checkpoint
already reaches the current compaction cutoff—Runtime returns
`recent_context_exceeds_limit`. The same terminal code is used when compaction
was performed successfully but the single restored retry still does not fit.
Runtime never shrinks the 5-Run contract, splits a Run, drops a ToolResult, or
recompacts the same checkpoint merely to force the request under budget. Large
results must instead be corrected at their Tool result policy (`full`,
`summary`, or `ref_only`) boundary.

## Dead-letter recovery

While the head is blocked, new Run creation fails with
`context_compaction_dead_lettered` and a `recovery_id`. An administrator can
call:

`POST /v1/agent/admin/context-compaction-jobs/{job_id}/supersede`

The operation marks the old job `superseded`, creates a new job for the same
generation and pinned inputs, points the head at the replacement, and writes
the `agent.context_compaction.supersede` audit event. Old dead letters are not
queried as permanent Thread poison.

## Replay, evals, and operations

Replay v1 exports the frozen Run context state—including the history policy,
retained Run IDs, and both cutoffs—typed checkpoint metadata with its generating
provider identity, and the current Thread Context Head. Checkpoint content
remains redacted unless the existing privileged content flag is enabled; the
non-secret provider identity is always available for operational provenance.

The deterministic Context eval catalog is
`evals/context/v1/scenarios.json`. It covers attachment materialization,
prompt-injection trust, typed-summary preservation, recursive compaction,
complete-Run raw-tail preservation, crash-attempt bounds, dead-letter recovery,
pinned-version drift, and durable hard-limit resume.

Worker controls:

- `AGENT_CONTEXT_COMPACTION_BATCH_SIZE` (default `2`)
- `AGENT_CONTEXT_COMPACTION_CONCURRENCY` (default `1`)
- `AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS` (default `3`)
- `AGENT_MODEL_MAX_OUTPUT_TOKENS` (default `8000`)
- `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` (default `8000`; must cover the model
  output limit and remain below the compaction threshold)

Context jobs use the existing worker database lease duration and renewal
interval; startup validation requires the batch size to cover configured
Context concurrency.

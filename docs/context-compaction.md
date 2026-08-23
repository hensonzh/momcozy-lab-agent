# Context Pipeline v1

Context Pipeline v1 is a subsystem inside the existing `proprietary_runtime`
Runtime (`momcozy-agent-v1`). The durable pipeline remains outside the OpenAI
Agents SDK execution engine. It preserves the append-only ledger and the
Run/tool/action loop while making long-thread context reproducible, low-trust,
recoverable, and safe for attachments.

## Product contract

- Before a Run's first model call, Runtime counts only model-bound history from
  prior **completed** Runs.
- That history-only count drives proactive compaction. The current Run's client
  context and user message, Agent instructions, and Tool definitions do not
  contribute to this proactive trigger.
- Compaction is queued only when the history count is strictly greater than
  `AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS`.
- Separately, immediately before **every** model call, Runtime asks the provider
  to count the complete request: stable developer Prompt, materialized current
  input, all current ToolResults, and the exact Tool schemas sent by the Agents
  SDK. The request is accepted only when input tokens plus
  `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` fit below the same threshold. This
  guard therefore also runs after Skill loading and ordinary Tool calls.
- When compaction is needed, a checkpoint replaces history only through the end
  of the **11th most recent completed Run**. The latest 10 completed Runs remain
  as an atomic raw tail; the current Run is separate and is never counted in
  those 10.
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
`agent_context_history_policy.v1`, with
`recent_completed_run_limit=10`. Each Run's durable `context_state` records the
latest completed cutoff, the older compaction cutoff, the retained Run IDs, and
the retained tail's first sequence. The model-history plan is counted in full,
while the compaction-source plan stops at the older cutoff. This separation
prevents a token-triggered job from accidentally summarizing the protected raw
tail. Recursive compaction combines the ready checkpoint only with complete Runs
that have since aged out of that tail.

This incompatible planning change advances `agent_context_plan` and
`agent_run_context` to `v2`, and the compaction summary policy to `v2`; the
typed checkpoint document remains `agent_context_checkpoint.v1` because its
shape did not change. During rollout, workers must first drain all queued,
retry-wait, and running jobs pinned to the old summary policy. A stored v1 Run
context is recomputed on its next execution, and a ready checkpoint produced by
the old policy is ignored for model projection; the append-only ledger remains
the source for rebuilding a safe v2 checkpoint. Old states and checkpoints stay
available to Replay rather than being rewritten or deleted. No relational
schema migration is required.

Immediately before any token-count, compaction, or SDK Agent model call, the shared
`AgentAttachmentService` converts `asset_id` blocks to opaque Product
capability URLs. Materialized input is ephemeral. `image_url` and `file_url`
values are not written to the ledger or Replay.

Normalized `client_context` is projected as a bounded `user` item with an
explicit untrusted-data envelope. The model projection contains only locale,
timezone, and the Runtime-derived local `as_of_date`; request source and raw
send time remain Runtime-side metadata. Client data cannot gain
developer/system authority merely because it was supplied by the client.
The retired `hospital_bag_cart` field is absent from the public schema and is
never persisted or projected to the model. Runtime only strips a bounded copy
of that field at ingress so already-installed legacy mobile clients continue to
create Runs during the retirement window.

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

The provider projection remains a `user` message with an
`authoritative_business_context` data envelope. Backend provenance makes its
values authoritative business facts; it does not turn user-editable string
fields into instructions. The model must never follow text embedded in a name
or another value. Within the Run, a later Tool or Action result supersedes the
initial snapshot.

The snapshot is inserted after checkpoint/history and before the current
Run's `client_context` and user message. It is usable by every model turn in
that Run, but it is not ordinary conversation history:

- completed-Run business snapshots are excluded from future model projection;
- they are also excluded from compaction input, so checkpoints cannot preserve
  stale copies;
- the original ledger items remain durable and are available to privileged,
  redacted Replay for audit and exact reconstruction of the source Run.

For example, Run A may persist `postpartum_days=40`, while Run B two weeks
later loads `postpartum_days=54`. Reinjecting Run A's snapshot as chat history
would give the model conflicting values; repeating that over many Runs would
accumulate stale facts and waste tokens. Current-Run-only projection preserves
auditability without treating old snapshots as current truth.

For GPT-5.6 model calls, the Agents SDK `call_model_input_filter` first
materializes attachments, then renders the active Agent instructions as the
first developer `input_text` block and writes one explicit cache breakpoint on it.
OpenAI injects the stable tool schemas before developer instructions, so the
breakpoint covers tools plus instructions while all history and attachment
URLs remain after it. Request-wide caching uses explicit mode with a `30m`
minimum lifetime. The same filter runs for CozyMate on its
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

Jobs pin the model, token counter, prompt, materializer, checkpoint schema,
summary policy, output cap, source hash, and generation. A worker with different
versions fails closed with `context_worker_incompatible` rather than processing
under misleading metadata.

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
Runtime never shrinks the 10-Run contract, splits a Run, drops a ToolResult, or
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
retained Run IDs, and both cutoffs—typed checkpoint metadata, and the current
Thread Context Head. Checkpoint content remains redacted unless the existing
privileged content flag is enabled.

The deterministic Context eval catalog is
`evals/context/v1/scenarios.json`. It covers attachment materialization,
prompt-injection trust, typed-summary preservation, recursive compaction,
complete-Run raw-tail preservation, crash-attempt bounds, dead-letter recovery,
pinned-version drift, and durable hard-limit resume.

Worker controls:

- `AGENT_CONTEXT_COMPACTION_BATCH_SIZE` (default `2`)
- `AGENT_CONTEXT_COMPACTION_CONCURRENCY` (default `1`)
- `AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS` (default `3`)
- `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` (default `8000`; must be below the
  compaction threshold)

Context jobs use the existing worker database lease duration and renewal
interval; startup validation requires the batch size to cover configured
Context concurrency.

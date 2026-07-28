# Context Pipeline v2

Context Pipeline v2 is a subsystem inside the existing `legacy_adapter`
Runtime. It preserves the append-only ledger and the Run/tool/action loop while
making long-thread context reproducible, low-trust, recoverable, and safe for
attachments.

## Product contract

- Before a Run's first model call, Runtime counts only model-bound history from
  prior **completed** Runs.
- The current Run's client context and user message, Agent instructions, and
  Tool definitions do not contribute to the `100000` trigger.
- Compaction is queued only when the history count is strictly greater than
  `AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS`.
- A checkpoint replaces all history through a completed-Run cutoff. There is no
  separately configured raw tail. The cutoff cannot split a function call from
  its output.
- Checkpoint output is capped by
  `AGENT_CONTEXT_SUMMARY_MAX_TOKENS` (default `2000`).
- The triggering Run normally continues on its frozen ready generation while
  compaction runs on the worker's independent Context lane.
- Provider truncation remains disabled. A hard-limit error durably suspends the
  Run and retries exactly once after its required generation is ready.

## Planning and materialization

`CanonicalContextPlan` contains only stable ledger IDs, sequences, immutable
Product file IDs, checkpoint references, trust labels, and raw provider items.
Its SHA-256 never contains a signed URL.

Immediately before any token-count, compaction, or main-model call, the shared
`AgentAttachmentService` converts `asset_id` blocks to short-lived provider
URLs. Materialized input is ephemeral. Signed `image_url` and `file_url` values
are not written to the ledger or a URL cache.

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
state. A second hard-limit rejection fails explicitly; Runtime never silently
drops input.

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

Replay v2 exports the frozen Run context state, typed checkpoint metadata, and
the current Thread Context Head. Checkpoint content remains redacted unless the
existing privileged content flag is enabled.

The deterministic Context eval catalog is
`evals/context/v2/scenarios.json`. It covers attachment materialization,
prompt-injection trust, typed-summary preservation, recursive compaction,
crash-attempt bounds, dead-letter recovery, pinned-version drift, and durable
hard-limit resume.

Worker controls:

- `AGENT_CONTEXT_COMPACTION_BATCH_SIZE` (default `2`)
- `AGENT_CONTEXT_COMPACTION_CONCURRENCY` (default `1`)
- `AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS` (default `3`)

Context jobs use the existing worker database lease duration and renewal
interval; startup validation requires the batch size to cover configured
Context concurrency.

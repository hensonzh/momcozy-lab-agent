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
Its SHA-256 never contains a model-fetch capability URL.

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

Replay v1 exports the frozen Run context state, typed checkpoint metadata, and
the current Thread Context Head. Checkpoint content remains redacted unless the
existing privileged content flag is enabled.

The deterministic Context eval catalog is
`evals/context/v1/scenarios.json`. It covers attachment materialization,
prompt-injection trust, typed-summary preservation, recursive compaction,
crash-attempt bounds, dead-letter recovery, pinned-version drift, and durable
hard-limit resume.

Worker controls:

- `AGENT_CONTEXT_COMPACTION_BATCH_SIZE` (default `2`)
- `AGENT_CONTEXT_COMPACTION_CONCURRENCY` (default `1`)
- `AGENT_CONTEXT_COMPACTION_MAX_ATTEMPTS` (default `3`)
- `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` (default `8000`; must be below the
  compaction threshold)

Context jobs use the existing worker database lease duration and renewal
interval; startup validation requires the batch size to cover configured
Context concurrency.

# Deployment

## Boundary

Agent Runtime owns its API, run worker, PostgreSQL, Redis, tools, actions,
replay, and eval. Product Backend owns user and business data and exposes only
typed internal APIs.

The two services use independent databases and release pipelines. Flutter
configures the Runtime origin with `MOMCOZY_AGENT_API_BASE_URL`.

## Database

The current Runtime migration is a fresh baseline for an empty PostgreSQL
database. It does not support older Runtime schemas, persisted runs, old Tool
names, or old Action data. Internal test data must be discarded by dropping and
recreating the Runtime database before this baseline is deployed.

## Release

1. Provision dedicated PostgreSQL and Redis, Product internal API/JWKS access,
   the outbound Product service key, the inbound Runtime admin service key, and
   the model-provider secret.
2. Validate `docker-compose.prod.yml`.
   Before rolling out `agent_context_history_policy.v2`, stop new Run admission
   and drain queued, retry-wait, and running Context jobs created under `v1`;
   their source hashes intentionally include the history policy.
3. Apply `alembic upgrade head` to the new empty Runtime database.
4. Start the API and run worker.
5. Validate the pinned Product OpenAPI contract and the exact Product release
   artifact with `scripts/check_product_backend_contract.py`.
6. Run `scripts/run_behavior_eval.py` against database-backed replay bundles.
   Structural assertions must pass, and all safety and response-quality reviews
   must be resolved.
7. Run the deterministic Runtime v1 contract harness:
   `python scripts/run_runtime_v1_harness.py --junit reports/runtime-v1.xml`.
8. Run the deterministic Context Pipeline release gate:
   `pytest -q tests/test_context_eval_gate.py tests/test_context_pipeline_v1.py`.
9. Run the Agents SDK execution contract gate:
   `pytest -q tests/test_openai_agents_execution.py tests/test_single_agent_loop.py`.
10. Verify `AGENT_MODEL_PROVIDER=openai_responses`. When a custom
    `OPENAI_BASE_URL` is configured, explicitly attest Responses compatibility
    with `OPENAI_RESPONSES_COMPATIBLE_BASE_URL=true`; production URLs must use
    remote HTTPS and contain no credentials, query, or fragment.
11. Verify `AGENT_MODEL_TIMEOUT_SECONDS` against the production model and keep
   `AGENT_WORKER_DB_LEASE_DURATION_SECONDS` at least one lease-renewal interval
   longer. The execution contract gate must prove that the single Agent stream
   stops at the configured wall-clock deadline.
12. Set `AGENT_MODEL_MAX_OUTPUT_TOKENS` (default `8000`) and keep
    `AGENT_CONTEXT_RESPONSE_RESERVE_TOKENS` at least that large but below
    `AGENT_CONTEXT_COMPACTION_THRESHOLD_TOKENS`. Verify the complete-request
    budget gate with production Tool schemas. A Skill loader result must remain
    complete; handlers for ordinary large ToolResults must define an explicit,
    bounded `model_output`.
13. Verify deterministic safety cases complete with a durable
    `safety.decision` and no model or Tool invocation.
14. Verify both durable and transient SSE cursor recovery, cancellation,
   confirmation permission rechecks, confirmation expiry, idempotent Product
   action application, terminal events, ready health, and the durable
   context-compaction/next-Run gate described in
   [context-compaction.md](context-compaction.md).

For Runtime v1, deploy Product Backend before Runtime so refreshed JWTs contain
`agent:run` and capability permissions. The baseline migration requires an
empty, independently resettable Runtime database; the exact drain/reset/rollout
contract is in [runtime-v1.md](runtime-v1.md). Never reset Product data.

## Rollback

- Roll back application images only when they support the current baseline.
- Quiesce new runs and drain active runs plus
  queued/retry-wait/running context-compaction jobs before stopping workers.
- Never point Product Backend and Agent Runtime at the same PostgreSQL
  database.
- Database downgrade and cross-service data conversion are not rollback
  mechanisms; recreate resettable test databases or roll forward.

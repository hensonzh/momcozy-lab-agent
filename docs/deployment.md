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
3. Apply `alembic upgrade head` to the new empty Runtime database.
4. Start the API and run worker.
5. Validate the pinned Product OpenAPI contract and the exact Product release
   artifact with `scripts/check_product_backend_contract.py`.
6. Run `scripts/run_behavior_eval.py` against database-backed replay bundles.
   Structural assertions must pass, and all safety and response-quality reviews
   must be resolved.
7. Run the deterministic Context Pipeline release gate:
   `pytest -q tests/test_context_eval_gate.py tests/test_context_pipeline_v2.py`.
8. Run the Agents SDK execution contract gate:
   `pytest -q tests/test_openai_agents_execution.py tests/test_multi_agent_loop.py`.
9. Verify `AGENT_MODEL_TIMEOUT_SECONDS` against the production model and keep
   `AGENT_WORKER_DB_LEASE_DURATION_SECONDS` at least one lease-renewal interval
   longer. The execution contract gate must prove that both root and delegated
   Agent streams stop at the configured wall-clock deadline.
10. Verify SSE replay, cancellation, confirmation expiry, idempotent Product
   action application, terminal events, ready health, and the durable
   context-compaction/next-Run gate described in
   [context-compaction.md](context-compaction.md).

## Rollback

- Roll back application images only when they support the current baseline.
- Quiesce new runs and drain active runs plus
  queued/retry-wait/running context-compaction jobs before stopping workers.
- Never point Product Backend and Agent Runtime at the same PostgreSQL
  database.
- Database downgrade and cross-service data conversion are not rollback
  mechanisms; recreate resettable test databases or roll forward.

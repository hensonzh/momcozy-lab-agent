# Migration and Cutover

## State

The code migration is complete. Runtime owns the public Agent API, ledger,
workers, Agent definitions, Tool/Action execution, fact/memory, replay/eval,
operations scripts, deployment templates, and CI. Product Backend retains
product data and exposes only typed `/v1/internal/agent/*` capabilities.

Legacy Runtime data, Tool-name, and persisted-run compatibility are
intentionally unsupported. Product Backend keeps its historical Alembic chain
so existing Product databases can retain business data while the bridge
migration removes only Runtime-owned tables.

## Production Topology

- API, run worker, and fact worker are continuous processes.
- Memory consolidation runs once per day for the previous UTC date.
- Replay/eval is an audited operator CLI or admin API, not a continuous worker.
- Runtime PostgreSQL is the durable source of truth; Redis provides controls
  and transient stream notification.
- Product Backend is reached over HTTPS with the Runtime service key. Runtime
  never imports Product code or reads Product tables.
- Flutter uses one `MOMCOZY_AGENT_API_BASE_URL`; every `/v1/agent/*` request,
  including run, stream, cancel, client-event, and action traffic, moves
  together.

## Production Cutover

1. Provision dedicated PostgreSQL and Redis, Product internal API/JWKS access,
   the outbound Product service key, the independent inbound Runtime admin
   service key, and the model-provider secret.
2. Validate `docker-compose.prod.yml`, apply `alembic upgrade head`, then start
   the API, run worker, and fact worker. Schedule memory consolidation daily.
3. Pass these independent release gates:
   - Refresh `docs/contracts/product.openapi.generated.json` from the Product
     release artifact, then run `scripts/check_product_backend_contract.py`
     against both the pinned copy and that exact release artifact.
   - Run `scripts/run_behavior_eval.py --run-map ...` over real, database-backed
     replay bundles for every active case in
     `evals/behavior/v1/scenarios.json`. Structural assertions must pass, and
     every `review_required` medical, crisis, security, and response-quality
     rubric must be resolved by a live-model judge or recorded human review.
     Catalog validation alone does not verify model safety or answer quality.
   - Complete an end-to-end Runtime run that proves SSE disconnect/replay,
     action confirmation with idempotency, Product-side application, and the
     final terminal event. Also prove an abandoned confirmation emits
     `action.expired` and `run.expired` and releases its active-run slot.
   - Require ready health and API/run-worker/fact-worker/operations-CLI smoke
     tests.
4. Stop the legacy Product Agent worker and prevent new legacy Agent runs.
   Never allow old and new workers to own the same traffic or queue.
5. Change the gateway route or Flutter `MOMCOZY_AGENT_API_BASE_URL` once so the
   complete `/v1/agent/*` surface points to Runtime.
6. Monitor run terminal rates, queue age, Product dependency failures, Redis
   reconnects, fact dead letters, and memory-consolidation results.

## Rollback Boundary

- Before public cutover, Runtime can be stopped without user-visible migration.
- After cutover, prefer rolling back to the previous compatible Runtime image.
  Quiesce new runs and drain or cancel active runs before stopping workers.
- Never run Product and Runtime workers concurrently or point either service at
  the other's PostgreSQL database. In-flight Runtime runs are not transferable
  to the legacy Product implementation.
- Database downgrade and Runtime-to-Product data conversion are not rollback
  mechanisms. If a schema is not backward-compatible, roll forward.
- Moving traffic back to Product, if explicitly required, is a coordinated
  outage: stop Runtime writes first, accept loss of unfinished Runtime runs,
  then switch the complete Agent base URL. Do not split run, stream, cancel, or
  action endpoints across services.

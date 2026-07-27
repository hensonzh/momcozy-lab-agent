# Agent

Independent production Agent service owned by the Agent team.

## Boundary

- Runtime owns `/v1/agent/*`, Agent definitions, threads, runs, append-only
  messages/events, tools, actions, fact/memory, replay/eval, and its workers.
- Product Backend owns product data and JWT signing. Runtime reaches it only
  through typed `/v1/internal/agent/*` HTTPS APIs using its service identity.
- Runtime has its own PostgreSQL database and never imports Product Backend
  implementation modules or reads product tables.

## Processes

- **API:** public Agent REST/SSE, memory/fact controls, admin replay/eval, and
  live/ready health endpoints.
- **Run worker:** executes durable queued runs, resumes confirmed actions, and
  reclaims abandoned confirmations with a low-frequency durable expiry sweep.
- **Fact worker:** asynchronously extracts approved structured facts from
  completed turns.
- **Memory consolidation:** a daily one-shot job that consolidates approved
  facts into owner-scoped memory.
- **Replay/eval ops:** an audited one-shot CLI for exporting a persisted run and
  evaluating a stored case, plus a release behavior gate over real persisted
  replay bundles.

PostgreSQL is the durable source of truth. Redis carries run controls and
transient stream notifications; losing Redis must not erase the durable ledger.

Flutter configures exactly one Agent origin through
`MOMCOZY_AGENT_API_BASE_URL`; runs, streams, cancellation, client events, and
actions all derive their `/v1/agent/*` URLs from it. See
[deployment.md](docs/deployment.md) for deployment and rollback.

## Local Run

```bash
cp env/compose.local.env.example env/compose.local.env
docker compose -f docker-compose.local.yml up --build --wait api worker fact-worker
```

Compose starts PostgreSQL, Redis, and a local S3-compatible MinIO bucket,
applies `alembic upgrade head`, then starts
the API and continuous workers. PostgreSQL defaults to `127.0.0.1:5433` and
Redis to `127.0.0.1:6380`.
MinIO defaults to `127.0.0.1:9002`; it stores tool outputs that exceed
`AGENT_TOOL_OUTPUT_MAX_INLINE_BYTES`.

The API binds to `127.0.0.1:8010` by default. For physical-device development,
set `MOMCOZY_AGENT_API_BIND=0.0.0.0:8010` for the Compose command and
use the development machine's LAN address in Flutter.

The local `PRODUCT_BACKEND_SERVICE_KEY` must match Product Backend
`AGENT_RUNTIME_SERVICE_API_KEY`. `AUTH_JWT_ISSUER` must match the Product
Backend issuer; Runtime fetches only public signing keys from `AUTH_JWKS_URL`.
`RUNTIME_ADMIN_SERVICE_KEY` is a separate inbound operator credential for
`/v1/agent/admin/*` and must not be reused as the Product service identity.

Run daily memory consolidation:

```bash
docker compose -f docker-compose.local.yml --profile ops run --rm memory-consolidation
```

Export a replay, optionally evaluating a stored case:

```bash
python scripts/run_replay_eval.py --run-id <run-uuid> [--eval-case-id <case-uuid>]
```

Validate the versioned behavior catalog without calling a model:

```bash
python scripts/run_behavior_eval.py --validate-only \
  --output-json reports/behavior-catalog.json \
  --junit reports/behavior-catalog.xml
```

For a release, execute each catalog prompt through the deployed Runtime, map
each case ID to its resulting run UUID in a local
`momcozy.behavior_eval_run_map.v1` JSON file, then evaluate the database-backed
replays:

```bash
python scripts/run_behavior_eval.py \
  --run-map /secure/path/release-run-map.json \
  --output-json reports/behavior-release.json \
  --junit reports/behavior-release.xml
```

The structural gate checks terminal state, final response events, responding
agent, the exact specialist set, required/forbidden tools, and forbidden
actions. Catalog prompts and expected values are never accepted as observed
trace; every report records the database run UUID. Response-quality rubrics are
separate: without an injected live-model judge or recorded manual review, the
report is `review_required` (exit code 2), not a full pass. Exit code 1 means a
structural or input failure. A judge is only accepted when its replay export
contains the actual final response; a redacted response remains
`review_required`.

Before a cross-service release, validate the exact Product Backend OpenAPI
artifact consumed by Runtime. Independent CI always validates the pinned
`docs/contracts/product.openapi.generated.json`; refresh that file from the
Product release artifact, then validate the exact artifact as well:

```bash
python scripts/check_product_backend_contract.py \
  --openapi-path ../backend/docs/openapi.generated.json
```

Health endpoints are `GET /v1/health/live` and `GET /v1/health/ready`.
Ready health requires PostgreSQL, Redis, fresh Agent/fact worker heartbeats,
and a valid Product Backend JWKS when `WORKER_HEARTBEATS_REQUIRED=true`.

Runtime processes emit privacy-safe structured operation logs for HTTP, run,
tool, and model outcomes/latency. Collection dimensions and incident guidance
are defined in [observability.md](docs/observability.md).

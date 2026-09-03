# Agent Runtime (agent/)

Independent Agent Runtime service.

Human-facing documentation uses the canonical service names `Product Backend
(backend/)` and `Agent Runtime (agent/)`. Existing identifiers such as
`PRODUCT_BACKEND_BASE_URL`, `MOMCOZY_AGENT_API_BASE_URL`, and the
`product-backend` Compose alias remain stable compatibility names.

## Boundary

- Agent Runtime owns `/v1/agent/*`, the CozyMate definition, threads, runs,
  append-only messages/events, tools, actions, replay/eval, and
  its workers.
- `app/agent_runtime/` is the reusable execution core. It receives one runtime
  definition, Tool handlers, and Action policies through constructor injection;
  it never imports `app/agent`, `app/capabilities`, or `app/bootstrap`.
- `app/bootstrap/` is the composition root, while
  `app/api/agent_runtime/` owns the public HTTP delivery layer.
- Product Backend owns product data and JWT signing. Agent Runtime reaches it only
  through typed `/v1/internal/agent/*` HTTPS APIs using its service identity.
- Agent Runtime has its own PostgreSQL database and never imports Product Backend
  implementation modules or reads product tables.
- `app/agent/` owns the CozyMate definition, its single system prompt,
  versioned service Skills, Skill loader, and the global progressive Tool
  catalog. Reusable Tool implementations live under
  `app/capabilities/`; each Capability also owns its concrete Action
  applicators and Action policy declarations.

## Processes

- **API:** public Agent Runtime REST/SSE, admin replay/eval, and live/ready health
  endpoints.
- **Run worker:** executes durable queued runs, resumes confirmed actions, and
  reclaims abandoned confirmations with a low-frequency durable expiry sweep.
  The same process also runs the independently bounded durable Thread-context
  compaction lane.
- **Replay/eval ops:** an audited one-shot CLI for exporting a persisted run and
  evaluating a stored case, plus a release behavior gate over real persisted
  replay bundles.

PostgreSQL is the durable source of truth. Redis carries run controls and
transient stream notifications; losing Redis must not erase the durable ledger.
The durable loop uses OpenAI Agents SDK as its inner model/Tool execution
engine with a provider-neutral Responses adapter for OpenAI or Azure OpenAI,
without SDK `Session`; process recovery always rebuilds state from the Agent Runtime
ledger.

Flutter configures exactly one Agent Runtime origin through
`MOMCOZY_AGENT_API_BASE_URL`; runs, streams, cancellation, client events, and
actions all derive their `/v1/agent/*` URLs from it. See
[deployment.md](docs/deployment.md) for deployment and rollback.

Environment names are fixed across current Agent Runtime artifacts: `local` is
developer work and `test` is the shared internal server profile. CI remains an
ephemeral verification lane (`momcozy-lab-agent-ci`), not a third deployable
environment. The shared server uses `docker-compose.test.yml` together with
`env/compose.test.env`; no production deployment profile is currently shipped.

## Local Run

```bash
cp env/compose.local.env.example env/compose.local.env
docker compose -f docker-compose.local.yml up --build --wait api worker
```

Compose starts PostgreSQL, Redis, and a local S3-compatible MinIO bucket,
applies `alembic upgrade head`, then starts
the API and run worker. PostgreSQL defaults to `127.0.0.1:5433` and
Redis to `127.0.0.1:6380`.
MinIO defaults to `127.0.0.1:9002`; it stores tool outputs that exceed
`AGENT_TOOL_OUTPUT_MAX_INLINE_BYTES`.

The API binds to `127.0.0.1:8010` by default. For physical-device development,
set `MOMCOZY_AGENT_API_BIND=0.0.0.0:8010` for the Compose command and
use the development machine's LAN address in Flutter.

The local `PRODUCT_BACKEND_SERVICE_KEY` must match Product Backend
`AGENT_RUNTIME_SERVICE_API_KEY`. `AUTH_JWT_ISSUER` must match the Product Backend
issuer; Agent Runtime fetches only public signing keys from `AUTH_JWKS_URL`.
`RUNTIME_ADMIN_SERVICE_KEY` is a separate inbound operator credential for
`/v1/agent/admin/*` and must not be reused as the Product Backend service identity.

## CI Container Profile

`docker-compose.ci.yml` is a CI-only override and is never deployed to a
server. CI combines it with `docker-compose.local.yml`, changes the Compose
project to `momcozy-lab-agent-ci`, and builds
`momcozy-lab-agent:ci`. A public JWKS fixture replaces the unavailable
Product Backend signing-key endpoint, while a CI-only model credential lets the
idle worker validate its startup contract without calling a real provider.
Readiness requires the API, migration, worker heartbeat, PostgreSQL, Redis, and
JWKS checks to pass before the stack is cleaned up.

## Test Run

Copy `env/compose.test.env.example` to the private host env. Copy only the
Agent-scoped PostgreSQL password, Redis ACL password, and MinIO bucket access
pair from Product Backend's private test env, fill the remaining secrets,
and set mode `0600`. Start Product Backend test first, then use the protected
`agent-test-delivery` workflow. A bare Compose `up` is not a release path;
the release script injects the manifest-owned image and heartbeat generation.

The test API binds to `127.0.0.1:8002` by default; the Product Backend uses
the adjacent `127.0.0.1:8001`. Port `8010` remains local-development-only.
Agent Runtime joins the external `momcozy-lab-test` network. Product Backend
Compose owns the single PostgreSQL, Redis, and MinIO instances; Agent Runtime uses database
`agent_runtime_test`, Redis DB 1 with `agent-runtime:*` keys, and bucket
`agent-runtime-test`.

The public test origin is
`https://agent-test.lute-momcozylab.luteos.cloud:8443`. Host Nginx routes that
SNI site to `127.0.0.1:8002`; it does not expose the container port directly.
See [deployment.md](docs/deployment.md) for the service-specific Nginx template
and installation contract.

Test has no on-host build tag. The protected workflow obtains the
digest-qualified image from the requested main commit's successful CI manifest;
neither image nor release identity is stored in `deploy.env`. Legacy names such
as `momcozy-production-backend` are not valid for this service.

A successful `agent-ci` run on `main` publishes the exact image already tested
by unit, migration, container, behavior-eval, and Runtime v1 gates. The manual,
protected `agent-test-delivery` workflow consumes that digest, validates the
current Product Backend release manifest and pinned OpenAPI contract, pauses API
admission, drains active work before stopping the worker, and backs up/migrates
only when the schema revision changes. It clears the prior heartbeat, checks
the new commit-specific worker generation plus loopback/SNI readiness, and
restores the current-manifest stack on failure. It records the Agent and Product
release identities together under `/opt/momcozy-lab/current/agent/release-manifest.json`.

Export a replay, optionally evaluating a stored case:

```bash
python scripts/run_replay_eval.py --run-id <run-uuid> [--eval-case-id <case-uuid>]
```

Replay v1 includes the run's ordered model-execution manifest: Prompt identity
and content hashes, Tool schemas and hashes, Agents SDK/OpenAI SDK versions,
model settings, ordered Context-item/request hashes, unified Runtime metadata,
and the versioned Tool/Action catalog hashes. The manifest does not copy Prompt
or user content; user-derived Context content remains governed by the replay
export's explicit content-inclusion flag.

Agent Runtime-owned versions live in one source,
`app/agent_runtime/runtime_metadata.py`. Tool/Action catalog drift is checked
against `docs/runtime-contract-catalog.generated.json`:

```bash
python scripts/export_runtime_contract_catalog.py --check
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

The structural gate checks terminal state, final response events, the fixed
`cozymate` responder, the exact loaded Skill set, required/forbidden tools,
forbidden actions, and deterministic safety decisions. Catalog prompts and
expected values are never accepted as observed trace; every report records the
database run UUID. Response-quality rubrics are
separate: without an injected live-model judge or recorded manual review, the
report is `review_required` (exit code 2), not a full pass. Exit code 1 means a
structural or input failure. A judge is only accepted when its replay export
contains the actual final response; a redacted response remains
`review_required`.

Before a cross-service release, validate the exact Product Backend OpenAPI
artifact consumed by Runtime. Independent CI always validates the pinned
`docs/contracts/product.openapi.generated.json`; refresh that file from the
Product Backend release artifact, then validate the exact artifact as well:

```bash
python scripts/check_product_backend_contract.py \
  --openapi-path ../backend/docs/openapi.generated.json
```

Health endpoints are `GET /v1/health/live` and `GET /v1/health/ready`.
Ready health requires PostgreSQL, Redis, a fresh Agent Runtime worker heartbeat,
and a valid Product Backend JWKS when `WORKER_HEARTBEATS_REQUIRED=true`.

Runtime processes emit privacy-safe structured operation logs for HTTP, run,
tool, and model outcomes/latency. Collection dimensions and incident guidance
are defined in [observability.md](docs/observability.md).

The current single-agent progressive Skill/Tool loading model is defined
in [single-agent-design.md](docs/single-agent-design.md).
The canonical human-readable Tool/Action design, complete inventory, current
limitations, and mandatory change protocol are maintained in
[tools.md](docs/tools.md). Every Tool-scheme change must update that document.
Runtime v1 authorization, Tool/Action policy, double-cursor streaming,
provider, migration, and deterministic harness contracts are defined in
[runtime-v1.md](docs/runtime-v1.md).
Provider configuration, capability gates, Azure migration, error normalization,
and rollout checks are defined in
[model-providers.md](docs/model-providers.md).
The Context Pipeline v1 100k-token contract, typed low-trust checkpoints,
next-Run generation gate, audited dead-letter recovery, and durable hard-limit
resume are defined in
[context-compaction.md](docs/context-compaction.md).

# Deployment

## Boundary

Agent Runtime owns its API, run worker, Agent Runtime database, Redis keys, tools,
actions, replay, and eval. Product Backend owns user and business data and
exposes only typed internal APIs. Product Backend staging Compose owns the shared
PostgreSQL, Redis, and MinIO service instances.

The two services use independent databases and release pipelines. Flutter
configures the Agent Runtime origin with `MOMCOZY_AGENT_API_BASE_URL`.

## Environment Identity

- `local`: developer-only Compose and local dependencies.
- `test`: automated tests and CI only; never a shared server deployment.
- `staging`: the shared internal server and Flutter staging flavor.

No production Compose/env profile is currently maintained.
The requested `backend-test` and `agent-test` DNS labels are ingress names only;
they do not rename the Compose/env profile from `staging` to `test`.

The environment token must agree across the Compose filename, private env
filename, and Compose project. Staging therefore uses
`docker-compose.staging.yml`, `env/compose.staging.env`, and project
`momcozy-lab-agent-staging`. The image repository remains environment-neutral,
and staging accepts only a digest-qualified immutable artifact such as
`ghcr.io/hensonzh/momcozy-lab-agent@sha256:...`; there is no on-host staging
build or mutable staging tag.

CI follows the same identity rule without becoming a deployable environment.
`docker-compose.ci.yml` is a CI-only override for
`docker-compose.local.yml`; the merged stack uses project
`momcozy-lab-agent-ci` and image `momcozy-lab-agent:ci`. Its public JWKS
fixture supplies only verification keys, and the worker receives a non-secret
CI credential solely to validate startup and heartbeat readiness. The CI file
must never be used with the staging Compose stack.

On the shared server, Product Backend staging binds `127.0.0.1:8001` and Agent
Runtime staging binds `127.0.0.1:8002`; `8010` is reserved for Agent Runtime
local development.

## Database

The current Agent Runtime migration is a fresh baseline for an empty PostgreSQL
database. It does not support older Agent Runtime schemas, persisted runs, old Tool
names, or old Action data. Internal test data must be discarded by dropping and
recreating the Agent Runtime database before this baseline is deployed.
The baseline stores the canonical non-secret provider identity on Context jobs
and checkpoints; it must not be backfilled from a mutable deployment alias.

## Standard Staging Delivery

`agent-ci` builds the image once, runs the full unit/type/migration/container,
behavior-eval, and Runtime v1 gates against it, then publishes that same image
under the full commit SHA and records the registry digest. It does not deploy.

Configure the GitHub `staging` environment with required reviewers and
`STAGING_SSH_HOST`, `STAGING_SSH_PORT`, `STAGING_SSH_USER`,
`STAGING_SSH_PRIVATE_KEY`, and `STAGING_SSH_KNOWN_HOSTS`. The host deployment
user must own `/opt/momcozy-lab`, have Docker access, and already be
authenticated to pull the private GHCR package. Store the private Agent env at
`/opt/momcozy-lab/shared/agent/deploy.env` with mode `0600`.

Run `.github/workflows/agent-staging-delivery.yml` manually only after Product
Backend has been deployed. Supply the full commit and
`ghcr.io/hensonzh/momcozy-lab-agent@sha256:...`. The workflow stages the exact
Git commit under `/opt/momcozy-lab/releases/agent/<commit>` and invokes
`scripts/staging_release.py`, which:

1. rejects a mutable image, wrong release root, mismatched OCI revision label,
   occupied `127.0.0.1:8002`, or incorrectly owned shared network/PostgreSQL;
2. requires the current Product Backend manifest at
   `/opt/momcozy-lab/current/backend/release-manifest.json` to match the pinned
   Product OpenAPI snapshot;
3. validates the pinned Product contract and behavior-eval release contract
   from inside the image; the full Runtime v1 pytest harness remains a required
   CI gate because production images intentionally contain neither tests nor
   pytest;
4. stops API and worker, backs up only `agent_runtime_staging`, applies Alembic
   explicitly, and starts API/worker without building;
5. verifies local and SNI readiness and records both service identities in the
   Agent release manifest before advancing the current pointer.

Rollback requires `confirm_schema_compatible=true`, revalidates the current
Product Backend contract, stops both Agent processes, and restores only the
previous Agent image. It never downgrades the database; use a roll-forward when
old code is incompatible with the current schema.

## Release Acceptance Detail

1. Start Product Backend staging first. It creates network `momcozy-lab-staging`, the
   `agent_runtime_staging` database/role, Redis logical DB 1, and bucket
   `agent-runtime-staging`. Copy the matching Agent Runtime DB, Redis, and MinIO
   values into Agent Runtime's private `env/compose.staging.env`.
2. Validate `docker-compose.staging.yml` with that private env and confirm the
   Agent Runtime services join the external shared network.
   Before rolling out `agent_context_history_policy.v2`, stop new Run admission
   and drain queued, retry-wait, and running Context jobs created under `v1`;
   their source hashes intentionally include the history policy.
3. Apply `alembic upgrade head` to the new empty Agent Runtime database.
4. Start the API and run worker.
5. Validate the pinned Product Backend OpenAPI contract and the exact Product Backend release
   artifact with `scripts/check_product_backend_contract.py`.
6. Run `scripts/run_behavior_eval.py` against database-backed replay bundles.
   Structural assertions must pass, and all safety and response-quality reviews
   must be resolved.
7. Run the deterministic Agent Runtime v1 contract harness:
   `python scripts/run_runtime_v1_harness.py --junit reports/runtime-v1.xml`.
8. Run the deterministic Context Pipeline release gate:
   `pytest -q tests/test_context_eval_gate.py tests/test_context_pipeline_v1.py`.
9. Run the provider and Agents SDK execution contract gate:
   `pytest -q tests/test_provider_contracts.py tests/test_provider_runtime.py
   tests/test_provider_errors.py tests/test_openai_agents_execution.py
   tests/test_single_agent_loop.py`.
10. Select exactly one supported provider. For `openai_responses`, configure
    `OPENAI_API_KEY`/`OPENAI_MODEL`; a custom `OPENAI_BASE_URL` additionally
    requires `OPENAI_RESPONSES_COMPATIBLE_BASE_URL=true`. For
    `azure_openai_responses`, configure the `/openai/v1` endpoint, deployment,
    exact model family/version, region, deployment type, and either Entra
    workload identity (recommended) or API key. Production endpoints must use
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
   confirmation permission rechecks, confirmation expiry, idempotent Product Backend
   action application, terminal events, ready health, and the durable
   context-compaction/next-Run gate described in
   [context-compaction.md](context-compaction.md).
15. Before changing provider, drain active Runs and Context jobs, run the live
    staging capability/error/compaction checks in
    [model-providers.md](model-providers.md), and compare Azure estimates with
    actual `usage.input_tokens`. Do not run two providers against the same
    durable worker queue during a cutover.

For Agent Runtime v1, deploy Product Backend before Agent Runtime so the shared
infrastructure/network exists and refreshed JWTs contain
`agent:run` and capability permissions. The baseline migration requires an
empty, independently resettable Agent Runtime database; the exact
drain/reset/rollout contract is in [runtime-v1.md](runtime-v1.md). Never reset
Product Backend data.

## Nginx Edge Proxy

`deploy/nginx/momcozy-lab-agent-runtime.conf` is the versioned Agent Runtime
site template. It owns only
`agent-test.lute-momcozylab.luteos.cloud:8443` and proxies it to
`127.0.0.1:8002`. Its Run SSE location disables proxy buffering, caching, and
compression and keeps the connection timeout at one hour.

The host must keep exactly one separate unknown-host rejection site. Individual
service files must not declare `default_server`, because the legacy site,
Product Backend, and Agent Runtime all share the host's `8443` listener. The
shared staging leaf certificate at
`/etc/nginx/tls/momcozy-lab-staging/fullchain.pem` must contain both DNS SANs:

- `backend-test.lute-momcozylab.luteos.cloud`
- `agent-test.lute-momcozylab.luteos.cloud`

Its private key is `/etc/nginx/tls/momcozy-lab-staging/privkey.pem`. Product
Backend and Agent Runtime deliberately reference this one SAN certificate;
SNI selects the correct service site and loopback upstream.

Install the Agent Runtime site under a service-specific host filename. Do not
replace the existing legacy `momcozy-api` site:

```bash
sudo install -m 0644 deploy/nginx/momcozy-lab-agent-runtime.conf \
  /etc/nginx/sites-available/momcozy-lab-agent-runtime
sudo ln -sfn /etc/nginx/sites-available/momcozy-lab-agent-runtime \
  /etc/nginx/sites-enabled/momcozy-lab-agent-runtime
sudo nginx -t
sudo systemctl reload nginx
curl --fail --show-error \
  --resolve agent-test.lute-momcozylab.luteos.cloud:8443:127.0.0.1 \
  --cacert /etc/nginx/tls/momcozy-ca/ca.pem \
  https://agent-test.lute-momcozylab.luteos.cloud:8443/v1/health/live
```

## Rollback

- Roll back application images only when they support the current baseline.
- Quiesce new runs and drain active runs plus
  queued/retry-wait/running context-compaction jobs before stopping workers.
- They may share one PostgreSQL service, but never point Product Backend and
  Agent Runtime at the same PostgreSQL database.
- Database downgrade and cross-service data conversion are not rollback
  mechanisms; recreate resettable test databases or roll forward.

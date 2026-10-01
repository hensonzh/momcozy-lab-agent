# Agent Runtime — US-East UAT (B) single-host strategy

Decision: 2026-09-30. This is a **design and handoff**, not an executable
release. The retired B Kubernetes workloads and migration Job templates were
removed. A's root Dockerfile, Compose files, private env and GitHub delivery
workflow remain unchanged; B must not call A's `staging` release entrypoint.

## Ownership and isolation

- B Agent API, worker and one-shot Alembic migration use one immutable Agent
  image digest in their own Compose project on the approved US-East host.
  This project joins the **B Backend-owned external Docker network** and does
  not launch or own PostgreSQL, Redis or MinIO containers.
- Both services use the same **B-only** PostgreSQL container. Agent connects
  to `momcozy_lab_agent_uat`; Product uses `momcozy_lab_backend_uat`, with
  separate roles and migrations. Previously discussed RDS databases are not
  automatically copied to the new local service; plan data transfer if needed.
- B Redis has Product and Agent on DB 0 with disjoint existing key prefixes
  and separately scoped ACL identities; DB 0/prefixes are not authorization.
  B MinIO has two private buckets, with Agent using only its own bucket and
  scoped credentials. Do not use A volumes or managed RDS/ElastiCache/S3.
- `deploy/config_us-east-uat` is a **non-secret** seed. Inject the private
  B `DATABASE_URL`, `REDIS_URL` ending `/0`, MinIO bucket/credentials,
  Product service URL/key, JWKS/JWT settings and model provider credentials
  through an isolated 0600 env. Set the same full 40-character release SHA as
  `RUNTIME_RELEASE_ID` for API and worker heartbeat checks.

## Release handoff

- B source: GitHub `dev`, declared in `release-source.json`. Build
  from repo root with `docker build -f deploy/Dockerfile .` and deploy the
  verified image digest to the approved B host. B Compose and private env
  templates exist. The B-only GitHub `dev` `agent-b-validation.yml` is
  prepared locally; after commit/push it will check contracts, render Compose
  and locally build the B Dockerfile; it neither
  pushes an image nor deploys. Release integration and live validation remain.
  B Compose requires `MOMCOZY_B_ENV_MARKER` so A env cannot pass static
  rendering by accident; `scripts/check_b_env.py` checks B identity, loopback
  ports, DB 0, 10-run settings, private file mode and no placeholders. The
  marker alone does not prove isolation. `scripts/release.py` still
  intentionally refuses B deployment.
- Run `python scripts/check_b_env.py --env-file /absolute/path/to/private-agent.env`
  before `docker compose --env-file /absolute/path/to/private-agent.env -f docker-compose.us-east-uat.yml config --quiet` (set `MOMCOZY_AGENT_ENV_FILE`,
  immutable `MOMCOZY_AGENT_IMAGE` and full SHA `MOMCOZY_AGENT_RELEASE_ID` outside
  the file). Static checks do not verify real services, limits or credentials.
- Bootstrap B stateful services via Product first. After the Product schema
  and API are healthy, migrate Agent's database with its own DDL rights, then
  start API and worker. Verify ready endpoint and worker heartbeat. Preserve
  B-only backup, rollback, lock and compatibility checks.
- **10 concurrent Agent runs per worker** (`AGENT_WORKER_BATCH_SIZE=10`,
  `AGENT_WORKER_CONCURRENCY=10`) is a target, not proven host capacity.
  Re-measure CPU, memory, DB connections, model rate limits, queue latency
  and OOM at 10 simultaneous runs before finalizing worker limits/replicas.
- The host, ports, domains/TLS, isolated volumes, final bucket names and
  credentials, provider egress, test evidence and Jenkins delivery details
  remain to be verified. Do not enable the B release merely by removing its
  guard or pointing A's `staging` CLI at the US-East host.

Cross-repository plan: `app/docs/deployment/b-us-east-single-host-uat.md`.

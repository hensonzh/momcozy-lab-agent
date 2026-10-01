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
  separate roles and migrations. On 2026-10-01 the operator confirmed that
  **no historical managed RDS, Redis or S3 data will be migrated**. Bootstrap
  only fresh B-owned state after Product's first-start check; do not connect
  to the managed resources or inherit their credentials.
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
  templates exist. The B-only GitHub `dev` `agent-b-validation.yml` checks
  contracts and admission, renders Compose and locally builds the B Dockerfile.
  After the validation job passes on a GitHub `dev` push, its separate
  `b-image` job publishes only the B Dockerfile image to private GHCR as
  `b-dev-<full SHA>` and records the immutable digest. Verify the exact
  commit's CI and digest before release. It does not deploy; release
  integration and live validation remain.
  B Compose requires `MOMCOZY_B_ENV_MARKER` so A env cannot pass static
  rendering by accident; `scripts/check_b_env.py` checks B identity, loopback
  ports, DB 0, 10-run settings, private file mode and no placeholders. The
  marker alone does not prove isolation. `scripts/release.py` still
  intentionally refuses B deployment. B-only `scripts/b_release.py` is a
  read-only admission check for a clean `dev` source commit, immutable image
  digest, private target/env and static Compose rendering; it does not deploy
  or prove host state, CI provenance or source-to-image labels.
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
- The US-East host identity, Docker/Compose and B-only root/backup mount were
  verified on 2026-10-01; the clean GitHub `dev` Agent snapshot
  `a689e662a21e91bcb95d4180e671cd5869c94154` was built locally after
  its B validation CI passed. The local image is not a registry digest;
  verify the B-only CI-published image separately. The mode-0600 Agent
  private env now holds newly generated B-only DB/Redis/MinIO and internal
  service credentials (mode 0600). Later on 2026-10-01 the approved A-chain
  OpenAI key was installed into the B Agent env and `/v1/models` authentication
  passed from the target host. No model inference was run, and static env/pair
  checks passed. Its target JSON declares
  `https://agent-us-dev.lute-momcozylab.luteos.cloud`, while the shared
  Backend origin is `https://backend-us-dev.lute-momcozylab.luteos.cloud`.
  Both DNS A records resolved to `32.199.186.149` on 2026-10-01, but HTTPS
  :443 timed out from both the operator Mac and target. The attached
  `launch-wizard-25` security group had no inbound TCP 80/443 rule on the
  subsequent read-only check; short-lived port listeners did not receive
  external connections. Do not attempt public TLS until approved ingress
  rules are in place and independently re-tested. No business
  containers or `current` pointer exist. B outbound HTTPS uses the host
  system CA bundle, not A's internal CA; this does not provision a
  certificate for the B ingress. TLS ingress, external exposure, isolated
  persistent volumes, final bucket names and credentials, provider egress,
  live backup/restore and 10-run load evidence remain to be verified. Do not
  enable the B release merely by removing its guard or pointing A's
  `staging` CLI at the US-East host.
- The versioned, **not installed** B Nginx template
  `deploy/us-east-uat/nginx-agent.conf` listens on :443 with the B Agent
  hostname and proxies to `127.0.0.1:8002`, preserving SSE buffering rules.
  Its `/etc/letsencrypt/live/` certificate paths are prerequisites, not
  existing files. Do not enable without public trust and readiness checks.

Cross-repository plan: `app/docs/deployment/b-us-east-single-host-uat.md`.

## Private host handoff (placeholder layout now present)

- `scripts/prepare_b_host.py` is an opt-in scaffold (`--apply`) for a fresh
  host, not for overwriting this host's existing mode-0700 layout and
  mode-0600 placeholders. Its default invocation is no-op.

- Keep B secrets only at `/opt/momcozy-lab-us-east-uat/shared/agent/north-america-staging.env`
  (mode 0600), separate from the Backend env in the same B-only root.
  `config/release-targets/north-america-staging.json.example` has B root,
  lock, env paths and the approved non-secret public URL. Keep the host's
  separate private target declaration mode 0600. A static metadata check
  passing does not verify HTTPS/TLS or authorize a release.
- Match Product and Agent service credentials, JWT issuer and public URL
  across their private files without copying A identities. B will not be
  started by the read-only admission command.

## 2026-10-01 port and ingress update

- B Agent now publishes only to `127.0.0.1:8002`; Product uses
  `127.0.0.1:8001`. The B-only private env files were updated without changing
  any credentials and retain mode 0600. Both static checks and read-only B
  release admissions pass; no services or pointers were started.
- The attached EC2 group `launch-wizard-25` has no TCP 80/443 inbound rule.
  Nginx/Certbot packages are installed but Nginx is stopped/disabled, with no
  public certificate or enabled B site. External ingress and private GHCR
  image pull authorization remain prerequisites for deployment.

# Agent Runtime — US East UAT (B lane)

This directory is a **template, not a runnable deployment**. A's root `Dockerfile`,
`docker-compose.deploy.yml`, `env/staging.env.example`, `scripts/release.py`,
and GitHub delivery workflow remain unchanged. B's release CLI still rejects
`north-america-staging`; do not disguise B as A's `staging` target.

## Build and deploy handoff for Codeup / IT

- Configure this repo's B Jenkins pipeline to build **Codeup `uat`**, as
  declared in `release-source.json`; A remains on its own `main` release path.
  IT says the B pipeline watches `uat`; confirm the job's exact branch filter.
  The `uat` branch is not yet present on Codeup, so it must be created and
  populated deliberately before the pipeline can fetch this configuration. The files are
  local until deliberately committed and synchronized. Build from the repo
  root: `docker build -f deploy/Dockerfile -t <uat-registry>/<image>:<full-commit-sha> .`.
  Publish and deploy a digest-qualified image; Agent API, worker and migration
  Job for this repo must use the **same digest**, not the Backend image.
- `deploy/config_us-east-uat` is a non-secret `KEY=VALUE` seed for the B-only
  `momcozy-agent-uat-config` ConfigMap. Never inject A's private env, database,
  Redis, bucket or model/provider secrets. `APP_ENV=staging` is an application
  mode, **not** permission to deploy on the A target. To use worker heartbeats,
  inject the **same full 40-character commit SHA** as `RUNTIME_RELEASE_ID` into
  both Agent API and worker for that release.
- Before rendering, IT must supply an **approved UAT namespace**, immutable
  image digest, UAT domain/TLS/Ingress and role-scoped Secrets
  (`momcozy-agent-api-uat-secrets`, `momcozy-agent-worker-uat-secrets`,
  `momcozy-agent-migrate-uat-secrets`). Use a separate DDL identity for the
  migration Job. Secrets stay in managed storage and out of Git/Jenkins logs.
- Agent requires an **independent PostgreSQL database and role**, not Product's
  `momcozy_lab_pre`. Its name has not been confirmed. `DATABASE_URL` and
  `REDIS_URL` are complete, private injected URLs; Redis has been specified as
  DB 0, not the `/1` in A staging. Confirm Redis TLS, cluster/Lua/Streams
  compatibility and key/ACL isolation between Product and Agent before rollout.
  Configure UAT Product URL/service key, JWT issuer/audience/JWKS, S3 bucket and
  access identity, model provider URL/key through UAT-only controlled settings.
  Never reuse A's credentials or the passwords previously shown in chat without
  an IT security review/rotation decision.
- `workloads.yaml` defines Agent API, internal Service and worker.
  `migration-job.yaml` is a separate pre-deployment Job with a unique release
  ID; **wait for success** before deploying API/worker. These templates retain
  invalid `REPLACE_WITH_*` fields until operator review. They do not create
  namespaces, Secrets, DB, Redis, bucket or Ingress; never apply as-is or point
  Jenkins/kubectl at A's cluster/namespace.
- The worker currently claims and runs **10 concurrent runs per Pod**. Worker
  CPU/memory requests and limits are deliberately placeholders until IT approves
  capacity from a 10-run load test. API limits are provisional. Verify migration,
  `/v1/health/ready` and heartbeat, actual model/tool calls, queue latency,
  memory peak/OOM and independent rollback before claiming B is ready.

# Agent Runtime Deployment

Updated: 2026-09-25. The deployable environment names are `staging` and
`production`; `test` is reserved for pytest and ephemeral CI.

For the single cross-repository file/command matrix (Backend, Agent, and Flutter),
see `app/docs/deployment/environment-workflow.md` from the workspace root.
This page documents the Agent-specific release contract.

## Files

```text
env/local.env.example
env/staging.env.example
env/production.env.example
docker-compose.local.yml
docker-compose.deploy.yml
scripts/release.py
.github/workflows/agent-delivery.yml
```

`docker-compose.ci.yml` is a CI-only override. It is never deployed.
The archived local Azure experiment under `.local/config-archive/` is not a
deployable env and is not loaded by Compose or the release workflow. If needed,
review its values and transfer the selected provider settings into the target
environment's private service env; do not add another `env_file` to Compose.

## Ownership boundary

Product Backend owns the shared PostgreSQL, Redis, MinIO, and Docker network.
Agent Runtime joins the environment network as an external consumer and must
never create a second stateful stack. It owns only its API, worker, migrations,
database schema, Redis identity/DB, output bucket and release manifest.

The following values in Agent's private env must match Backend's private env:

```text
MOMCOZY_BACKEND_COMPOSE_PROJECT
MOMCOZY_AGENT_COMPOSE_PROJECT
MOMCOZY_NETWORK_NAME
MOMCOZY_AGENT_API_BIND
MOMCOZY_POSTGRES_ADMIN_USER
MOMCOZY_AGENT_POSTGRES_DB
MOMCOZY_AGENT_POSTGRES_USER
MOMCOZY_AGENT_POSTGRES_PASSWORD
MOMCOZY_AGENT_REDIS_PASSWORD
MOMCOZY_AGENT_MINIO_BUCKET
MOMCOZY_AGENT_MINIO_ACCESS_KEY
MOMCOZY_AGENT_MINIO_SECRET_KEY
```

## Local development

```bash
cp env/local.env.example env/local.env
chmod 600 env/local.env
make agent-local-up
```

The workspace-level preferred entrypoint remains `cd ../app && make local-dev-up`
because it aligns Backend JWT/service credentials before starting Agent Runtime.

## Staging and production

Create a private host env from exactly one template and replace every
`REPLACE_WITH_*` value. Do not add `MOMCOZY_AGENT_IMAGE`,
`MOMCOZY_AGENT_RELEASE_ID`, or `RUNTIME_RELEASE_ID`; those are owned by the
release workflow.

The staging public origin currently retains the historical hostname
`https://agent-test.lute-momcozylab.luteos.cloud:8443`. Its semantic identity
and release manifest value are nevertheless `staging`.

Run `.github/workflows/agent-delivery.yml` only after the matching Product
Backend release is healthy. Select `staging` or `production`, then `deploy` or
`rollback`. The workflow consumes the digest-qualified image produced by a
successful `agent-ci` run for the selected main commit.

GitHub Environment variables:

```text
staging:
  RELEASE_ROOT=/opt/momcozy-lab-staging
  SERVICE_ENV_FILE=/opt/momcozy-lab-staging/shared/agent/staging.env
  RELEASE_LOCK_PATH=/opt/momcozy-lab-staging/shared/staging-release.lock
production:
  RELEASE_ROOT=/opt/momcozy-lab-production
  SERVICE_ENV_FILE=/opt/momcozy-lab-production/shared/agent/production.env
  RELEASE_LOCK_PATH=/opt/momcozy-lab-production/shared/production-release.lock
both (environment-specific values):
  CA_FILE=/path/to/reviewed/ca.pem
  PUBLIC_URL=https://<agent-host>:8443
```

Copy only the values, not the labels. Use the same release root and lock path
as the Product Backend for the chosen target.

GitHub Environment secrets:

```text
SSH_HOST
SSH_PORT
SSH_USER
SSH_PRIVATE_KEY
SSH_KNOWN_HOSTS
```

`RELEASE_APPROVERS` is a repository variable; staging can reuse the existing
`STAGING_APPROVERS` variable and `STAGING_SSH_*` repository secrets when
environment-scoped values are absent. Keep GitHub Environment required reviewers
enabled, especially for production.

## Release safety sequence

`scripts/release.py` performs the following under the host lock:

1. verifies the requested immutable image revision;
2. validates the environment, private env permissions and Compose contract;
3. verifies the Product Backend release manifest has the same environment and
   matches the pinned Product OpenAPI hash;
4. pauses API admission and drains active Runs and context jobs;
5. backs up the Agent database before a required migration;
6. migrates, clears stale worker heartbeat, and starts the new API/worker pair;
7. checks the commit-specific heartbeat plus loopback and public readiness;
8. records both Agent and Product release identities in the promoted manifest.

Rollback changes application code only and requires explicit schema compatibility.
It never downgrades the database. If compatibility is uncertain, roll forward.

## Validation only

```bash
make agent-staging-config
make agent-production-config
make agent-test
make agent-lint
```

No Make target directly mutates a deployed environment.

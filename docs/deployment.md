# Agent Runtime deployment configuration

Updated: 2026-10-21. Deployment targets are `singapore-staging` and
`north-america-staging`; both run with `APP_ENV=staging`. `local` is for the
developer stack and `test` for ephemeral CI. No production target exists.

| Target | Env template | Compose | Source gate |
| --- | --- | --- | --- |
| Local | `env/local.env.example` | `docker-compose.local.yml` | Not published |
| Singapore staging | `env/staging.env.example` | `docker-compose.deploy.yml` | `agent-singapore-delivery.yml`, verified `main` image |
| North America staging | `env/us-east-uat.env.example` | `docker-compose.us-east-uat.yml` | `agent-north-america-validation.yml` publishes on `main`; Product-owned `backend-north-america-delivery.yml` handles separately approved, manual unified host release |

Product Backend must be healthy first. It owns the shared PostgreSQL, Redis,
MinIO and network; Agent owns only its own database, worker/API, output bucket
and manifest. The two regional targets have distinct private env files,
credentials, locks, volumes and public origins. Do not cross-load their
settings or deploy from a development branch. `docker-compose.ci.yml` is
never installed on a host.

Copy exactly one regional template into a mode-`0600` private host file and
replace every placeholder. `ZOUNDREAM_ENDPOINT` and `ZOUNDREAM_API_KEY` are
required for the read-only baby-cry tool in **each** region; no key is committed.
The release preflight rejects missing cry configuration. Image digest and
`RUNTIME_RELEASE_ID` are release-owned, never copied into the private env.

Local entrypoint: `cd ../app && make local-dev-up`. Static Singapore Compose
check: `make agent-staging-config`. North America private-env admission:
`python scripts/check_north_america_env.py --env-file <private-env>`.
These checks do not call the cry vendor, prove live provider inference, or
authorize deployment. Verify immutable image provenance, schema compatibility,
backups and isolated restore, Product/Agent contracts, worker heartbeat and
public HTTPS readiness before a regional release or rollback.

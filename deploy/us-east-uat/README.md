# North America staging — Agent Runtime host handoff

Updated: 2026-10-20. `APP_ENV=staging`; deployment target is
`north-america-staging`. Source and image identity must come from a clean,
validated `main` commit and immutable digest. The successful
`agent-north-america-validation.yml` image job does not deploy to a host.

The regional host's physical root is `/opt/momcozy-lab-us-east-uat`. Product
Backend owns shared PostgreSQL, Redis, MinIO, network and TLS ingress. Agent
uses `docker-compose.us-east-uat.yml`, a separate database/role, Redis identity,
output bucket and mode-`0600` private env derived from
`env/us-east-uat.env.example`. The private target declaration is based on
`config/release-targets/north-america-staging.json.example`. Never load
Singapore's env or invoke its delivery workflow against this host.

The Agent private env must include `ZOUNDREAM_ENDPOINT` and
`ZOUNDREAM_API_KEY` to enable the baby-cry tool. Check structure with
`python scripts/check_north_america_env.py --env-file <private-agent-env>`;
`scripts/north_america_release.py` is read-only admission, not an activation
command. Require Product readiness and matching contracts, image provenance,
backups/isolated restore, current schema, worker heartbeat and public HTTPS
before any separate authorized release/rollback action. A static check is not
evidence of vendor inference or deployment. Recheck actual host state; this
file makes no historical or live-service status claim.

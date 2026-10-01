from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_staging_runtime_trusts_public_backend_without_disabling_tls() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text()
    template = (ROOT / "env/staging.env.example").read_text()
    assert "MOMCOZY_AGENT_TRUST_BUNDLE_FILE" in compose
    ci = (ROOT / ".github/workflows/agent-ci.yml").read_text()
    dockerfile = (ROOT / "deploy/shared/Minio.Dockerfile").read_text()
    assert "Build the pinned community MinIO image" in ci
    assert "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" in dockerfile
    assert "SSL_CERT_FILE: /run/momcozy/trust-bundle.pem" in compose
    assert "MOMCOZY_AGENT_TRUST_BUNDLE_FILE=/opt/momcozy-lab-staging/shared/trust-bundle.pem" in template
    assert "PRODUCT_BACKEND_BASE_URL=https://backend-test.lute-momcozylab.luteos.cloud:8443" in template
    assert "AUTH_JWKS_URL=https://backend-test.lute-momcozylab.luteos.cloud:8443/.well-known/jwks.json" in template


def test_b_uses_system_ca_without_reusing_a_internal_ca() -> None:
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    template = (ROOT / "env/us-east-uat.env.example").read_text()
    assert "MOMCOZY_AGENT_TRUST_BUNDLE_FILE=/etc/ssl/certs/ca-certificates.crt" in template
    assert "SSL_CERT_FILE: /run/momcozy/trust-bundle.pem" in compose
    assert "${MOMCOZY_AGENT_TRUST_BUNDLE_FILE:-/etc/ssl/certs/ca-certificates.crt}" in compose

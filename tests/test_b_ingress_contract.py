"""B Agent ingress must preserve SSE without sharing the A TLS endpoint."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy/us-east-uat/nginx-agent.conf"


def test_b_agent_ingress_preserves_sse_and_isolation() -> None:
    text = SITE.read_text()
    host = "agent-us-dev.lute-momcozylab.luteos.cloud"
    assert f"server_name {host};" in text
    assert "listen 443 ssl" in text
    assert "127.0.0.1:8102" in text
    assert f"/etc/letsencrypt/live/{host}/fullchain.pem" in text
    assert f"/etc/letsencrypt/live/{host}/privkey.pem" in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in text
    assert "location ~ ^/v1/agent/runs/[^/]+/stream$" in text
    assert "proxy_buffering off;" in text
    assert "gzip off;" in text
    assert "add_header X-Accel-Buffering no always;" in text
    for forbidden in (
        "backend-test.lute-momcozylab", "agent-test.lute-momcozylab",
        "/opt/momcozy-lab-staging", "listen 8443", "0.0.0.0:8102",
        "ssl_verify off", "ssl_certificate /etc/nginx/tls/momcozy-lab-staging",
    ):
        assert forbidden not in text

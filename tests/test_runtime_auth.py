from __future__ import annotations
from unittest.mock import AsyncMock

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.dependencies import (
    authenticate_runtime_principal,
    require_agent_run_principal,
)
from app.auth import JwksCache, RuntimePrincipal, RuntimeTokenAuthenticator
from app.auth.jwks import JwksUnavailableError
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app


def _base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


ISSUER = "https://identity.momcozy.test"
AUDIENCE = "momcozy-agent-runtime"
KEY_ID = "runtime-auth-key-1"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_JWK = {
    "kty": "RSA",
    "use": "sig",
    "alg": "RS256",
    "kid": KEY_ID,
    "n": _base64url_uint(PRIVATE_KEY.public_key().public_numbers().n),
    "e": _base64url_uint(PRIVATE_KEY.public_key().public_numbers().e),
}


def test_valid_rs256_token_returns_runtime_principal_derived_from_subject() -> None:
    user_id = uuid4()
    session_id = uuid4()
    authenticator, fetcher = _authenticator()

    principal = asyncio.run(
        authenticator.authenticate(
            _token(
                sub=str(user_id),
                sid=str(session_id),
                roles=["user"],
                permissions=["agent:run"],
            )
        )
    )

    assert principal.user_id == user_id
    assert principal.subject == str(user_id)
    assert principal.session_id == session_id
    assert principal.token_id == "access-token-1"
    assert principal.token_version == 1
    assert principal.roles == frozenset({"user"})
    assert principal.permissions == frozenset({"agent:run"})
    assert principal.issued_at is not None
    assert principal.expires_at is not None
    assert principal.issued_at < principal.expires_at
    assert RuntimePrincipal.from_authorization_context(
        principal.authorization_context()
    ) == principal
    assert fetcher.calls == 1


@pytest.mark.parametrize(
    "mutate",
    (
        lambda context: context.update({"unexpected": True}),
        lambda context: context.update(
            {"permissions": ["agent:run", "agent:run"]}
        ),
        lambda context: context.update(
            {"permissions": ["profile:read", "agent:run"]}
        ),
    ),
)
def test_authorization_snapshot_rejects_non_canonical_payload(
    mutate: Any,
) -> None:
    user_id = uuid4()
    context = RuntimePrincipal(
        user_id=user_id,
        subject=str(user_id),
        session_id=uuid4(),
        token_id="token-id",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset({"agent:run"}),
    ).authorization_context()
    mutate(context)

    with pytest.raises(ValueError, match="Authorization context"):
        RuntimePrincipal.from_authorization_context(context)


@pytest.mark.parametrize(
    ("claim", "value"),
    (
        ("iss", "https://wrong-issuer.test"),
        ("aud", "wrong-audience"),
        ("aud", ["momcozy-product-api"]),
        ("sub", "provider-subject"),
        ("sid", "not-a-uuid"),
        ("jti", ""),
        ("token_version", 2),
        ("iat", "not-a-number"),
        ("roles", None),
        ("roles", "user"),
        ("roles", ["user", 1]),
        ("permissions", None),
        ("permissions", "agent:run"),
        ("permissions", ["agent:run", False]),
    ),
)
def test_invalid_identity_claims_are_rejected(
    claim: str,
    value: Any,
) -> None:
    authenticator, _fetcher = _authenticator()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(authenticator.authenticate(_token(**{claim: value})))

    assert exc_info.value.status == 401
    assert exc_info.value.code == "authentication_required"


@pytest.mark.parametrize(
    "missing_claim",
    (
        "iss",
        "aud",
        "sub",
        "exp",
        "sid",
        "jti",
        "iat",
        "token_version",
        "roles",
        "permissions",
    ),
)
def test_every_required_claim_is_enforced(missing_claim: str) -> None:
    authenticator, _fetcher = _authenticator()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            authenticator.authenticate(
                _token(remove_claim=missing_claim)
            )
        )

    assert exc_info.value.status == 401
    assert exc_info.value.code == "authentication_required"


def test_expired_token_is_rejected() -> None:
    authenticator, _fetcher = _authenticator()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            authenticator.authenticate(
                _token(exp=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
        )

    assert exc_info.value.status == 401


def test_algorithm_confusion_is_rejected_before_any_jwks_fetch() -> None:
    authenticator, fetcher = _authenticator()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        _claims(now=now),
        "shared-secret-that-runtime-must-never-accept",
        algorithm="HS256",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(authenticator.authenticate(token))

    assert exc_info.value.status == 401
    assert fetcher.calls == 0


def test_non_access_token_type_is_rejected_before_any_jwks_fetch() -> None:
    authenticator, fetcher = _authenticator()
    token = _token(
        headers={"kid": KEY_ID, "typ": "application/other+jwt"}
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(authenticator.authenticate(token))

    assert exc_info.value.status == 401
    assert fetcher.calls == 0


@pytest.mark.parametrize(
    "kid",
    (
        " key-with-whitespace",
        "key-with-whitespace ",
        "x" * 129,
    ),
)
def test_malformed_kid_is_a_401_and_never_triggers_jwks_fetch(
    kid: str,
) -> None:
    authenticator, fetcher = _authenticator()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            authenticator.authenticate(
                _token(headers={"kid": kid})
            )
        )

    assert exc_info.value.status == 401
    assert exc_info.value.code == "authentication_required"
    assert fetcher.calls == 0


def test_untrusted_jku_and_x5u_headers_are_ignored() -> None:
    authenticator, fetcher = _authenticator()

    principal = asyncio.run(
        authenticator.authenticate(
            _token(
                headers={
                    "kid": KEY_ID,
                    "jku": "http://169.254.169.254/latest/meta-data",
                    "x5u": "https://attacker.test/key.pem",
                }
            )
        )
    )

    assert principal.user_id
    assert fetcher.calls == 1


def test_unknown_kid_after_successful_refresh_is_an_authentication_error() -> None:
    authenticator, _fetcher = _authenticator()

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            authenticator.authenticate(
                _token(headers={"kid": "unknown-key"})
            )
        )

    assert exc_info.value.status == 401
    assert exc_info.value.code == "authentication_required"


def test_jwks_unavailability_without_a_usable_key_is_a_retryable_service_error() -> None:
    fetcher = FakeFetcher(JwksUnavailableError("upstream unavailable"))
    authenticator = RuntimeTokenAuthenticator(
        jwks_cache=JwksCache(
            fetcher=fetcher,
            cache_ttl_seconds=300,
            kid_miss_cooldown_seconds=30,
        ),
        issuer=ISSUER,
        audience=AUDIENCE,
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(authenticator.authenticate(_token()))

    assert exc_info.value.status == 503
    assert exc_info.value.code == "authentication_keys_unavailable"
    assert exc_info.value.details == {"retryable": True}


def test_bearer_dependency_returns_principal_and_never_accepts_body_identity() -> None:
    expected = RuntimePrincipal(
        user_id=uuid4(),
        subject="subject",
        session_id=uuid4(),
        token_id="token-id",
        token_version=1,
        roles=frozenset(),
        permissions=frozenset(),
    )
    app = _app_with_protected_endpoint()
    app.state.product_backend_client = AsyncMock()
    app.state.runtime_authenticator = FakeAuthenticator(expected)

    response = TestClient(app).post(
        "/test/protected",
        headers={"Authorization": "Bearer signed-token"},
        json={"actor_user_id": str(uuid4())},
    )

    assert response.status_code == 200
    assert response.json() == {"owner_user_id": str(expected.user_id)}


def test_agent_run_dependency_requires_explicit_permission() -> None:
    principal = RuntimePrincipal(
        user_id=uuid4(),
        subject="subject",
        session_id=uuid4(),
        token_id="token-id",
        token_version=1,
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
    app = _app_with_protected_endpoint(
        dependency=require_agent_run_principal
    )
    app.state.product_backend_client = AsyncMock()
    app.state.runtime_authenticator = FakeAuthenticator(principal)

    response = TestClient(app).post(
        "/test/protected",
        headers={"Authorization": "Bearer signed-token"},
        json={},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.parametrize(
    "authorization",
    (None, "Basic credentials", "Bearer"),
)
def test_bearer_dependency_returns_safe_401_contract(
    authorization: str | None,
) -> None:
    app = _app_with_protected_endpoint()
    headers = {"Authorization": authorization} if authorization else {}

    response = TestClient(app).post(
        "/test/protected",
        headers=headers,
        json={},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"
    response_body = json.dumps(response.json()).lower()
    assert "signed-token" not in response_body
    assert "credentials" not in response_body


class FakeFetcher:
    def __init__(self, outcome: bytes | Exception) -> None:
        self._outcome = outcome
        self.calls = 0

    async def fetch(self) -> bytes:
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class FakeAuthenticator:
    def __init__(self, principal: RuntimePrincipal) -> None:
        self._principal = principal

    async def authenticate(self, _token_value: str) -> RuntimePrincipal:
        return self._principal


def _authenticator() -> tuple[RuntimeTokenAuthenticator, FakeFetcher]:
    fetcher = FakeFetcher(
        json.dumps({"keys": [PUBLIC_JWK]}, separators=(",", ":")).encode()
    )
    cache = JwksCache(
        fetcher=fetcher,
        cache_ttl_seconds=300,
        kid_miss_cooldown_seconds=30,
    )
    return (
        RuntimeTokenAuthenticator(
            jwks_cache=cache,
            issuer=ISSUER,
            audience=AUDIENCE,
        ),
        fetcher,
    )


def _token(
    *,
    remove_claim: str | None = None,
    headers: dict[str, Any] | None = None,
    **overrides: Any,
) -> str:
    now = datetime.now(timezone.utc)
    claims = _claims(now=now)
    claims.update(overrides)
    if remove_claim:
        claims.pop(remove_claim)
    return jwt.encode(
        claims,
        PRIVATE_KEY,
        algorithm="RS256",
        headers=headers or {"kid": KEY_ID},
    )


def _claims(*, now: datetime) -> dict[str, Any]:
    return {
        "iss": ISSUER,
        "aud": ["momcozy-product-api", AUDIENCE],
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": "access-token-1",
        "token_version": 1,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "roles": [],
        "permissions": [],
    }


def _app_with_protected_endpoint(
    *,
    dependency: Any = authenticate_runtime_principal,
) -> Any:
    app = create_app(
        Settings(
            app_env="test",
            product_backend_service_key="agent-runtime-test-service-key-32-bytes",
        )
    )

    @app.post("/test/protected")
    async def protected(
        principal: RuntimePrincipal = Depends(dependency),
    ) -> dict[str, str]:
        return {"owner_user_id": str(principal.user_id)}

    return app

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import jwt
from jwt import InvalidTokenError

from app.agent_runtime.runtime_metadata import AUTH_TOKEN_VERSION
from app.core.errors import ApiError

from .jwks import (
    MAX_KID_LENGTH,
    JwksCache,
    JwksUnavailableError,
    UnknownSigningKeyError,
)
from .principal import RuntimePrincipal


ALGORITHM = "RS256"
TOKEN_VERSION = AUTH_TOKEN_VERSION
MAX_TOKEN_ID_LENGTH = 255
MAX_AUTHORITY_VALUES = 64
MAX_AUTHORITY_VALUE_LENGTH = 128
REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "sid",
    "jti",
    "token_version",
    "iat",
    "exp",
    "roles",
    "permissions",
)


class RuntimeTokenAuthenticator:
    def __init__(
        self,
        *,
        jwks_cache: JwksCache,
        issuer: str,
        audience: str,
    ) -> None:
        self._jwks_cache = jwks_cache
        self._issuer = issuer
        self._audience = audience

    async def authenticate(self, token: str) -> RuntimePrincipal:
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise _authentication_required() from exc

        if (
            header.get("alg") != ALGORITHM
            or header.get("typ") != "JWT"
        ):
            raise _authentication_required()
        kid = header.get("kid")
        if (
            not isinstance(kid, str)
            or not kid
            or kid != kid.strip()
            or len(kid) > MAX_KID_LENGTH
        ):
            raise _authentication_required()

        try:
            signing_key = await self._jwks_cache.get_signing_key(kid)
        except UnknownSigningKeyError as exc:
            raise _authentication_required() from exc
        except JwksUnavailableError as exc:
            raise ApiError(
                code="authentication_keys_unavailable",
                message="Authentication keys are temporarily unavailable.",
                status=503,
                details={"retryable": True},
            ) from exc

        try:
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": list(REQUIRED_CLAIMS),
                },
            )
            return _principal_from_payload(payload)
        except (InvalidTokenError, TypeError, ValueError) as exc:
            raise _authentication_required() from exc


def _principal_from_payload(payload: dict[str, Any]) -> RuntimePrincipal:
    subject = _required_string(payload.get("sub"))
    session_id = UUID(_required_string(payload.get("sid")))
    token_id = _required_string(payload.get("jti"))
    if len(token_id) > MAX_TOKEN_ID_LENGTH:
        raise ValueError("Token ID is too long.")
    token_version = payload.get("token_version")
    if type(token_version) is not int or token_version != TOKEN_VERSION:
        raise ValueError("Token version is invalid.")
    issued_at = _numeric_date(payload.get("iat"))
    expires_at = _numeric_date(payload.get("exp"))
    return RuntimePrincipal(
        user_id=UUID(subject),
        subject=subject,
        session_id=session_id,
        token_id=token_id,
        token_version=token_version,
        roles=_authority_values(payload.get("roles")),
        permissions=_authority_values(payload.get("permissions")),
        issued_at=datetime.fromtimestamp(issued_at, tz=timezone.utc),
        expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
    )


def _required_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Required token string is invalid.")
    return value.strip()


def _numeric_date(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("Token timestamp is invalid.")
    return float(value)


def _authority_values(value: Any) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > MAX_AUTHORITY_VALUES:
        raise ValueError("Token authorities are invalid.")
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Token authorities are invalid.")
        authority = item.strip()
        if (
            not authority
            or len(authority) > MAX_AUTHORITY_VALUE_LENGTH
        ):
            raise ValueError("Token authorities are invalid.")
        normalized.add(authority)
    return frozenset(normalized)


def _authentication_required() -> ApiError:
    return ApiError(
        code="authentication_required",
        message="Bearer access token is invalid.",
        status=401,
        headers={"WWW-Authenticate": "Bearer"},
    )

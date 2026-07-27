from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import RuntimePrincipal, RuntimeTokenAuthenticator
from app.core.errors import ApiError


bearer_scheme = HTTPBearer(auto_error=False)


async def require_runtime_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> RuntimePrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(
            code="authentication_required",
            message="Bearer access token is required.",
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    authenticator: RuntimeTokenAuthenticator = (
        request.app.state.runtime_authenticator
    )
    return await authenticator.authenticate(credentials.credentials)

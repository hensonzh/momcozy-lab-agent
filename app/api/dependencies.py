from __future__ import annotations

from hmac import compare_digest

from fastapi import Depends, Request, Security
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.auth import (
    RuntimeAdminPrincipal,
    RuntimePrincipal,
    RuntimeTokenAuthenticator,
)
from app.core.errors import ApiError


bearer_scheme = HTTPBearer(auto_error=False)
runtime_admin_service_key_scheme = APIKeyHeader(
    name="X-Service-Key",
    scheme_name="RuntimeAdminServiceKey",
    description=(
        "Dedicated Agent Runtime operator credential for replay and eval "
        "administration."
    ),
    auto_error=False,
)
RUNTIME_ADMIN_SERVICE_NAME = "agent-runtime-operator"


async def authenticate_runtime_principal(
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
    principal = await authenticator.authenticate(credentials.credentials)
    client = getattr(request.app.state, "product_backend_client", None)
    if client is None:
        raise ApiError(code="authentication_unavailable", message="Account verification is temporarily unavailable.", status=503)
    await client.require_active_account(access_token=credentials.credentials, user_id=str(principal.user_id))
    return principal


async def require_runtime_principal(
    principal: RuntimePrincipal = Depends(authenticate_runtime_principal),
) -> RuntimePrincipal:
    if "agent:run" not in principal.permissions:
        raise ApiError(
            code="permission_denied",
            message="Agent Runtime permission is required.",
            status=403,
        )
    return principal


require_agent_run_principal = require_runtime_principal


async def require_runtime_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
    service_key: str | None = Security(
        runtime_admin_service_key_scheme
    ),
) -> RuntimeAdminPrincipal:
    if service_key is not None:
        configured_key = request.app.state.settings.runtime_admin_service_key
        if not configured_key or not compare_digest(
            service_key,
            configured_key,
        ):
            raise ApiError(
                code="invalid_service_key",
                message="Runtime administrator service key is invalid.",
                status=401,
                headers={"WWW-Authenticate": "ServiceKey"},
            )
        return RuntimeAdminPrincipal(
            actor_user_id=None,
            actor_service=RUNTIME_ADMIN_SERVICE_NAME,
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(
            code="authentication_required",
            message=(
                "Bearer administrator token or Runtime administrator "
                "service key is required."
            ),
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = await authenticate_runtime_principal(request, credentials)
    if (
        "admin" not in principal.roles
        and "agent:admin" not in principal.permissions
        and "agent.runtime.admin" not in principal.permissions
    ):
        raise ApiError(
            code="permission_denied",
            message="Agent Runtime administrator permission is required.",
            status=403,
        )
    return RuntimeAdminPrincipal(
        actor_user_id=principal.user_id,
        actor_service="",
    )

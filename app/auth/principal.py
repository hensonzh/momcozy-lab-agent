from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from app.agent_runtime.runtime_metadata import (
    AUTHORIZATION_CONTEXT_SCHEMA_VERSION,
    AUTH_TOKEN_VERSION,
)

_REQUIRED_AUTHORIZATION_CONTEXT_FIELDS = frozenset(
    {
        "schema_version",
        "user_id",
        "subject",
        "session_id",
        "token_id",
        "token_version",
        "roles",
        "permissions",
    }
)
_OPTIONAL_AUTHORIZATION_CONTEXT_FIELDS = frozenset(
    {"issued_at", "expires_at"}
)


@dataclass(frozen=True)
class RuntimePrincipal:
    user_id: UUID
    subject: str
    session_id: UUID
    token_id: str
    token_version: int
    roles: frozenset[str]
    permissions: frozenset[str]
    issued_at: datetime | None = None
    expires_at: datetime | None = None

    def authorization_context(self) -> dict[str, Any]:
        context: dict[str, Any] = {
            "schema_version": AUTHORIZATION_CONTEXT_SCHEMA_VERSION,
            "user_id": str(self.user_id),
            "subject": self.subject,
            "session_id": str(self.session_id),
            "token_id": self.token_id,
            "token_version": self.token_version,
            "roles": sorted(self.roles),
            "permissions": sorted(self.permissions),
        }
        if self.issued_at is not None:
            context["issued_at"] = self.issued_at.isoformat()
        if self.expires_at is not None:
            context["expires_at"] = self.expires_at.isoformat()
        return context

    @classmethod
    def from_authorization_context(
        cls,
        value: Mapping[str, Any],
    ) -> "RuntimePrincipal":
        fields = set(value)
        if (
            not _REQUIRED_AUTHORIZATION_CONTEXT_FIELDS <= fields
            or fields
            - _REQUIRED_AUTHORIZATION_CONTEXT_FIELDS
            - _OPTIONAL_AUTHORIZATION_CONTEXT_FIELDS
        ):
            raise ValueError("Authorization context fields are invalid.")
        if value.get("schema_version") != AUTHORIZATION_CONTEXT_SCHEMA_VERSION:
            raise ValueError("Authorization context schema is invalid.")
        user_id = UUID(_required_string(value, "user_id"))
        subject = _required_string(value, "subject")
        if UUID(subject) != user_id:
            raise ValueError("Authorization context subject is invalid.")
        token_version = value.get("token_version")
        if (
            type(token_version) is not int
            or token_version != AUTH_TOKEN_VERSION
        ):
            raise ValueError("Authorization context token version is invalid.")
        return cls(
            user_id=user_id,
            subject=subject,
            session_id=UUID(_required_string(value, "session_id")),
            token_id=_required_string(value, "token_id"),
            token_version=token_version,
            roles=_authority_values(value, "roles"),
            permissions=_authority_values(value, "permissions"),
            issued_at=_optional_datetime(value.get("issued_at")),
            expires_at=_optional_datetime(value.get("expires_at")),
        )


@dataclass(frozen=True)
class RuntimeAdminPrincipal:
    actor_user_id: UUID | None
    actor_service: str


def _required_string(value: Mapping[str, Any], field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Authorization context {field} is invalid.")
    return raw.strip()


def _authority_values(
    value: Mapping[str, Any],
    field: str,
) -> frozenset[str]:
    raw = value.get(field)
    if not isinstance(raw, list) or len(raw) > 64:
        raise ValueError(f"Authorization context {field} is invalid.")
    normalized: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise ValueError(f"Authorization context {field} is invalid.")
        normalized.add(item.strip())
    if raw != sorted(normalized):
        raise ValueError(f"Authorization context {field} is not canonical.")
    return frozenset(normalized)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Authorization context timestamp is invalid.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Authorization context timestamp is invalid.")
    return parsed

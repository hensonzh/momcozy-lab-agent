from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RuntimePrincipal:
    user_id: UUID
    subject: str
    session_id: UUID
    token_id: str
    token_version: int
    roles: frozenset[str]
    permissions: frozenset[str]

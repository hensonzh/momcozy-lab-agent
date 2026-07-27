from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_actor_service", "actor_service", "created_at"),
        Index("ix_audit_logs_request_id", "request_id"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(32),
        default="user",
        server_default="user",
        nullable=False,
    )
    actor_service: Mapped[str] = mapped_column(
        String(120),
        default="",
        server_default="",
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[str] = mapped_column(
        String(120),
        default="",
        server_default="",
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(
        String(80),
        default="",
        server_default="",
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(
        String(32),
        default="succeeded",
        server_default="succeeded",
        nullable=False,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        "details_json",
        postgresql.JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "scope",
            "key",
            name="uq_idempotency_actor_scope_key",
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    response_ref: Mapped[str] = mapped_column(
        String(512),
        default="",
        server_default="",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="in_progress",
        server_default="in_progress",
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

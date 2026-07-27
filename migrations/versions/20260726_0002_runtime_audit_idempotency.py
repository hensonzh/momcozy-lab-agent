"""Add Runtime-owned audit and idempotency records."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0002"
down_revision: str | None = "20260726_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "actor_type",
            sa.String(length=32),
            server_default="user",
            nullable=False,
        ),
        sa.Column(
            "actor_service",
            sa.String(length=120),
            server_default="",
            nullable=False,
        ),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=120), nullable=False),
        sa.Column(
            "resource_id",
            sa.String(length=120),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.String(length=80),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.String(length=32),
            server_default="succeeded",
            nullable=False,
        ),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_actor_created",
        "audit_logs",
        ["actor_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_actor_service",
        "audit_logs",
        ["actor_service", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_request_id",
        "audit_logs",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
        unique=False,
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "response_ref",
            sa.String(length=512),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="in_progress",
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_idempotency_keys"),
        ),
        sa.UniqueConstraint(
            "actor_user_id",
            "scope",
            "key",
            name="uq_idempotency_actor_scope_key",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_expires_at",
        "idempotency_keys",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_idempotency_keys_expires_at",
        table_name="idempotency_keys",
    )
    op.drop_table("idempotency_keys")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_service", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")

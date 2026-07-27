"""Persist canonical tool outputs and replayable action results."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260727_0003"
down_revision: str | None = "20260726_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "service_skill_id",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_agent_runs_service_skill_id",
        "agent_runs",
        ["service_skill_id"],
        unique=False,
    )
    op.alter_column(
        "agent_tool_outputs",
        "safe_output_json",
        new_column_name="output_json",
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )
    op.alter_column(
        "agent_tool_outputs",
        "raw_output_ref",
        new_column_name="output_ref",
        existing_type=sa.String(length=512),
        existing_nullable=False,
    )
    op.add_column(
        "agent_actions",
        sa.Column(
            "result_payload_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_actions", "result_payload_json")
    op.alter_column(
        "agent_tool_outputs",
        "output_ref",
        new_column_name="raw_output_ref",
        existing_type=sa.String(length=512),
        existing_nullable=False,
    )
    op.drop_index(
        "ix_agent_runs_service_skill_id",
        table_name="agent_runs",
    )
    op.drop_column("agent_runs", "service_skill_id")
    op.alter_column(
        "agent_tool_outputs",
        "output_json",
        new_column_name="safe_output_json",
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )

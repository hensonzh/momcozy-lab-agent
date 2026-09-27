"""Record Agent run notification handoff without replaying historical runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260926_0003"
down_revision = "20260916_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_agent_runs_completed_notification", "agent_runs", ["completed_at", "id"],
        postgresql_where=sa.text("status = 'completed'"))
    op.create_table("agent_notification_receipts",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
    )
    # A release must not produce notifications for already-completed conversations.
    op.execute("""INSERT INTO agent_notification_receipts (run_id, delivered_at)
        SELECT id, CURRENT_TIMESTAMP FROM agent_runs WHERE status = 'completed'""")


def downgrade() -> None:
    op.drop_table("agent_notification_receipts")
    op.drop_index("ix_agent_runs_completed_notification", table_name="agent_runs")

"""Keep per-Run stool follow-up references outside model/compaction state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "20261022_0004"
down_revision = "20260926_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("stool_analysis_id", pg.UUID(as_uuid=True), nullable=True))
    op.add_column("agent_runs", sa.Column("stool_baby_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_check_constraint("ck_agent_runs_stool_followup_pair", "agent_runs",
                               "(stool_analysis_id IS NULL) = (stool_baby_id IS NULL)")


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM agent_runs WHERE stool_analysis_id IS NOT NULL)" )).scalar():
        raise RuntimeError("Stool follow-up Runs exist; preserve their bindings and roll forward.")
    op.drop_constraint("ck_agent_runs_stool_followup_pair", "agent_runs", type_="check")
    op.drop_column("agent_runs", "stool_baby_id")
    op.drop_column("agent_runs", "stool_analysis_id")

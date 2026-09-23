"""Remove the unique index specific to the retired prenatal workflow."""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_agent_workflow_states_owner_type_active", table_name="agent_workflow_states")


def downgrade() -> None:
    op.create_index("uq_agent_workflow_states_owner_type_active", "agent_workflow_states", ["owner_user_id", "workflow_type"], unique=True,
        postgresql_where=sa.text("workflow_type = 'pregnancy_plan' AND status IN ('collecting', 'ready', 'waiting', 'paused')"))

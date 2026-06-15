"""Add pipeline_type, priority, tags, department, description to deals; created_by_id to activities."""

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def upgrade() -> None:
    # ── deals table ──────────────────────────────────────────────────────────
    op.add_column("deals", sa.Column("pipeline_type", sa.String(), nullable=False, server_default="deal"))
    op.add_column("deals", sa.Column("priority", sa.String(), nullable=False, server_default="normal"))
    op.add_column("deals", sa.Column("tags", JSONB(), nullable=False, server_default="[]"))
    op.add_column("deals", sa.Column("department", sa.String(), nullable=True))
    op.add_column("deals", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("deals", sa.Column("assigned_to_id", sa.Uuid(), nullable=True))

    op.create_index("ix_deals_pipeline_type", "deals", ["pipeline_type"])
    op.create_index("ix_deals_assigned_to_id", "deals", ["assigned_to_id"])
    op.create_foreign_key("fk_deals_assigned_to_id", "deals", "users", ["assigned_to_id"], ["id"])

    # Migrate existing stages to the new deal stage vocabulary
    op.execute("""
        UPDATE deals SET stage = CASE stage
            WHEN 'discovery' THEN 'open'
            WHEN 'demo' THEN 'demo_scheduled'
            WHEN 'poc' THEN 'poc_agreed'
            WHEN 'proposal' THEN 'commercial_negotiation'
            WHEN 'negotiation' THEN 'commercial_negotiation'
            ELSE stage
        END
    """)

    # ── activities table ─────────────────────────────────────────────────────
    op.add_column("activities", sa.Column("created_by_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_activities_created_by_id", "activities", "users", ["created_by_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_activities_created_by_id", "activities", type_="foreignkey")
    op.drop_column("activities", "created_by_id")

    op.drop_constraint("fk_deals_assigned_to_id", "deals", type_="foreignkey")
    op.drop_index("ix_deals_assigned_to_id", table_name="deals")
    op.drop_index("ix_deals_pipeline_type", table_name="deals")
    op.drop_column("deals", "assigned_to_id")
    op.drop_column("deals", "description")
    op.drop_column("deals", "department")
    op.drop_column("deals", "tags")
    op.drop_column("deals", "priority")
    op.drop_column("deals", "pipeline_type")

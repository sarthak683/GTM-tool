"""Add structured assignment updates for execution tracking."""

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.create_table(
        "assignment_updates",
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_role", sa.String(), nullable=False),
        sa.Column("progress_state", sa.String(), nullable=False),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("buyer_signal", sa.String(), nullable=False),
        sa.Column("blocker_type", sa.String(), nullable=False),
        sa.Column("last_touch_type", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("next_step_due_date", sa.Date(), nullable=True),
        sa.Column("blocker_detail", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("entity_name_snapshot", sa.String(), nullable=True),
        sa.Column("company_name_snapshot", sa.String(), nullable=True),
        sa.Column("assignee_name_snapshot", sa.String(), nullable=True),
        sa.Column("assignee_email_snapshot", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assignment_updates_assignee_id", "assignment_updates", ["assignee_id"])
    op.create_index("ix_assignment_updates_created_at", "assignment_updates", ["created_at"])
    op.create_index("ix_assignment_updates_created_by_id", "assignment_updates", ["created_by_id"])
    op.create_index("ix_assignment_updates_entity_id", "assignment_updates", ["entity_id"])
    op.create_index("ix_assignment_updates_entity_type", "assignment_updates", ["entity_type"])
    op.create_index("ix_assignment_updates_assignment_role", "assignment_updates", ["assignment_role"])
    op.create_index("ix_assignment_updates_next_step_due_date", "assignment_updates", ["next_step_due_date"])
    op.create_index(
        "ix_assignment_updates_lookup",
        "assignment_updates",
        ["entity_type", "entity_id", "assignment_role", "assignee_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assignment_updates_lookup", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_next_step_due_date", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_assignment_role", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_entity_type", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_entity_id", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_created_by_id", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_created_at", table_name="assignment_updates")
    op.drop_index("ix_assignment_updates_assignee_id", table_name="assignment_updates")
    op.drop_table("assignment_updates")

"""Add role-first task ownership

Revision ID: 033
Revises: 032
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("assigned_role", sa.String(), nullable=True))
    op.create_index("ix_tasks_assigned_role", "tasks", ["assigned_role"], unique=False)

    op.execute(
        """
        UPDATE tasks
        SET assigned_role = CASE
            WHEN entity_type = 'deal' THEN 'ae'
            ELSE 'sdr'
        END
        WHERE assigned_role IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_assigned_role", table_name="tasks")
    op.drop_column("tasks", "assigned_role")

"""Add task_track column for sales_ai / hygiene / critical categorisation

Revision ID: 063
Revises: 049
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa


revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("task_track", sa.String(), nullable=True))
    op.create_index("ix_tasks_task_track", "tasks", ["task_track"], unique=False)

    # Everything that exists today was emitted by the old rules engine — none
    # of those map cleanly to the 6 new sales-AI codes. Park them all as
    # "hygiene" so the AE queue stays clean; the new emitter will fill the
    # sales_ai / critical tracks on the next refresh.
    op.execute(
        """
        UPDATE tasks
        SET task_track = CASE
            WHEN task_type = 'manual' THEN 'manual'
            ELSE 'hygiene'
        END
        WHERE task_track IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_task_track", table_name="tasks")
    op.drop_column("tasks", "task_track")

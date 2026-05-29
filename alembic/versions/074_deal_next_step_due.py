"""Add next_step_due_at column to deals.

Revision ID: 074
Revises: 073
Create Date: 2026-05-29

Stores the due time for a deal's next step. The pipeline reminder Celery task
queries this column to fire a one-time in-app notification to the assigned rep
when the next step is due/overdue, so it is indexed.
"""

from alembic import op
import sqlalchemy as sa


revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column(
            "next_step_due_at",
            sa.DateTime(),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_deals_next_step_due_at"),
        "deals",
        ["next_step_due_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_deals_next_step_due_at"),
        table_name="deals",
    )
    op.drop_column("deals", "next_step_due_at")

"""Add deal AI task refresh metadata

Revision ID: 064
Revises: 050
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa


revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("ai_tasks_refreshed_at", sa.DateTime(), nullable=True))
    op.add_column("deals", sa.Column("ai_tasks_input_hash", sa.String(), nullable=True))
    op.add_column("deals", sa.Column("ai_tasks_refresh_requested_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "ai_tasks_refresh_requested_at")
    op.drop_column("deals", "ai_tasks_input_hash")
    op.drop_column("deals", "ai_tasks_refreshed_at")

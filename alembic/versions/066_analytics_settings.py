"""Add analytics_settings JSONB to workspace_settings.

Revision ID: 066
Revises: 052
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("analytics_settings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "analytics_settings")

"""add zippy_system_prompt column to workspace_settings

Revision ID: 051
Revises: 050
Create Date: 2026-04-21
"""

from alembic import op
import sqlalchemy as sa


revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column("zippy_system_prompt", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "zippy_system_prompt")

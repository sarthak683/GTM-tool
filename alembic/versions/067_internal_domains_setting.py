"""Add internal_domains JSONB to workspace_settings.

Revision ID: 067
Revises: 053
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_settings",
        sa.Column(
            "internal_domains",
            sa.JSON(),
            nullable=False,
            server_default='["beacon.li"]',
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_settings", "internal_domains")

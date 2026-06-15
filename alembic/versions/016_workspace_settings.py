"""workspace_settings table

Revision ID: 016
Revises: 015
Create Date: 2026-03-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_settings",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column(
            "outreach_step_delays",
            JSONB(),
            nullable=False,
            server_default="[0, 3, 7]",
        ),
    )
    # Seed the single default row
    op.execute("INSERT INTO workspace_settings (id, outreach_step_delays) VALUES (1, '[0, 3, 7]')")


def downgrade():
    op.drop_table("workspace_settings")

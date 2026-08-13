"""Add is_marketing_lead and marketing_source to deals

Revision ID: 110
Revises: 109
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "110"
down_revision = "109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("is_marketing_lead", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "deals",
        sa.Column("marketing_source", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deals", "marketing_source")
    op.drop_column("deals", "is_marketing_lead")

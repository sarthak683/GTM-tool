"""Add meeting_booked_with column to deals table.

Revision ID: 102
Revises: 101
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("meeting_booked_with", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deals", "meeting_booked_with")

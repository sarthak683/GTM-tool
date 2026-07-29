"""Add meeting_booked_from column to deals table.

Revision ID: 107
Revises: 106
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("meeting_booked_from", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deals", "meeting_booked_from")

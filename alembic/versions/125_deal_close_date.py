"""Add close_date column to deals.

Revision ID: 125
Revises: 124
Create Date: 2026-08-26

Genuinely separate from close_date_est, which is the meeting date shown as
"Date of Meeting" everywhere. Reps set close_date manually after the deal
exists, so existing rows start NULL.
"""

from alembic import op
import sqlalchemy as sa


revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deals",
        sa.Column("close_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deals", "close_date")

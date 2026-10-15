"""Reason note when an account is moved to Not a Fit / Reach Out Later / DND.

Revision ID: 145
Revises: 144
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op

revision = "145"
down_revision = "144"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("account_status_reason", sa.Text(), nullable=True))
    op.add_column("account_status_history", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("account_status_history", "reason")
    op.drop_column("companies", "account_status_reason")

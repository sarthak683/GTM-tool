"""Reason note when a prospect is moved to Not the Right Prospect / Reach Out Later / DND.

Revision ID: 146
Revises: 145
Create Date: 2026-10-14
"""
import sqlalchemy as sa
from alembic import op

revision = "146"
down_revision = "145"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contacts", sa.Column("account_status_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "account_status_reason")

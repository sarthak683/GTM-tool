"""Add push-state columns to recotap_accounts (Beacon → Recotap push).

Revision ID: 089
Revises: 088
Create Date: 2026-06-09

Tracks the Beacon → Recotap push (CRM status as tags): when an account was last
pushed and the outcome. Two nullable columns; no data change.
"""
from alembic import op
import sqlalchemy as sa


revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recotap_accounts", sa.Column("pushed_at", sa.DateTime(), nullable=True))
    op.add_column("recotap_accounts", sa.Column("push_status", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("recotap_accounts", "push_status")
    op.drop_column("recotap_accounts", "pushed_at")

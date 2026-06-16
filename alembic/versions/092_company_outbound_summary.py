"""Add outbound_summary free-text notes to companies.

Revision ID: 092
Revises: 091
Create Date: 2026-06-11

Adds a nullable `outbound_summary` Text column for the quick SDR notes shown
under the status control on the Account Sourcing detail page. Free text, so no
index (never filtered or sorted on).
"""
from alembic import op
import sqlalchemy as sa


revision = "092"
down_revision = "091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("outbound_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "outbound_summary")

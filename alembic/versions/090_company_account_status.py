"""Add manual account_status to companies (account-sourcing workflow status).

Revision ID: 090
Revises: 089
Create Date: 2026-06-10

Adds a nullable `account_status` column reps set on the Account Sourcing detail
page (in_progress | cold | dnd | in_pipeline | reach_out_later). Indexed for the
list filter and the analytics breakdown. Distinct from `disposition`.
"""
from alembic import op
import sqlalchemy as sa


revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("account_status", sa.String(), nullable=True))
    op.create_index(
        "ix_companies_account_status", "companies", ["account_status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_companies_account_status", table_name="companies")
    op.drop_column("companies", "account_status")

"""Add companies.use_case.

Revision ID: 135
Revises: 134
Create Date: 2026-09-18

Same product-line categorization as Deal.use_case (migrations 133-134), but
at the account level — reps categorize an account's target use case(s) in
Account Sourcing before any deal exists, or alongside one. JSONB list of
strings from the start (unlike deals.use_case, this never went through a
single-string phase), nullable-free with an empty-array default.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "135"
down_revision = "134"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("use_case", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("companies", "use_case")

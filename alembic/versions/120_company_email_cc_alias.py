"""Add a per-account "Zippy ID" email alias to companies.

Revision ID: 120
Revises: 119
Create Date: 2026-08-19

Mirrors 032_deal_email_cc_alias.py, but for Company instead of Deal, and
WITHOUT that migration's eager backfill: companies.email_cc_alias is added
NULL-able with no data migration. Almost every company already existed before
this column was added, and the product decision here (unlike the Deal alias)
is display-only for now — nothing reads or matches on it yet
(app/tasks/email_sync.py is untouched). Backfilling ~all rows in one shot
would be wasted work for aliases that may never be looked at.

Instead, CompanyRepository.ensure_email_cc_alias() lazily generates + persists
one the first time a company is read through GET /api/v1/companies/{id} or
GET /api/v1/account-sourcing/companies/{id} (see those endpoints). The unique
index is created up front anyway so that lazy path can never collide with a
concurrent request minting the same slug — Postgres unique indexes ignore
NULLs, so rows that haven't been read yet (still NULL) don't conflict with
each other.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("email_cc_alias", sa.String(), nullable=True))
    op.create_index(
        "ix_companies_email_cc_alias", "companies", ["email_cc_alias"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_companies_email_cc_alias", table_name="companies")
    op.drop_column("companies", "email_cc_alias")

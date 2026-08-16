"""Soft-delete for companies/deals + multi-domain (alias) accounts

Revision ID: 114
Revises: 113
Create Date: 2026-08-16

Two first-class operations the 2026-08-16 audit called for:

1. Soft-delete. Hard-deleting a company cascaded into its activities and its
   deals' stage history, retroactively rewriting historical scorecards (last
   quarter's demos/closed-won changed when an account was cleaned up). Deletes
   now stamp ``deleted_at``; current-state surfaces (lists, board, pipeline
   value, prospecting) exclude the rows, while stage-history outcome metrics
   deliberately keep counting them — history is the point.

   The lower(domain) unique index becomes partial on live rows so a domain can
   be re-created after its old account was deleted (a soft-deleted row keeping
   the slot would fail imports with an IntegrityError nobody can see past).

2. Alias domains. Rebrands and multi-domain companies (ceridian.com →
   dayforce.com; iris.co.uk vs irissoftware.com) need "this account has more
   than one legitimate domain" so matching and the mismatch badge stop fighting
   reality. ``additional_domains`` is a JSONB array of normalized domains,
   written through the company-update/merge endpoints which enforce
   cross-account uniqueness at the application layer (a partial-unique GIN
   arrangement for array membership is not expressible as a btree constraint;
   the API check + the primary-domain index cover the real collision paths).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "114"
down_revision = "113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("deals", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "companies",
        sa.Column("additional_domains", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_companies_deleted_at", "companies", ["deleted_at"])
    op.create_index("ix_deals_deleted_at", "deals", ["deleted_at"])

    # Re-scope the domain dedup key to LIVE companies only.
    op.execute("DROP INDEX IF EXISTS ix_companies_lower_domain_unique")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_lower_domain_unique
        ON companies (lower(domain))
        WHERE domain IS NOT NULL AND btrim(domain) <> '' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_lower_domain_unique")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_lower_domain_unique
        ON companies (lower(domain))
        WHERE domain IS NOT NULL AND btrim(domain) <> ''
        """
    )
    op.drop_index("ix_deals_deleted_at", table_name="deals")
    op.drop_index("ix_companies_deleted_at", table_name="companies")
    op.drop_column("companies", "additional_domains")
    op.drop_column("deals", "deleted_at")
    op.drop_column("companies", "deleted_at")

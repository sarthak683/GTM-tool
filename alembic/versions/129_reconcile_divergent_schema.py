"""Reconcile the schemas produced by the former branch-specific revisions.

Revision ID: 129
Revises: 128
Create Date: 2026-09-02

Production and staging previously used the same Alembic revision numbers for
different migrations. Both live schemas are valid, but their version stamps do
not describe the same historical path. This idempotent migration establishes a
single post-merge contract: every database has the close-date and currency
columns, exact company money types, audit-history tables, and global-search
indexes regardless of which 125-128 lineage it followed.
"""

from alembic import op


revision = "129"
down_revision = "128"
branch_labels = None
depends_on = None


MONEY_COLS = (
    "arr_estimate",
    "opp_amount",
    "opp_arr",
    "opp_multiyear_license_fee",
    "opp_service_fee",
)

TRIGRAM_INDEXES = (
    ("ix_companies_domain_trgm", "companies", "domain gin_trgm_ops"),
    (
        "ix_contacts_name_trgm",
        "contacts",
        "((first_name || ' ' || last_name)) gin_trgm_ops",
    ),
    ("ix_contacts_email_trgm", "contacts", "email gin_trgm_ops"),
    ("ix_deals_name_trgm", "deals", "name gin_trgm_ops"),
    ("ix_meetings_title_trgm", "meetings", "title gin_trgm_ops"),
    ("ix_tasks_title_trgm", "tasks", "title gin_trgm_ops"),
    (
        "ix_sales_resources_title_trgm",
        "sales_resources",
        "title gin_trgm_ops",
    ),
)


def upgrade() -> None:
    op.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS close_date date")
    op.execute(
        "ALTER TABLE deals ADD COLUMN IF NOT EXISTS "
        "currency_code varchar(3) DEFAULT 'USD'"
    )
    op.execute(
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS "
        "arr_estimate_currency varchar(3) DEFAULT 'USD'"
    )

    for col in MONEY_COLS:
        op.execute(
            f'ALTER TABLE companies ALTER COLUMN "{col}" '
            f'TYPE numeric(15,2) USING "{col}"::numeric(15,2)'
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_status_history (
            id uuid PRIMARY KEY,
            company_id uuid NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            from_status varchar NULL,
            to_status varchar NULL,
            changed_by_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,
            changed_at timestamp without time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_status_history_company_id "
        "ON account_status_history (company_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_status_history_from_status "
        "ON account_status_history (from_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_status_history_to_status "
        "ON account_status_history (to_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_account_status_history_changed_at "
        "ON account_status_history (changed_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_disposition_history (
            id uuid PRIMARY KEY,
            contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            from_disposition varchar NULL,
            to_disposition varchar NULL,
            changed_by_id uuid NULL REFERENCES users(id) ON DELETE SET NULL,
            changed_at timestamp without time zone NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contact_disposition_history_contact_id "
        "ON contact_disposition_history (contact_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contact_disposition_history_from_disposition "
        "ON contact_disposition_history (from_disposition)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contact_disposition_history_to_disposition "
        "ON contact_disposition_history (to_disposition)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contact_disposition_history_changed_at "
        "ON contact_disposition_history (changed_at)"
    )

    with op.get_context().autocommit_block():
        for index_name, table_name, expression in TRIGRAM_INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
                f"ON {table_name} USING gin ({expression})"
            )


def downgrade() -> None:
    # Revision 129 reconciles objects that may have been created by several
    # historical lineages. Dropping them here could destroy objects owned by a
    # prior migration, so downgrade intentionally preserves the unified schema.
    pass

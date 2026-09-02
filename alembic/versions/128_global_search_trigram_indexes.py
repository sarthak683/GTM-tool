"""Add trigram indexes for global search expressions.

Revision ID: 128
Revises: 127
Create Date: 2026-08-24

This was formerly revision 127 on one branch. IF NOT EXISTS keeps it safe for
databases that already applied that lineage.
"""

from alembic import op


revision = "128"
down_revision = "127"
branch_labels = None
depends_on = None


INDEXES = (
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
    with op.get_context().autocommit_block():
        for index_name, table_name, expression in INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
                f"ON {table_name} USING gin ({expression})"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, _, _ in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}")

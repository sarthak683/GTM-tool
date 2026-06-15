"""Add composite indexes on activities for hot list/board queries.

Revision ID: 084
Revises: 083
Create Date: 2026-06-08

Two pure-additive indexes that target the two hottest authenticated read paths:

  1) ix_activities_contact_type_created (contact_id, type, created_at DESC)
     — serves the prospects list, which attaches three correlated scalar
       subqueries per row filtering Activity by (contact_id AND type).
       Previously only single-column ix_activities_contact_id / _type existed.

  2) ix_activities_deal_created (deal_id, created_at DESC)
     — serves the deal kanban board engagement maps and the deal-detail
       activity timeline, both of which scan a deal's activities ordered by
       recency.

Idempotent (CREATE INDEX IF NOT EXISTS) per the repo's index-migration rule.
No data is changed. NOTE for very large activities tables: building these
non-concurrently briefly locks writes; if that matters in prod, build them
out-of-band with CREATE INDEX CONCURRENTLY and let this migration no-op via
IF NOT EXISTS.
"""
from alembic import op


revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activities_contact_type_created "
        "ON activities (contact_id, type, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activities_deal_created "
        "ON activities (deal_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activities_deal_created")
    op.execute("DROP INDEX IF EXISTS ix_activities_contact_type_created")

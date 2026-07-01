"""Indexes for hot analytics/list query columns.

Revision ID: 093
Revises: 092
Create Date: 2026-06-12

Two columns are filtered constantly but had no index:

- meetings.scheduled_at — every analytics window query, the meetings list's
  temporal filters, the drilldown sort, and the pre-meeting-brief due window
  all range-filter on it. Volume grows steadily via tl;dv + calendar sync.
- activities(created_by_id, created_at) — the "calls logged today" tile and
  rep-scoped activity lists filter by creator (optionally with a time range);
  only created_at alone was indexed.

IF NOT EXISTS keeps the migration idempotent, matching repo convention.
"""
from alembic import op


revision = "093"
down_revision = "092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_meetings_scheduled_at "
        "ON meetings (scheduled_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activities_created_by_created "
        "ON activities (created_by_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activities_created_by_created")
    op.execute("DROP INDEX IF EXISTS ix_meetings_scheduled_at")

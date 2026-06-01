"""Performance indexes for hot reporting/dedup queries.

Revision ID: 078
Revises: 077
Create Date: 2026-05-31

Every scorecard/leaderboard/calls-today/activity-list query filters on
`activities.type`, and `notifications.dedup_key` is looked up on every webhook
re-delivery — both were unindexed sequential scans. (activities.created_at and
tasks.entity_id were *already* indexed by migrations 001 and 031 respectively;
the models just never declared `index=True`, so this revision leaves them be.)

Idempotent CREATE/DROP so it tolerates any out-of-band index drift across envs.
"""

from alembic import op


revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_activities_type ON activities (type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_dedup_key ON notifications (dedup_key)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_dedup_key")
    op.execute("DROP INDEX IF EXISTS ix_activities_type")

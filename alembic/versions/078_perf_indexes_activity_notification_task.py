"""Performance indexes for hot reporting/dedup/scoping queries.

Revision ID: 078
Revises: 077
Create Date: 2026-05-31

The activities table is the fastest-growing one and every scorecard, leaderboard,
calls-today, and activity-list query filters on `type` and/or `created_at` — both
were unindexed (sequential scans). `notifications.dedup_key` is looked up on every
webhook re-delivery and notification create. `tasks.entity_id` backs the newly
entity-scoped open-task assignment backfill. Index names follow SQLModel's
`ix_<table>_<column>` convention so the ORM `index=True` declarations and the DB agree.
"""

from alembic import op


revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_activities_type", "activities", ["type"])
    op.create_index("ix_activities_created_at", "activities", ["created_at"])
    op.create_index("ix_notifications_dedup_key", "notifications", ["dedup_key"])
    op.create_index("ix_tasks_entity_id", "tasks", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_entity_id", table_name="tasks")
    op.drop_index("ix_notifications_dedup_key", table_name="notifications")
    op.drop_index("ix_activities_created_at", table_name="activities")
    op.drop_index("ix_activities_type", table_name="activities")

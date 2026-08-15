"""DB backstops for app-level dedup, missing FKs, and cached-counter repair

Revision ID: 113
Revises: 112
Create Date: 2026-08-15

A production data audit (2026-08-15) found every sync dedup in the app is a
Python check-then-insert with no database constraint behind it, running on two
API replicas and a two-process worker. The audit also found the drift that
pattern produces: model-declared FKs that were never created (migrations 075/
076 added bare UUID columns), stakeholder_count wrong on 151 of 689 deals, 500
tasks carrying the deal-vocabulary priority "normal" that the task model does
not allow, and one pair of duplicate open system tasks.

This migration adds the backstops and repairs the data:

1. FKs on call_recordings.deal_id / deleted_by_id (ON DELETE SET NULL, matching
   the soft-delete design — a deleted deal detaches its recordings, it does not
   destroy them).
2. A partial unique index on meetings(external_source, external_source_id):
   calendar sync fans out per attendee, so a shared customer meeting is
   upserted by N users' syncs concurrently, and tl;dv rows race between the
   webhook (API pod) and the 5-minute poller (worker). Deals and activities
   already have their uq_*_external_source_id equivalents; meetings was the
   gap. Prod has zero duplicates today, so this creates clean.
3. A partial unique index on open system tasks: the task emitter's per-key
   dedup is app-level only. Prod's one existing duplicate pair is dismissed
   (not deleted — task_comments FK and audit history stay intact).
4. Data repair: tasks.priority 'normal' -> 'medium'; stakeholder_count
   recomputed from deal_contacts.
"""

import sqlalchemy as sa
from alembic import op

revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. FKs the models have declared since 075/076 but never existed ──────
    op.create_foreign_key(
        "fk_call_recordings_deal_id_deals",
        "call_recordings",
        "deals",
        ["deal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_call_recordings_deleted_by_id_users",
        "call_recordings",
        "users",
        ["deleted_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 2. Unique backstop for externally-sourced meetings ───────────────────
    op.create_index(
        "uq_meetings_external_source_id",
        "meetings",
        ["external_source", "external_source_id"],
        unique=True,
        postgresql_where=sa.text("external_source_id IS NOT NULL"),
    )

    # ── 3. Open system tasks: dismiss existing dups, then enforce ────────────
    # Keep the oldest task of each duplicate group (it is the one reps may have
    # already seen/commented on); dismiss the newer copies.
    op.execute(
        """
        UPDATE tasks t SET status = 'dismissed', updated_at = NOW()
        FROM tasks keeper
        WHERE t.status = 'open' AND keeper.status = 'open'
          AND t.system_key IS NOT NULL
          AND t.entity_type = keeper.entity_type
          AND t.entity_id = keeper.entity_id
          AND t.task_type = keeper.task_type
          AND t.system_key = keeper.system_key
          AND (t.created_at > keeper.created_at
               OR (t.created_at = keeper.created_at AND t.id > keeper.id))
        """
    )
    op.create_index(
        "uq_tasks_open_system_key",
        "tasks",
        ["entity_type", "entity_id", "task_type", "system_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND system_key IS NOT NULL"),
    )

    # ── 4a. Off-enum priority: 'normal' is the DEAL vocabulary ───────────────
    # (TASK_PRIORITIES is low/medium/high; 500 prod rows carried 'normal' and
    # silently fell out of every priority filter.)
    op.execute("UPDATE tasks SET priority = 'medium' WHERE priority = 'normal'")

    # ── 4b. stakeholder_count: recompute from deal_contacts ──────────────────
    # Bulk attach paths (ClickUp import, deal linker) never maintained the
    # cached counter the manual endpoint maintains; 22% of deals were wrong,
    # including 0-vs-97. Idempotent: re-running converges to the same values.
    op.execute(
        """
        UPDATE deals d SET stakeholder_count = c.n
        FROM (SELECT deal_id, COUNT(*) AS n FROM deal_contacts GROUP BY deal_id) c
        WHERE c.deal_id = d.id AND d.stakeholder_count IS DISTINCT FROM c.n
        """
    )
    op.execute(
        """
        UPDATE deals SET stakeholder_count = 0
        WHERE stakeholder_count <> 0
          AND NOT EXISTS (SELECT 1 FROM deal_contacts dc WHERE dc.deal_id = deals.id)
        """
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_open_system_key", table_name="tasks")
    op.drop_index("uq_meetings_external_source_id", table_name="meetings")
    op.drop_constraint("fk_call_recordings_deleted_by_id_users", "call_recordings", type_="foreignkey")
    op.drop_constraint("fk_call_recordings_deal_id_deals", "call_recordings", type_="foreignkey")
    # Data repairs (4a/4b) are intentionally not reversed: the old values were
    # wrong, and 'normal' priorities are indistinguishable from real 'medium'.

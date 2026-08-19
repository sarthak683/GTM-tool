"""Give every live deal an initial stage-history row.

Revision ID: 122
Revises: 121
Create Date: 2026-08-20

Demo-funnel scorecards (demos scheduled/done/converted, win rate, cycle time)
read DealStageHistory, not Deal.stage. A deal with no history row is therefore
invisible to all of them no matter what stage it sits in.

Prod had exactly one: the Bamboo Rose deal, created 2026-08-14 straight into
demo_scheduled through the meeting-booked notification flow. That path did not
write an initial-stage row until 417257c landed on 2026-08-15, one day later, so
this deal fell in the gap. Its SDR (Mahesh) booked the demo and it never showed
up in his numbers.

The code path is already fixed; this only repairs the orphan it left behind.
Written as a general predicate rather than a hard-coded id so it also heals any
deal a future creation path forgets, and it is a no-op once every deal has one.

changed_at uses stage_entered_at, falling back to created_at, so the row lands
on the day the deal actually entered the stage and the demo counts in the right
week rather than on the migration date.
"""

from __future__ import annotations

from alembic import op


revision = "122"
down_revision = "121"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO deal_stage_history (id, deal_id, from_stage, to_stage, changed_at, source)
        SELECT gen_random_uuid(), d.id, NULL, d.stage,
               COALESCE(d.stage_entered_at, d.created_at), 'backfill_122'
          FROM deals d
         WHERE d.deleted_at IS NULL
           AND d.stage IS NOT NULL AND d.stage <> ''
           AND NOT EXISTS (SELECT 1 FROM deal_stage_history h WHERE h.deal_id = d.id)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM deal_stage_history WHERE source = 'backfill_122'")

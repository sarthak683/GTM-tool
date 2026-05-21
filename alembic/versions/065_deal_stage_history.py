"""Create deal_stage_history table and backfill from activities + current stage.

Revision ID: 065
Revises: 051
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa


revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deal_stage_history",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deal_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("deals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_stage", sa.String(), nullable=True),
        sa.Column("to_stage", sa.String(), nullable=False),
        sa.Column("changed_by_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
    )
    op.create_index("ix_deal_stage_history_deal_id", "deal_stage_history", ["deal_id"])
    op.create_index("ix_deal_stage_history_to_stage", "deal_stage_history", ["to_stage"])
    op.create_index("ix_deal_stage_history_changed_at", "deal_stage_history", ["changed_at"])
    op.create_index("ix_deal_stage_history_deal_changed", "deal_stage_history", ["deal_id", "changed_at"])

    # ── Backfill ────────────────────────────────────────────────────────────
    # 1) One row per existing deal representing its current-stage entry.
    #    Uses stage_entered_at when available, else created_at.
    op.execute(
        """
        INSERT INTO deal_stage_history (id, deal_id, from_stage, to_stage, changed_at, source)
        SELECT gen_random_uuid(), id, NULL, stage,
               COALESCE(stage_entered_at, created_at, now()), 'backfill_current'
        FROM deals
        """
    )

    # 2) Parse existing Activity(type='stage_change') rows to recover past
    #    transitions. Content format written by deals.py is:
    #    "Stage moved from <from> to <to>".  Best-effort — if parse fails,
    #    the row is skipped silently (no ERROR in migration).
    op.execute(
        """
        INSERT INTO deal_stage_history (id, deal_id, from_stage, to_stage, changed_at, source)
        SELECT gen_random_uuid(),
               a.deal_id,
               NULLIF(split_part(substring(a.content from 'from ([a-z_]+) to'), ' ', 1), ''),
               NULLIF(substring(a.content from 'to ([a-z_]+)'), ''),
               a.created_at,
               'backfill_activity'
        FROM activities a
        WHERE a.type = 'stage_change'
          AND a.deal_id IS NOT NULL
          AND a.content ~ 'from [a-z_]+ to [a-z_]+'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_deal_stage_history_deal_changed", table_name="deal_stage_history")
    op.drop_index("ix_deal_stage_history_changed_at", table_name="deal_stage_history")
    op.drop_index("ix_deal_stage_history_to_stage", table_name="deal_stage_history")
    op.drop_index("ix_deal_stage_history_deal_id", table_name="deal_stage_history")
    op.drop_table("deal_stage_history")

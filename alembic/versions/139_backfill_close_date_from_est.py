"""Backfill deals.close_date from close_date_est where close_date is unset.

Revision ID: 139
Revises: 138
Create Date: 2026-09-17

close_date is a newer field, never auto-populated — reps set it manually via
the drawer's "Close Date" field, added after close_date_est ("Date of
Meeting") already existed on every deal. Before this session, every place
that showed a deal's "close date" (Pipeline board card, table column, close-
date filters, CSV export) actually read close_date_est, so a rep who never
touched the new field still saw a date there. Once the board/table were
switched over to read close_date only, deals with no close_date went blank
even though a date was already showing a moment ago from close_date_est.

One-time catch-up: seed close_date with the existing close_date_est value
for every deal that has no close_date of its own yet, so what was already
visible keeps showing. Never overwrites a close_date a rep already set by
hand. Going forward, close_date and close_date_est are edited independently
(this migration does not touch close_date_est).
"""

from alembic import op


revision = "139"
down_revision = "138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE deals
        SET close_date = close_date_est
        WHERE close_date IS NULL
          AND close_date_est IS NOT NULL
        """
    )


def downgrade() -> None:
    # Non-destructive data enrichment — no reliable way to tell which rows
    # this backfill set apart from a close_date a rep set by hand that
    # happens to match close_date_est, so there is nothing safe to revert.
    pass
